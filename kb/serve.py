"""Knowledge Base Server - HTTP API for the local personal knowledge base."""
from __future__ import annotations

import http.server
import io
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from pathlib import Path

import storage
from app_config import load_recent_workspaces
from workspace import (
    create_workspace,
    destroy_workspace,
    get_active_workspace,
    open_workspace,
    require_active_workspace,
)
from storage import (
    STATIC_INDEX_HTML,
    add_item_to_workspace,
    delete_article,
    delete_article_attachment,
    delete_article_history,
    delete_chat_session_file,
    delete_note,
    delete_notebook,
    delete_workspace,
    ensure_directories,
    find_note_block,
    get_all_articles,
    get_all_notes,
    get_article,
    get_data_root_info,
    get_article_note_count,
    get_conn,
    get_note_blocks,
    get_note_backlinks,
    get_notes_for_article,
    inject_block_anchors,
    sync_note_links,
    list_article_attachments,
    list_article_history,
    list_chat_sessions,
    list_conversion_history,
    list_notebooks,
    list_workspaces,
    load_chat_session_file,
    load_local_env,
    load_translation_state,
    public_local_env,
    record_conversion,
    remove_item_from_workspace,
    save_chat_index,
    save_chat_session_file,
    save_translation_state,
    set_data_root,
    sync_note_blocks,
    update_article_fields,
    upsert_article,
    upsert_article_attachment,
    upsert_note,
    upsert_notebook,
    upsert_workspace,
    list_article_folders,
    create_article_folder,
    update_article_folder,
    delete_article_folder,
    move_article_to_folder,
    move_articles_to_folder,
)
from llm_config import (
    call_chat_completion,
    public_llm_config,
    resolve_llm_settings,
    save_llm_config_from_public,
)
from database import (
    add_column,
    add_row,
    add_view,
    batch_delete_rows,
    create_database,
    database_attachments_dir,
    delete_column,
    delete_database,
    delete_row,
    delete_view,
    export_database_csv,
    import_database_csv,
    list_database_history,
    list_databases,
    load_database,
    public_field_types,
    render_database,
    restore_database_history,
    save_database_attachment,
    import_legacy_databases,
    reindex_all_databases,
    search_databases,
    update_column,
    update_database_meta,
    update_row,
    update_view,
    validate_database_id,
)
from updater import check_for_update, apply_update, is_installed_build
from version import VERSION

PORT = 8765
INVALID_ARTICLE_CHARS = set("/\\:*?\"<>|'")
ALLOWED_SAVE_SUFFIXES = {
    ".md", ".markdown", ".json", ".txt", ".log", ".csv", ".tsv",
    ".bib", ".tex", ".py", ".js", ".ts", ".tsx", ".jsx", ".css",
    ".rs", ".go", ".java", ".c", ".cpp", ".h", ".hpp", ".r", ".m",
    ".sh", ".yaml", ".yml", ".toml", ".ini", ".xml", ".sql", ".ipynb",
}
TEXT_FILE_MAX_BYTES = 5 * 1024 * 1024
_LOG_LOCK = threading.Lock()
_CHAT_LOCK = threading.Lock()

# Ensure the runtime layout exists on first import.
ensure_directories()
load_local_env()
try:
    import_legacy_databases()
    reindex_all_databases()
except Exception:
    pass


# ---------------------------------------------------------------------------
# Path safety helpers
# ---------------------------------------------------------------------------


def _is_inside(path: Path, base: Path) -> bool:
    try:
        return path == base or path.is_relative_to(base)
    except AttributeError:
        try:
            return os.path.commonpath([str(path), str(base)]) == str(base)
        except ValueError:
            return False


def _workspace_static_root() -> Path:
    ws = get_active_workspace()
    if ws is not None:
        return ws.root.resolve()
    return storage.DATA_ROOT.resolve()


def _resolve_workspace_static_file(relative: str) -> Path | None:
    """Map a workspace-relative URL path to a file under the active workspace root."""
    rel = relative.replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        return None
    ws = get_active_workspace()
    if ws is not None and ws.is_readonly_path(rel):
        try:
            target = ws.resolve(rel)
        except ValueError:
            return None
        if target.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}:
            return None
        return target if target.is_file() else None
    root = _workspace_static_root()
    target = (root / rel).resolve()
    if _is_inside(target, root) and target.is_file():
        try:
            first = target.relative_to(root).parts[0]
        except ValueError:
            return None
        if first == ".kbase":
            return None
        return target
    if rel.startswith("notes/"):
        note_file = (storage.NOTES_DIR / rel[len("notes/"):]).resolve()
        if note_file.is_file():
            return note_file
    return None


def _database_path_parts(request_path: str) -> tuple[str, str | None, str | None]:
    parts = urllib.parse.urlsplit(request_path).path.strip("/").split("/")
    if len(parts) < 3 or parts[0] != "api" or parts[1] != "databases":
        return "", None, None
    db_id = urllib.parse.unquote(parts[2])
    sub = parts[3] if len(parts) > 3 else None
    sub_id = urllib.parse.unquote(parts[4]) if len(parts) > 4 else None
    return db_id, sub, sub_id


def sanitize_article_id(value: str) -> str:
    base = Path(value or "upload").stem or "upload"
    article_id = re.sub(r"[\s.]+", "_", base.strip())
    article_id = "".join(
        "_" if ch in INVALID_ARTICLE_CHARS or ord(ch) < 32 else ch
        for ch in article_id
    )
    article_id = re.sub(r"_+", "_", article_id).strip(" ._")
    return article_id or f"upload_{int(time.time())}"


def validate_article_id(article_id: str) -> str:
    article_id = str(article_id or "").strip()
    if not article_id:
        raise ValueError("Article id is required")
    path = Path(article_id)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or any(ch in INVALID_ARTICLE_CHARS or ord(ch) < 32 for ch in article_id)
        or article_id in {".", ".."}
    ):
        raise ValueError("Invalid article id")
    return article_id


def article_dir_for(article_id: str) -> Path:
    article_id = validate_article_id(article_id)
    base = storage.ARTICLES_DIR.resolve()
    target = (storage.ARTICLES_DIR / article_id).resolve()
    if target == base or not _is_inside(target, base):
        raise ValueError("Invalid article path")
    return target


def article_id_from_request_path(request_path: str) -> str:
    path = urllib.parse.urlsplit(request_path).path.rstrip("/")
    return validate_article_id(urllib.parse.unquote(path.rsplit("/", 1)[-1]))


def resolve_save_path(filepath: str) -> Path:
    """Allow saving text-like files under the active workspace (or DATA_ROOT)."""
    rel = Path(str(filepath or "").replace("\\", "/"))
    if str(rel).replace("\\", "/").startswith("@sources/"):
        raise ValueError("External sources are read-only; copy the file into managed storage first")
    if (
        rel.is_absolute()
        or rel.drive
        or rel.anchor
        or any(part == ".." for part in rel.parts)
        or not rel.parts
    ):
        raise ValueError("Invalid save path")
    if rel.suffix.lower() not in ALLOWED_SAVE_SUFFIXES:
        raise ValueError("Saving is only allowed for text-like files")

    root = _workspace_static_root()
    target = (root / rel).resolve()
    if not _is_inside(target, root) or target == root:
        raise ValueError("Saving is only allowed inside the workspace")
    try:
        first = target.relative_to(root).parts[0]
    except ValueError as exc:
        raise ValueError("Invalid save path") from exc
    if first == ".kbase":
        raise ValueError("Cannot write into .kbase")
    return target


def resolve_workspace_rel_path(rel_path: str, *, must_exist: bool = False) -> Path:
    """Resolve a workspace-relative path for read/write (not under .kbase)."""
    rel = str(rel_path or "").replace("\\", "/").strip("/")
    if not rel or ".." in rel.split("/"):
        raise ValueError("Invalid path")
    ws = get_active_workspace()
    if ws is not None and ws.is_readonly_path(rel):
        target = ws.resolve(rel)
        if must_exist and not target.exists():
            raise FileNotFoundError(rel)
        return target
    root = _workspace_static_root()
    target = (root / rel).resolve()
    if not _is_inside(target, root):
        raise ValueError("Path escapes workspace")
    try:
        parts = target.relative_to(root).parts
    except ValueError as exc:
        raise ValueError("Invalid path") from exc
    if parts and parts[0] == ".kbase":
        raise ValueError("Cannot access .kbase")
    if must_exist and not target.exists():
        raise FileNotFoundError(rel)
    return target


def require_managed_workspace_path(rel_path: str) -> None:
    """Reject mutations against linked external folders."""
    ws = get_active_workspace()
    if ws is not None and ws.is_readonly_path(rel_path):
        raise ValueError("External sources are read-only; copy files into managed storage first")


def note_id_for_workspace_path(rel_path: str) -> str:
    """Stable SQLite note id for an arbitrary workspace markdown path."""
    import hashlib

    norm = str(rel_path or "").replace("\\", "/").strip("/")
    digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:20]
    return f"ws_{digest}"


def workspace_path_for_note_id(note_id: str) -> str | None:
    """Return folder-stored path for ws_* notes, else None."""
    note = next((n for n in get_all_notes() if n.get("id") == note_id), None)
    if not note:
        return None
    folder = str(note.get("folder") or "")
    if folder.startswith("path:"):
        return folder[5:]
    return None


def resolve_note_markdown_path(note_id: str) -> Path:
    """Resolve on-disk markdown for a note id (legacy notes/ or workspace path)."""
    ws_path = workspace_path_for_note_id(note_id)
    if ws_path:
        return resolve_workspace_rel_path(ws_path, must_exist=False)
    return note_file_for(note_id)


# ---------------------------------------------------------------------------
# Sync filesystem -> SQLite
# ---------------------------------------------------------------------------


def _read_json_file(path: Path) -> dict:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _is_note_ancestor(note_id: str, candidate_parent_id: str) -> bool:
    """True if candidate_parent_id is note_id or a descendant of note_id."""
    if not note_id or not candidate_parent_id:
        return False
    if candidate_parent_id == note_id:
        return True
    notes = {n["id"]: n for n in get_all_notes()}
    cur = candidate_parent_id
    seen: set[str] = set()
    while cur:
        if cur == note_id:
            return True
        if cur in seen:
            break
        seen.add(cur)
        parent = notes.get(cur, {}).get("parent_id")
        cur = parent or None
    return False


def _is_folder_ancestor(folder_id: str, candidate_parent_id: str) -> bool:
    """True if candidate_parent_id is folder_id or a descendant of folder_id."""
    if not folder_id or not candidate_parent_id:
        return False
    if candidate_parent_id == folder_id:
        return True
    folders = {f["id"]: f for f in list_article_folders()}
    cur = candidate_parent_id
    seen: set[str] = set()
    while cur:
        if cur == folder_id:
            return True
        if cur in seen:
            break
        seen.add(cur)
        parent = folders.get(cur, {}).get("parent_id")
        cur = parent or None
    return False


def _preferred_markdown_file(article_id: str) -> Path | None:
    folder = article_dir_for(article_id)
    for suffix in ("_calibrated.md", ".md", "_translated.md"):
        candidate = folder / f"{article_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def _versioned_markdown_files(article_id: str) -> list[Path]:
    folder = article_dir_for(article_id)
    engines = {"pymupdf", "marker", "docmind", "docparser"}
    files: list[Path] = []
    for f in folder.iterdir():
        if not f.is_file() or not f.name.startswith(f"{article_id}_") or not f.name.endswith(".md"):
            continue
        engine = f.name[len(article_id) + 1 : -3]
        if engine in engines:
            files.append(f)
    return files


def scan_articles() -> list[dict]:
    """Reconcile the filesystem with SQLite, returning the full article list."""
    from workspace_paths import adjacent_parsed_md_path, adjacent_zh_md_path

    with get_conn() as conn:
        existing = {
            row["id"]: dict(row)
            for row in conn.execute("SELECT * FROM articles").fetchall()
        }

        for folder in sorted(storage.ARTICLES_DIR.iterdir()) if storage.ARTICLES_DIR.exists() else []:
            if not folder.is_dir():
                continue
            aid = folder.name
            article = existing.pop(aid, None)

            pdf_candidates = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
            pdf_path = pdf_candidates[0] if pdf_candidates else folder / "original.pdf"
            parsed_exists = adjacent_parsed_md_path(folder, pdf_path, aid).exists()
            trans_exists = (
                adjacent_zh_md_path(folder, pdf_path, aid).exists()
                or (folder / f"{aid}_translated.md").exists()
            )
            summary_exists = (folder / f"{aid}_summary.md").exists()
            pdf_exists = (folder / "original.pdf").exists() or bool(pdf_candidates)
            md_exists = parsed_exists or (folder / f"{aid}.md").exists()

            meta = _read_json_file(folder / f"{aid}_meta.json")
            info = _read_json_file(folder / f"{aid}_info.json")
            original_files = [
                p for p in folder.iterdir()
                if p.is_file() and p.stem == "original" and p.suffix.lower() != ".md"
            ]
            file_available = bool(original_files)
            source_filename = (
                meta.get("source_filename")
                or (original_files[0].name if original_files else "")
            )

            if article is None:
                article = {
                    "id": aid,
                    "title": meta.get("title") or aid,
                    "author": "",
                    "authors": [],
                    "pages": len(meta.get("page_stats") or []),
                    "date_added": time.strftime("%Y-%m-%d %H:%M"),
                    "category": "",
                    "tags": [],
                    "translated": trans_exists,
                    "summarized": summary_exists,
                    "pdf_available": pdf_exists,
                    "md_available": md_exists,
                    "file_available": file_available,
                    "source_filename": source_filename,
                    "kind": meta.get("document_kind") or ("paper" if pdf_exists else "file"),
                    "doi": "",
                    "year": "",
                    "venue": "",
                    "abstract": "",
                    "metadata_extracted": False,
                    "metadata_source": "",
                    "metadata_extracted_at": "",
                    "parser": "",
                    "preparse_error": "",
                    "converting": False,
                }
            else:
                article["translated"] = trans_exists
                article["summarized"] = summary_exists
                article["pdf_available"] = pdf_exists
                article["md_available"] = md_exists
                article["file_available"] = file_available
                article["has_old_translation"] = (folder / f"{aid}_translated_old.md").exists()

            if meta.get("title") and (not article.get("title") or article.get("title") == aid):
                article["title"] = meta["title"]
            if meta.get("document_kind"):
                article["kind"] = meta["document_kind"]
            elif meta.get("source") == "pymupdf" and not article.get("kind"):
                article["kind"] = "paper"
            if not article.get("source_filename") and source_filename:
                article["source_filename"] = source_filename

            if isinstance(info, dict) and info:
                article["metadata_extracted"] = True
                if info.get("extracted_at"):
                    article["metadata_extracted_at"] = info["extracted_at"]
                if info.get("extraction_reason"):
                    article["metadata_source"] = info["extraction_reason"]
                for key in ("title", "author", "doi", "year", "venue", "abstract", "category"):
                    if info.get(key):
                        article[key] = info[key]
                if info.get("authors"):
                    article["authors"] = info["authors"]
                if info.get("document_kind"):
                    article["kind"] = info["document_kind"]
                tags = info.get("tags") or info.get("keywords") or []
                if tags:
                    article["tags"] = tags

            # Page count from meta
            if meta.get("page_stats") and not article.get("pages"):
                article["pages"] = len(meta["page_stats"])

            upsert_article(article)

        # Any leftover rows whose article folder is gone: drop them.
        for aid in list(existing.keys()):
            if not (storage.ARTICLES_DIR / aid).exists():
                delete_article(aid)

    return get_all_articles()


# ---------------------------------------------------------------------------
# Conversion / calibration / translation state
# ---------------------------------------------------------------------------


_conv_status: dict[str, dict] = {}
_conv_lock = threading.Lock()
_translation_threads: dict[str, threading.Thread] = {}
_translation_lock = threading.Lock()
_metadata_threads: dict[str, threading.Thread] = {}
_metadata_lock = threading.Lock()


def set_conv_status(article_id: str, task: str, status: str, message: str = "", log: str = "") -> None:
    with _conv_lock:
        bucket = _conv_status.setdefault(article_id, {})
        bucket[task] = {
            "status": status,
            "message": message,
            "log": log,
            "updated": time.time(),
        }


def get_conv_status(article_id: str, task: str) -> dict | None:
    with _conv_lock:
        bucket = _conv_status.get(article_id, {})
        return dict(bucket.get(task, {})) or None


def _log_path(article_id: str, task: str) -> Path:
    folder = storage.KBASE_DIR / "logs" / article_id
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{task}.log"


def _read_log(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _write_log(path: Path, msg: str) -> None:
    with _LOG_LOCK:
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except OSError:
            pass


def run_conversion(pdf_path: str, article_id: str, engine_name: str = "marker", docparser_engine: str | None = None) -> None:
    log_path = _log_path(article_id, "conversion")

    def log(msg: str) -> None:
        _write_log(log_path, msg)
        with _conv_lock:
            entry = _conv_status.setdefault(article_id, {}).setdefault("conversion", {
                "status": "running", "message": "", "log": "", "updated": time.time()
            })
            entry["log"] = (entry.get("log", "") + msg + "\n")[-200000:]
            entry["updated"] = time.time()

    try:
        log(f"=== Conversion started at {time.strftime('%H:%M:%S')} ===")
        log(f"Engine: {engine_name}")
        log(f"PDF: {pdf_path}")
        set_conv_status(article_id, "conversion", "running", f"启动 {engine_name} 引擎...")

        from engines import get_engine
        engine = get_engine(engine_name)

        if engine_name == "docparser" and docparser_engine:
            success = engine.run(pdf_path, article_id, log_callback=log, engine=docparser_engine)
        else:
            success = engine.run(pdf_path, article_id, log_callback=log)

        if not success:
            log("=== Conversion FAILED ===")
            record_conversion(article_id, engine_name, "fail")
            set_conv_status(article_id, "conversion", "error", "解析失败，查看日志了解详情")
            return

        # Snapshot versioned copy (prefer workspace-adjacent .parsed.md)
        from workspace_paths import (
            adjacent_parsed_md_path,
            adjacent_zh_md_path,
            legacy_md_path,
        )

        article_dir = article_dir_for(article_id)
        pdf = Path(pdf_path)
        adjacent = adjacent_parsed_md_path(article_dir, pdf, article_id)
        legacy = legacy_md_path(article_dir, article_id)
        md_file = adjacent if adjacent.exists() else legacy
        versioned = article_dir / f"{article_id}_{engine_name}.md"
        if md_file.exists():
            try:
                shutil.copy2(md_file, versioned)
            except OSError:
                pass
            record_article_history_safe(article_id, engine_name, versioned)

        # Drop outdated derived files. Translated goes to *_translated_old.md.
        zh_file = adjacent_zh_md_path(article_dir, pdf, article_id)
        for derived in (
            article_dir / f"{article_id}_calibrated.md",
            article_dir / f"{article_id}_translated.md",
            article_dir / f"{article_id}_summary.md",
            zh_file,
        ):
            if not derived.exists():
                continue
            try:
                if derived.name.endswith("_translated.md"):
                    shutil.move(str(derived), str(article_dir / f"{article_id}_translated_old.md"))
                elif derived == zh_file:
                    shutil.move(str(derived), str(article_dir / f"{article_id}_translated_old.zh.md"))
                else:
                    derived.unlink()
            except OSError as exc:
                log(f"Failed to handle {derived}: {exc}")

        record_conversion(article_id, engine_name, "success")
        set_conv_status(article_id, "conversion", "done", "解析完成")
        update_article_fields(article_id, {
            "md_available": adjacent.exists() or legacy.exists(),
            "parser": engine_name,
        })
        try:
            from derivations import sync_legacy_parse

            result = sync_legacy_parse(article_id, md_file, engine_name)
            if result:
                log(f"Workspace: parsed → {result.get('path')}")
        except Exception as exc:  # noqa: BLE001
            log(f"Workspace derivation skipped: {exc}")
        scan_articles()
        _start_extract_info(article_id, reason=f"parsed:{engine_name}", allow_parallel=True)
    except Exception as exc:  # noqa: BLE001
        import traceback
        log(f"FATAL ERROR: {exc}")
        log(traceback.format_exc())
        set_conv_status(article_id, "conversion", "error", f"系统错误: {exc}")


def record_article_history_safe(article_id: str, engine: str, file_path: Path) -> None:
    try:
        record_article_history(article_id, engine, file_path)
    except Exception:
        pass


def _run_calibrate(article_id: str, log_callback) -> None:
    log_path = _log_path(article_id, "calibrate")

    def log(msg: str) -> None:
        _write_log(log_path, msg)
        log_callback(msg)

    try:
        from calibrate import calibrate
        ok = calibrate(article_id, log_callback=log)
        if ok:
            set_conv_status(article_id, "calibration", "done", "校准完成")
            _start_extract_info(article_id, reason="calibrated", allow_parallel=True)
        else:
            set_conv_status(article_id, "calibration", "error", "校准失败")
    except Exception as exc:  # noqa: BLE001
        import traceback
        log(f"Calibrate error: {exc}\n{traceback.format_exc()}")
        set_conv_status(article_id, "calibration", "error", str(exc))


def _translation_state(article_id: str) -> dict:
    state = load_translation_state(article_id)
    if state:
        with _translation_lock:
            thread = _translation_threads.get(article_id)
            if thread and thread.is_alive():
                state["status"] = "running"
                state["message"] = state.get("message") or "后台翻译中"
        return state
    art_dir = article_dir_for(article_id)
    if (art_dir / f"{article_id}_translated.md").exists():
        return {"status": "done", "message": "翻译完成", "percent": 100, "current": 0, "total": 0}
    return {"status": "idle", "message": "", "percent": 0, "current": 0, "total": 0}


def _run_translate(article_id: str, mode: str, target_language: str, extra_prompt: str) -> None:
    log_path = _log_path(article_id, "translation")

    def log(msg: str) -> None:
        _write_log(log_path, msg)

    try:
        from translate import translate_article
        ok = translate_article(
            article_id,
            mode=mode,
            target_language=target_language,
            extra_prompt=extra_prompt,
            log_callback=log,
        )
        if ok:
            update_article_fields(article_id, {"translated": True})
    except Exception as exc:  # noqa: BLE001
        import traceback
        log(f"Translation error: {exc}\n{traceback.format_exc()}")
        save_translation_state(article_id, status="error", message=str(exc))
    finally:
        with _translation_lock:
            _translation_threads.pop(article_id, None)


def _run_extract_info(article_id: str, provider_id: str, model: str, reason: str) -> None:
    log_path = _log_path(article_id, "metadata")

    def log(msg: str) -> None:
        _write_log(log_path, msg)

    try:
        from document_info import extract_document_info
        extract_document_info(
            article_id,
            log_callback=log,
            provider_id=provider_id,
            model=model,
            reason=reason,
        )
    except Exception as exc:  # noqa: BLE001
        import traceback
        log(f"Metadata extraction error: {exc}\n{traceback.format_exc()}")
    finally:
        with _metadata_lock:
            current = _metadata_threads.get(article_id)
            if current is threading.current_thread():
                _metadata_threads.pop(article_id, None)


def _start_extract_info(article_id: str, provider_id: str = "", model: str = "", reason: str = "auto", allow_parallel: bool = False) -> bool:
    with _metadata_lock:
        existing = _metadata_threads.get(article_id)
        if existing and existing.is_alive() and not allow_parallel:
            return False
        thread = threading.Thread(
            target=_run_extract_info,
            args=(article_id, provider_id, model, reason),
            daemon=True,
        )
        _metadata_threads[article_id] = thread
        thread.start()
        return True


# ---------------------------------------------------------------------------
# Local env editing
# ---------------------------------------------------------------------------

KNOWN_ENV_KEYS = (
    "LLM_API_KEY",
    "LLM_API_URL",
    "LLM_MODEL",
    "DOCMIND_ACCESS_KEY_ID",
    "DOCMIND_ACCESS_KEY_SECRET",
    "DOCMIND_REGION",
    "DOCPARSER_API_URL",
    "DOCPARSER_API_KEY",
    "DOCPARSER_ENGINE",
    "OCR_PROVIDER_TYPE",
    "OCR_API_URL",
    "OCR_API_KEY",
    "OCR_PROVIDER",
    "OCR_MODEL",
    "OCR_LANG",
    "UNISOUND_API_KEY",
    "UNISOUND_BASE_URL",
    "UNISOUND_MODEL",
    "UNISOUND_TOKEN_PLAN",
    # Per-task LLM routing (empty = use the global active provider/model).
    "CHAT_PROVIDER",
    "CHAT_MODEL",
    "TRANSLATION_PROVIDER",
    "TRANSLATION_MODEL",
)

SENSITIVE_KEYS = {
    "LLM_API_KEY",
    "DOCMIND_ACCESS_KEY_ID",
    "OCR_API_KEY",
    "DOCMIND_ACCESS_KEY_SECRET",
    "DOCPARSER_API_KEY",
    "UNISOUND_API_KEY",
}


def _mask_value(key: str, value: str) -> str:
    # No longer mask: the user wants to see the real value in the settings
    # input so they can edit / copy it. This is safe because the endpoint
    # is served on localhost inside a desktop app (no remote exposure).
    # If you ever expose KBase over a network, add masking back here.
    return value


def public_env() -> dict:
    """Return all known env keys with their effective values.

    Resolution order (first non-empty wins):
      1. ``data/local.env`` — values saved via the Settings UI.
      2. ``os.environ`` — includes values sourced from repo-root
         ``.env.local`` (gitignored, used for source-mode debug keys).

    The frontend uses this to display the real value of API keys so the
    user can see and edit them. Safe because the endpoint is served on
    localhost inside the desktop app.
    """
    import os
    data = public_local_env()
    out: dict[str, dict] = {}
    for k in KNOWN_ENV_KEYS:
        v = (data.get(k) or "").strip()
        if not v:
            v = (os.environ.get(k) or "").strip()
        out[k] = {"value": _mask_value(k, v), "set": bool(v)}
    return out


def _persist_env_updates(updates: dict[str, str]) -> None:
    """Apply UI updates. Empty string means "clear"; None means "no change"."""
    cleaned: dict[str, str] = {}
    for k, v in updates.items():
        if k not in KNOWN_ENV_KEYS:
            continue
        if v is None:
            continue
        cleaned[k] = str(v).strip()
    storage._write_local_env(cleaned)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


class KBHandler(http.server.BaseHTTPRequestHandler):
    server_version = "KBase/1.0"

    def log_message(self, format, *args):
        print(f" [{self.client_address[0]}] {args[0]}")

    def end_headers(self):
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    # Common helpers ----------------------------------------------------

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str) -> None:
        self._json({"error": message}, status=status)

    def _send_download(self, data: bytes, filename: str, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self) -> dict:
        content_type = self.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            raise ValueError("Expected Content-Type: application/json")
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        body = self.rfile.read(length)
        if not body:
            return {}
        return json.loads(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    # GET ----------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        try:
            if path == "/api/articles":
                from legacy_bridge import enrich_articles

                self._json({"articles": enrich_articles(scan_articles())})
            elif path == "/api/settings":
                self._json(self._collect_settings())
            elif path == "/api/llm-config":
                self._json(public_llm_config())
            elif path == "/api/local-env":
                self._json(public_env())
            elif path.startswith("/api/conversion-status/"):
                article_id = article_id_from_request_path(self.path)
                article_dir_for(article_id)
                self._json(self._conv_status_response(article_id))
            elif path.startswith("/api/translation-status/"):
                article_id = article_id_from_request_path(self.path)
                article_dir_for(article_id)
                self._json(_translation_state(article_id))
            elif path.startswith("/api/conversion-history/"):
                article_id = article_id_from_request_path(self.path)
                article_dir = article_dir_for(article_id)
                history = list_conversion_history(article_id)
                versions = []
                for entry in list_article_history(article_id):
                    if not entry.get("file_path"):
                        continue
                    p = Path(entry["file_path"])
                    if not p.exists() or p.parent != article_dir:
                        continue
                    engine = entry["engine"]
                    versions.append({"engine": engine, "file": p.name})
                self._json({"history": history, "versions": versions})
            elif path.startswith("/api/articles/") and path.endswith("/attachments"):
                self.handle_get_attachments()
            elif path.startswith("/api/articles/") and path.endswith("/notes"):
                self.handle_get_article_notes()
            elif path == "/api/articles/duplicates":
                self._json({"groups": _find_duplicate_article_groups(get_all_articles())})
            elif path == "/api/notes":
                from legacy_bridge import enrich_notes

                self._json({"notes": enrich_notes(get_all_notes())})
            elif path.startswith("/api/notes/") and path.endswith("/backlinks"):
                self.handle_note_backlinks()
            elif path.startswith("/api/notes/") and path.endswith("/blocks"):
                self.handle_get_note_blocks()
            elif path.startswith("/api/notes/"):
                self.handle_get_note()
            elif path == "/api/library-chat/sessions":
                self.handle_library_chat_sessions()
            elif path.startswith("/api/library-chat/sessions/"):
                self.handle_library_chat_session_get()
            elif path == "/api/notebooks":
                self._json({"notebooks": list_notebooks()})
            elif path == "/api/article-folders":
                self._json({"folders": list_article_folders()})
            elif path == "/api/workspaces":
                self._json({"workspaces": list_workspaces()})
            elif path == "/api/check-update":
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                force = qs.get("force", [""])[0].lower() in ("1", "true", "yes")
                self._json(check_for_update(force=force))
            elif path == "/api/data-root":
                self._json(get_data_root_info())
            elif path == "/api/workspace/info":
                self.handle_workspace_info()
            elif path == "/api/workspace/recent":
                self._json({"workspaces": load_recent_workspaces()})
            elif path == "/api/workspace/sources":
                self.handle_workspace_sources_get()
            elif path == "/api/workspace/file":
                self.handle_workspace_file_read()
            elif path.startswith("/api/workspace/articles/") and path.endswith("/derivations"):
                aid = path.split("/api/workspace/articles/", 1)[1].rsplit("/derivations", 1)[0].strip("/")
                self.handle_workspace_article_derivations(aid)
            elif path == "/api/workspace/documents":
                self.handle_workspace_documents_get()
            elif path == "/api/workspace/tree":
                self.handle_workspace_tree_get()
            elif path == "/api/workspace/search":
                self.handle_workspace_search()
            elif path == "/api/workspace/bookmarks":
                self.handle_workspace_bookmarks_get()
            elif path == "/api/workspace/reindex":
                self.handle_workspace_reindex()
            elif path == "/api/workspace/ingest-status":
                self.handle_workspace_ingest_status()
            elif path == "/api/workspace/library-status":
                self.handle_workspace_library_status()
            elif path == "/api/workspace/organize-preview":
                self.handle_workspace_organize_preview()
            elif path.startswith("/api/workspace/documents/"):
                doc_id = path.split("/api/workspace/documents/", 1)[1].strip("/")
                self.handle_workspace_document_get(doc_id)
            elif path == "/api/databases":
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                if qs.get("search", [""])[0].strip():
                    self._json({"results": search_databases(qs.get("search", [""])[0])})
                    return
                self._json({"databases": list_databases(), "fieldTypes": public_field_types()})
            elif path.startswith("/api/databases/"):
                self.handle_get_database()
            elif path == "/api/export":
                qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                self.handle_export(
                    force_ids=qs.get("ids", [""])[0].split(","),
                    force_format=qs.get("format", [""])[0],
                )
            elif path in ("/", "/index.html") or path.startswith("/assets/") or path.endswith((".html", ".js", ".css", ".png", ".svg", ".ico")):
                self.serve_static(path)
            else:
                # Allow serving files inside articles/<id>/...
                self.serve_static(path)
        except ValueError as exc:
            self._error(400, str(exc))
        except FileNotFoundError as exc:
            self._error(404, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def _conv_status_response(self, article_id: str) -> dict:
        log_path = _log_path(article_id, "conversion")
        log_content = _read_log(log_path)
        with _conv_lock:
            bucket = _conv_status.get(article_id, {})
            conversion = bucket.get("conversion")
            if not conversion:
                return {"status": "unknown", "message": "", "log": log_content}
            out = dict(conversion)
            out["log"] = log_content
            return out

    def _collect_settings(self) -> dict:
        runtime = {}
        if storage.LOW_MEMORY_CONFIG.exists():
            try:
                runtime = json.loads(storage.LOW_MEMORY_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                runtime = {}
        for key in ("DOCPARSER_API_URL", "DOCPARSER_ENGINE"):
            env_val = os.environ.get(key)
            if env_val:
                runtime[key] = env_val
        return runtime

    def serve_static(self, path: str) -> None:
        # Translate URL path to filesystem path under data/ or package dir.
        relative = urllib.parse.unquote(path.lstrip("/"))
        if not relative or relative == "index.html":
            target = STATIC_INDEX_HTML
        else:
            # Article assets: articles/<id>/<file>
            if relative.startswith("articles/"):
                tail = relative[len("articles/"):]
                parts = tail.split("/", 1)
                if len(parts) == 2:
                    aid = validate_article_id(parts[0])
                    safe_name = parts[1]
                    if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
                        self._error(400, "Invalid asset path")
                        return
                    target = storage.ARTICLES_DIR / aid / safe_name
                else:
                    self._error(404, "Asset not found")
                    return
            elif relative.startswith(".kbase/database_attachments/"):
                tail = relative[len(".kbase/database_attachments/"):]
                parts = tail.split("/", 1)
                if len(parts) != 2 or ".." in tail:
                    self._error(400, "Invalid attachment path")
                    return
                db_id = parts[0]
                try:
                    validate_database_id(db_id)
                except ValueError:
                    self._error(400, "Invalid database id")
                    return
                safe_name = parts[1]
                if ".." in safe_name or "/" in safe_name or "\\" in safe_name:
                    self._error(400, "Invalid attachment path")
                    return
                target = database_attachments_dir(db_id) / safe_name
            else:
                ws_target = _resolve_workspace_static_file(relative)
                if ws_target is not None:
                    target = ws_target
                else:
                    target = (PACKAGE_DIR / relative).resolve()
                    if not _is_inside(target, PACKAGE_DIR.resolve()):
                        self._error(403, "Access denied")
                        return
        if not target.exists():
            self._error(404, f"Not found: {path}")
            return
        if not target.is_file():
            self._error(404, f"Not a file: {path}")
            return
        data = target.read_bytes()
        ctype = self._guess_content_type(target)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    @staticmethod
    def _guess_content_type(target: Path) -> str:
        suffix = target.suffix.lower()
        if suffix in {".md", ".markdown"}:
            return "text/markdown; charset=utf-8"
        if suffix == ".html":
            return "text/html; charset=utf-8"
        if suffix == ".pdf":
            return "application/pdf"
        if suffix == ".js":
            return "application/javascript; charset=utf-8"
        if suffix == ".css":
            return "text/css; charset=utf-8"
        if suffix in {".json", ".geojson"}:
            return "application/json; charset=utf-8"
        if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".svg"}:
            return {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
                ".ico": "image/x-icon",
                ".svg": "image/svg+xml",
            }[suffix]
        if suffix in {".woff", ".woff2", ".ttf", ".otf", ".eot"}:
            return {
                ".woff": "font/woff",
                ".woff2": "font/woff2",
                ".ttf": "font/ttf",
                ".otf": "font/otf",
                ".eot": "application/vnd.ms-fontobject",
            }[suffix]
        return "application/octet-stream"

    # POST ---------------------------------------------------------------

    def do_POST(self):
        path = self.path
        try:
            if path == "/api/upload":
                self.handle_upload()
            elif path == "/api/local-env":
                self.handle_save_env()
            elif path == "/api/articles/delete":
                self.handle_article_delete()
            elif path == "/api/articles/update":
                self.handle_article_update()
            elif path == "/api/articles/import":
                self.handle_article_import()
            elif path == "/api/llm-config":
                self._json(save_llm_config_from_public(self._read_json()))
            elif path == "/api/chat":
                payload = self._read_json()
                messages = payload.get("messages") or []
                if not isinstance(messages, list) or not messages:
                    self._error(400, "messages must be a non-empty list")
                    return
                try:
                    result = call_chat_completion(
                        messages,
                        provider_id=str(payload.get("provider_id", "") or ""),
                        model=str(payload.get("model", "") or ""),
                        temperature=float(payload.get("temperature", 0.3) or 0.3),
                        max_tokens=(
                            int(payload["max_tokens"])
                            if payload.get("max_tokens") is not None
                            else None
                        ),
                        timeout=int(payload.get("timeout", 120) or 120),
                    )
                except (ValueError, TypeError) as exc:
                    self._error(400, str(exc))
                except Exception as exc:  # noqa: BLE001
                    # Surface upstream API errors verbatim so the UI can show
                    # the model provider's message (e.g. 401, 404, 429).
                    self._error(502, f"Upstream LLM error: {exc}")
                else:
                    self._json(result)
            elif path == "/api/library-chat/ask":
                self.handle_library_chat_ask()
            elif path == "/api/library-chat/sessions":
                self.handle_library_chat_sessions_create()
            elif path == "/api/library-chat/sessions/delete":
                self.handle_library_chat_session_delete()
            elif path == "/api/library-chat/sessions/clear":
                self.handle_library_chat_session_clear()
            elif path == "/api/notebooks":
                self.handle_create_notebook()
            elif path == "/api/article-folders":
                self.handle_create_article_folder()
            elif path == "/api/article-folders/move-articles":
                self.handle_move_articles_to_folder()
            elif path == "/api/notes":
                self.handle_create_note()
            elif path.startswith("/api/convert/"):
                self.handle_convert()
            elif path.startswith("/api/calibrate/"):
                self.handle_calibrate()
            elif path.startswith("/api/translate/"):
                self.handle_translate()
            elif path.startswith("/api/extract-info/"):
                self.handle_extract_info()
            elif path.startswith("/api/open-folder/"):
                self.handle_open_folder()
            elif path == "/api/export":
                self.handle_export()
            elif path == "/api/apply-update":
                self.handle_apply_update()
            elif path == "/api/data-root":
                self.handle_set_data_root()
            elif path == "/api/workspace/open":
                self.handle_workspace_open()
            elif path == "/api/workspace/create":
                self.handle_workspace_create()
            elif path == "/api/workspace/delete":
                self.handle_workspace_delete()
            elif path == "/api/workspace/scan":
                self.handle_workspace_scan()
            elif path == "/api/workspace/sources":
                self.handle_workspace_source_add()
            elif path == "/api/workspace/sources/remove":
                self.handle_workspace_source_remove()
            elif path == "/api/workspace/import-managed":
                self.handle_workspace_import_managed()
            elif path.startswith("/api/workspace/documents/") and path.endswith("/preparse"):
                doc_id = path.split("/api/workspace/documents/", 1)[1].rsplit("/preparse", 1)[0].strip("/")
                self.handle_workspace_preparse(doc_id)
            elif path == "/api/workspace/bookmarks":
                self.handle_workspace_bookmarks_create()
            elif path == "/api/workspace/reindex":
                self.handle_workspace_reindex()
            elif path == "/api/workspace/ingest-run":
                self.handle_workspace_ingest_run()
            elif path == "/api/workspace/organize-literature":
                self.handle_workspace_organize_literature()
            elif path == "/api/workspace/settings":
                self.handle_workspace_settings_save()
            elif path == "/api/workspace/file":
                self.handle_workspace_file_read()
            elif path == "/api/workspace/mkdir":
                self.handle_workspace_mkdir()
            elif path == "/api/workspace/write":
                self.handle_workspace_write()
            elif path == "/api/workspace/rename":
                self.handle_workspace_rename()
            elif path == "/api/workspace/delete-path":
                self.handle_workspace_delete_path()
            elif path == "/api/skills/install":
                self.handle_skills_install()
            elif path == "/api/skills/preview":
                self.handle_skills_preview()
            elif path.startswith("/api/articles/") and path.endswith("/attachments"):
                self.handle_upload_attachment()
            elif path.startswith("/api/articles/") and path.endswith("/history/delete"):
                self.handle_history_delete()
            elif path == "/api/databases":
                self.handle_create_database()
            elif path.startswith("/api/databases/"):
                self.handle_database_post()
            else:
                self._error(404, "Not found")
        except ValueError as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    # PUT ----------------------------------------------------------------

    def do_PUT(self):
        path = self.path
        try:
            if path == "/save":
                self.handle_save_file()
            elif path == "/api/llm-config":
                self._json(save_llm_config_from_public(self._read_json()))
            elif path == "/api/articles/update":
                self.handle_article_update()
            elif path.startswith("/api/notebooks/"):
                self.handle_update_notebook()
            elif path.startswith("/api/article-folders/"):
                self.handle_update_article_folder()
            elif path.startswith("/api/notes/") and path.endswith("/rename"):
                self.handle_rename_note()
            elif path.startswith("/api/notes/"):
                self.handle_save_note()
            elif path.startswith("/api/databases/"):
                self.handle_database_put()
            else:
                self._error(404, "Not found")
        except ValueError as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    # DELETE -------------------------------------------------------------

    def do_DELETE(self):
        path = self.path
        try:
            if path.startswith("/api/notes/"):
                self.handle_delete_note()
            elif path.startswith("/api/notebooks/"):
                self.handle_delete_notebook()
            elif path.startswith("/api/article-folders/"):
                self.handle_delete_article_folder()
            elif path.startswith("/api/articles/") and "/attachments/" in path:
                self.handle_delete_attachment()
            elif path.startswith("/api/databases/"):
                self.handle_database_delete()
            elif path.startswith("/api/workspace/bookmarks/"):
                doc_id = path.split("/api/workspace/bookmarks/", 1)[1].strip("/")
                self.handle_workspace_bookmark_delete(doc_id)
            else:
                self._error(404, "Not found")
        except ValueError as exc:
            self._error(400, str(exc))
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    # Handlers -----------------------------------------------------------

    def handle_save_env(self):
        body = self._read_json()
        if not isinstance(body, dict):
            raise ValueError("Expected object")
        updates: dict[str, str] = {}
        for key in KNOWN_ENV_KEYS:
            if key in body:
                value = body[key]
                if value in (None, ""):
                    updates[key] = ""
                else:
                    updates[key] = str(value).strip()
        _persist_env_updates(updates)
        self._json(public_env())

    def handle_save_file(self):
        body = self._read_json()
        filepath = body.get("path", "")
        content = body.get("content", "")
        try:
            target = resolve_save_path(filepath)
        except ValueError as exc:
            self._error(403, str(exc))
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._json({"status": "ok"})

    def handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._error(400, "Expected multipart/form-data")
            return

        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self._error(400, "No boundary found")
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        boundary_bytes = boundary.encode()

        for part in body.split(b"--" + boundary_bytes):
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end].decode(errors="replace")
            content = part[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if 'name="file"' not in headers_raw:
                continue

            filename = "upload.pdf"
            disposition = next(
                (line for line in headers_raw.splitlines() if line.lower().startswith("content-disposition")),
                "",
            )
            match = re.search(r'filename="([^"]*)"', disposition) or re.search(r"filename=([^;\r\n]+)", disposition)
            if match:
                filename = Path(match.group(1).strip()).name or filename

            article_id = sanitize_article_id(filename)
            ext = Path(filename).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,12}", ext or ""):
                ext = ".bin"

            existing = get_article(article_id)
            if existing:
                article_id = f"{article_id}_{int(time.time())}"

            article_dir = article_dir_for(article_id)
            article_dir.mkdir(parents=True, exist_ok=True)
            is_pdf = ext == ".pdf"
            original_path = article_dir / ("original.pdf" if is_pdf else f"original{ext}")
            original_path.write_bytes(content)

            base = Path(filename).stem or article_id
            title = base.replace("_", " ")
            pages = 0
            kind = "paper" if is_pdf else "file"
            md_available = False
            preparse_error = ""

            try:
                from document_info import (
                    ingest_non_pdf_file,
                    material_kind_from_filename,
                    quick_parse_pdf,
                )
            except ImportError as exc:
                preparse_error = f"Document parsing unavailable: {exc}"
                if is_pdf:
                    record_conversion(article_id, "pymupdf", "fail")
            else:
                try:
                    kind = material_kind_from_filename(filename)
                    if is_pdf:
                        parsed = quick_parse_pdf(article_id, original_path, filename)
                        title = parsed.get("title") or title
                        pages = parsed.get("pages") or 0
                        md_available = True
                        record_conversion(article_id, "pymupdf", "success")
                        from storage import record_article_history as _rah
                        try:
                            _rah(article_id, "pymupdf", article_dir / f"{article_id}.md")
                        except Exception:
                            pass
                    else:
                        parsed = ingest_non_pdf_file(article_id, original_path, filename)
                        title = parsed.get("title") or title
                        kind = parsed.get("kind") or kind
                        md_available = True
                except Exception as exc:  # noqa: BLE001
                    preparse_error = str(exc)
                    if is_pdf:
                        record_conversion(article_id, "pymupdf", "fail")

            article = {
                "id": article_id,
                "title": title,
                "author": "",
                "authors": [],
                "pages": pages,
                "date_added": time.strftime("%Y-%m-%d %H:%M"),
                "category": "",
                "tags": [],
                "translated": False,
                "summarized": False,
                "pdf_available": is_pdf,
                "md_available": md_available,
                "file_available": True,
                "source_filename": filename,
                "kind": kind,
                "doi": "",
                "year": "",
                "venue": "",
                "abstract": "",
                "metadata_extracted": False,
                "metadata_source": "",
                "parser": "pymupdf" if is_pdf and md_available else "",
                "preparse_error": preparse_error,
            }
            upsert_article(article)
            info_extraction = "running" if md_available and _start_extract_info(article_id, reason="upload") else ""
            self._json({
                "status": "ok",
                "article": article,
                "preparsed": md_available,
                "preparse_error": preparse_error,
                "info_extraction": info_extraction,
            })
            return
        self._error(400, "No file field found")

    def handle_calibrate(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
        article_dir = article_dir_for(article_id)
        md_file = article_dir / f"{article_id}.md"
        if not md_file.exists():
            self._error(404, "No markdown to calibrate")
            return

        def log(msg: str) -> None:
            set_conv_status(article_id, "calibration", "running", msg)

        set_conv_status(article_id, "calibration", "running", "校准中...")
        thread = threading.Thread(target=_run_calibrate, args=(article_id, log), daemon=True)
        thread.start()
        self._json({"status": "calibrating", "id": article_id})

    def handle_translate(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
        mode = body.get("mode", "update")
        target_language = body.get("target_language", "Simplified Chinese")
        extra_prompt = body.get("extra_prompt", "")
        article_dir = article_dir_for(article_id)
        if not (article_dir / f"{article_id}_calibrated.md").exists() and not (article_dir / f"{article_id}.md").exists():
            self._error(404, "No markdown to translate")
            return

        with _translation_lock:
            thread = _translation_threads.get(article_id)
            if thread and thread.is_alive():
                self._json({"status": "running", "id": article_id, "message": "翻译已在后台运行"})
                return
            save_translation_state(
                article_id,
                status="running",
                message="后台翻译已启动",
                current=0,
                total=0,
                percent=0,
                started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                target_language=target_language,
            )
            thread = threading.Thread(
                target=_run_translate,
                args=(article_id, mode, target_language, extra_prompt),
                daemon=True,
            )
            _translation_threads[article_id] = thread
            thread.start()
        self._json({"status": "running", "id": article_id, "message": "后台翻译已启动"})

    def handle_extract_info(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
        article_dir = article_dir_for(article_id)
        if not article_dir.exists():
            self._error(404, "Article not found")
            return
        if not (
            (article_dir / f"{article_id}.md").exists()
            or (article_dir / f"{article_id}_calibrated.md").exists()
            or (article_dir / f"{article_id}_translated.md").exists()
        ):
            self._error(404, "No markdown available for metadata extraction")
            return

        provider_id = body.get("provider_id") or body.get("provider") or ""
        model = body.get("model") or ""
        reason = body.get("reason") or "manual"
        if body.get("background"):
            started = _start_extract_info(
                article_id,
                provider_id=provider_id,
                model=model,
                reason=reason,
                allow_parallel=bool(body.get("force")),
            )
            self._json({"status": "running" if started else "already_running", "id": article_id, "reason": reason})
            return
        from document_info import extract_document_info
        result = extract_document_info(article_id, provider_id=provider_id, model=model, reason=reason)
        scan_articles()
        self._json({"status": "ok", "id": article_id, **result, "article": get_article(article_id)})

    def handle_open_folder(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
        article_dir = article_dir_for(article_id)
        if not article_dir.exists():
            self._error(404, "Article folder not found")
            return
        if sys.platform.startswith("win"):
            os.startfile(str(article_dir))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(article_dir)])
        else:
            subprocess.Popen(["xdg-open", str(article_dir)])
        self._json({"status": "ok", "path": str(article_dir)})

    def handle_export(self, force_ids=None, force_format=None):
        if force_ids is not None:
            ids = force_ids
            export_format = str(force_format or "").lower().strip()
        else:
            body = self._read_json()
            export_format = str(body.get("format") or "").lower().strip()
            ids = body.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        ids = [validate_article_id(i) for i in ids if i]
        ids = list(dict.fromkeys(ids))
        if not ids:
            self._error(400, "No articles selected")
            return
        if export_format not in {"bibtex", "ris", "csljson", "pdf", "markdown"}:
            self._error(400, "Unsupported export format")
            return

        articles = [a for a in scan_articles() if a["id"] in ids]
        if not articles:
            self._error(404, "Selected articles were not found")
            return

        stamp = time.strftime("%Y%m%d_%H%M%S")
        if export_format == "bibtex":
            used: set[str] = set()
            content = ("\n\n".join(_article_to_bibtex(a, used) for a in articles) + "\n").encode("utf-8")
            self._send_download(content, f"kbase_export_{stamp}.bib", "application/x-bibtex; charset=utf-8")
            return
        if export_format == "ris":
            content = ("\n".join(_article_to_ris(a) for a in articles) + "\n").encode("utf-8")
            self._send_download(content, f"kbase_export_{stamp}.ris", "application/x-research-info-systems; charset=utf-8")
            return
        if export_format == "csljson":
            content = json.dumps(
                [_article_to_csl_json(article) for article in articles],
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            self._send_download(content, f"kbase_export_{stamp}.json", "application/json; charset=utf-8")
            return

        archive = io.BytesIO()
        used_names: set[str] = set()
        missing: list[str] = []
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for article in articles:
                aid = article["id"]
                if export_format == "pdf":
                    source = article_dir_for(aid) / "original.pdf"
                    ext = ".pdf"
                else:
                    source = _preferred_markdown_file(aid)
                    ext = ".md"
                if not source or not source.exists():
                    missing.append(aid)
                    continue
                name = _unique_archive_name(f"{_export_stem(article)}{ext}", used_names)
                zf.write(source, name)
            if missing:
                zf.writestr("missing.txt", "以下条目没有可导出的文件:\n" + "\n".join(missing) + "\n")
        payload = archive.getvalue()
        if not used_names:
            self._error(404, "No files available for this export")
            return
        suffix = "pdf" if export_format == "pdf" else "markdown"
        self._send_download(payload, f"kbase_{suffix}_{stamp}.zip", "application/zip")

    def handle_convert(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", ""))
        engine = body.get("engine", "marker")
        docparser_engine = body.get("docparser_engine", "").strip()
        article_dir = article_dir_for(article_id)
        pdf_path = article_dir / "original.pdf"
        if not pdf_path.exists():
            self._error(404, "PDF not found")
            return
        update_article_fields(article_id, {"converting": True})
        thread = threading.Thread(
            target=run_conversion,
            args=(str(pdf_path), article_id, engine, docparser_engine),
            daemon=True,
        )
        thread.start()
        self._json({"status": "converting", "id": article_id, "engine": engine})

    def handle_article_update(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", ""))
        updates = body.get("updates", {})
        if not isinstance(updates, dict):
            self._error(400, "updates must be an object")
            return
        update_article_fields(article_id, updates)
        self._json({"status": "ok"})

    def handle_article_import(self):
        body = self._read_json()
        text = str(body.get("text") or "")
        if not text.strip():
            self._error(400, "text is required")
            return
        if len(text.encode("utf-8")) > 10 * 1024 * 1024:
            self._error(413, "Import file is too large")
            return
        fmt = str(body.get("format") or "").strip().lower()
        filename = str(body.get("filename") or "").strip()
        skip_duplicates = body.get("skipDuplicates", True) is not False
        result = _import_reference_records(
            text,
            fmt=fmt,
            filename=filename,
            skip_duplicates=skip_duplicates,
        )
        self._json(result)

    def handle_article_delete(self):
        body = self._read_json()
        article_id = validate_article_id(body.get("id", ""))
        delete_article(article_id)
        article_dir = article_dir_for(article_id)
        if article_dir.exists():
            shutil.rmtree(article_dir)
        self._json({"status": "ok"})

    def handle_history_delete(self):
        body = self._read_json()
        article_id = validate_article_id(urllib.parse.unquote(self.path.rstrip("/").split("/")[-3]))
        engine = body.get("engine", "").strip()
        if not engine:
            self._error(400, "engine is required")
            return
        delete_article_history(article_id, engine)
        self._json({"status": "ok"})

    # Library chat -------------------------------------------------------

    def handle_library_chat_ask(self):
        try:
            body = self._read_json()
        except Exception:
            self._error(400, "Invalid JSON")
            return
        try:
            from library_chat import ask_library_question
            result = ask_library_question(
                body.get("question") or body.get("message") or "",
                session_id=body.get("session_id") or "",
                provider_id=body.get("provider_id") or body.get("provider") or "",
                model=body.get("model") or "",
            )
            self._json(result)
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def handle_library_chat_sessions(self):
        try:
            from library_chat import list_sessions
            self._json(list_sessions())
        except Exception as exc:  # noqa: BLE001
            self._error(500, str(exc))

    def handle_library_chat_session_get(self):
        try:
            from library_chat import get_session
            session_id = urllib.parse.unquote(
                urllib.parse.urlsplit(self.path).path.rstrip("/").rsplit("/", 1)[-1]
            )
            self._json(get_session(session_id))
        except Exception as exc:  # noqa: BLE001
            self._error(404, str(exc))

    def handle_library_chat_sessions_create(self):
        try:
            from library_chat import create_session
            body = self._read_json()
            self._json(create_session(body.get("title") or "新会话"))
        except Exception as exc:  # noqa: BLE001
            self._error(400, str(exc))

    def handle_library_chat_session_delete(self):
        try:
            from library_chat import delete_session
            body = self._read_json()
            self._json(delete_session(str(body.get("session_id") or "")))
        except Exception as exc:  # noqa: BLE001
            self._error(400, str(exc))

    def handle_library_chat_session_clear(self):
        try:
            from library_chat import clear_session
            body = self._read_json()
            self._json(clear_session(str(body.get("session_id") or "")))
        except Exception as exc:  # noqa: BLE001
            self._error(400, str(exc))

    # Notes --------------------------------------------------------------

    def _note_id_from_path(self) -> str:
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "notes"]:
            return validate_note_id(urllib.parse.unquote(parts[2]))
        raise ValueError("Invalid note path")

    def handle_get_note(self):
        note_id = self._note_id_from_path()
        md_path = resolve_note_markdown_path(note_id)
        if not md_path.exists():
            self._error(404, "Note not found")
            return
        content = md_path.read_text(encoding="utf-8")
        meta = next((n for n in get_all_notes() if n["id"] == note_id), None)
        payload = {"id": note_id, "content": content, "meta": meta}
        ws_path = workspace_path_for_note_id(note_id)
        if ws_path:
            payload["path"] = ws_path
        self._json(payload)

    def handle_get_note_blocks(self):
        note_id = self._note_id_from_path()
        md_path = resolve_note_markdown_path(note_id)
        if not md_path.exists():
            self._error(404, "Note not found")
            return
        blocks = get_note_blocks(note_id)
        self._json({"note_id": note_id, "blocks": blocks})

    def handle_get_database(self):
        db_id, sub, sub_id = _database_path_parts(self.path)
        if not db_id:
            self._error(404, "Not found")
            return
        validate_database_id(db_id)
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if sub == "export":
            fmt = qs.get("format", ["csv"])[0] or "csv"
            view_id = qs.get("view", [""])[0] or None
            if fmt == "csv":
                csv_text = export_database_csv(db_id, view_id or None)
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{db_id}.csv"')
                self.end_headers()
                self.wfile.write(csv_text.encode("utf-8-sig"))
                return
            self._error(400, "Unsupported export format")
            return
        if sub == "history":
            self._json({"history": list_database_history(db_id)})
            return
        if sub:
            self._error(404, "Not found")
            return
        if qs.get("render", [""])[0] in ("1", "true", "yes"):
            view_id = qs.get("view", [""])[0] or None
            query = qs.get("q", [""])[0] or ""
            self._json(render_database(db_id, view_id or None, query=query))
            return
        self._json(load_database(db_id))

    def handle_create_database(self):
        body = self._read_json()
        name = str(body.get("name") or "Untitled").strip()[:120] or "Untitled"
        self._json(create_database(name))

    def handle_database_post(self):
        db_id, sub, sub_id = _database_path_parts(self.path)
        if not db_id:
            self._error(404, "Not found")
            return
        validate_database_id(db_id)
        if sub == "attachments":
            self.handle_database_attachment_upload(db_id)
            return
        body = self._read_json()
        if sub == "rows":
            if sub_id == "batch-delete":
                ids = body.get("ids") if isinstance(body.get("ids"), list) else []
                deleted = batch_delete_rows(db_id, [str(x) for x in ids])
                self._json({"deleted": deleted})
                return
            row = add_row(db_id, body.get("cells") if isinstance(body.get("cells"), dict) else None)
            self._json(row)
            return
        if sub == "import":
            csv_text = str(body.get("csv") or "")
            mode = str(body.get("mode") or "append")
            self._json(import_database_csv(db_id, csv_text, mode=mode))
            return
        if sub == "history" and sub_id:
            self._json(restore_database_history(db_id, sub_id))
            return
        if sub == "ai-generate":
            self.handle_database_ai_generate(db_id, body if isinstance(body, dict) else {})
            return
        if sub == "columns":
            col = add_column(
                db_id,
                str(body.get("name") or "新列"),
                str(body.get("type") or "text"),
                **{k: body[k] for k in (
                    "linkDatabase", "bidirectional", "reverseColumn", "linkColumn",
                    "lookupColumn", "rollupColumn", "rollupFn", "expression", "aiPrompt",
                ) if k in body},
            )
            self._json(col)
            return
        if sub == "views":
            view = add_view(
                db_id,
                str(body.get("name") or "新视图"),
                str(body.get("type") or "table"),
                group_column=str(body.get("groupColumn") or ""),
                cover_column=str(body.get("coverColumn") or ""),
                date_column=str(body.get("dateColumn") or ""),
                end_date_column=str(body.get("endDateColumn") or ""),
                category_column=str(body.get("categoryColumn") or ""),
                value_column=str(body.get("valueColumn") or ""),
            )
            self._json(view)
            return
        self._error(404, "Not found")

    def handle_database_attachment_upload(self, db_id: str):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._error(400, "Expected multipart/form-data")
            return
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self._error(400, "No boundary found")
            return
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            self._error(400, "Empty payload")
            return
        body = self.rfile.read(length)
        boundary_bytes = boundary.encode()
        for part in body.split(b"--" + boundary_bytes):
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end].decode(errors="replace")
            content = part[header_end + 4 :]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if 'name="file"' not in headers_raw:
                continue
            filename = "upload.bin"
            disposition = next(
                (line for line in headers_raw.splitlines() if line.lower().startswith("content-disposition")),
                "",
            )
            match = re.search(r'filename="([^"]*)"', disposition) or re.search(r"filename=([^;\r\n]+)", disposition)
            if match:
                filename = Path(match.group(1).strip()).name or filename
            if not content:
                self._error(400, "Empty file")
                return
            self._json(save_database_attachment(db_id, filename, content))
            return
        self._error(400, "file field required")

    def handle_database_ai_generate(self, db_id: str, body: dict):
        row_id = str(body.get("rowId") or "")
        col_id = str(body.get("columnId") or "")
        if not row_id or not col_id:
            self._error(400, "rowId and columnId required")
            return
        db = load_database(db_id)
        col_map = {c["id"]: c for c in db.get("columns") or []}
        col = col_map.get(col_id)
        if not col or col.get("type") != "ai_text":
            self._error(400, "Not an ai_text column")
            return
        row = next((r for r in db.get("rows") or [] if r.get("id") == row_id), None)
        if not row:
            self._error(404, "Row not found")
            return
        prompt_parts = [str(col.get("aiPrompt") or "根据以下字段生成文本：")]
        for c in db.get("columns") or []:
            if c.get("type") in ("ai_text", "formula", "lookup", "rollup"):
                continue
            val = (row.get("cells") or {}).get(c["id"], "")
            if val not in ("", None, [], False):
                prompt_parts.append(f"{c.get('name')}: {val}")
        messages = [{"role": "user", "content": "\n".join(prompt_parts)}]
        data = call_chat_completion(messages)
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        updated = update_row(db_id, row_id, {col_id: text.strip()})
        self._json({"text": text.strip(), "row": updated})

    def handle_database_put(self):
        db_id, sub, sub_id = _database_path_parts(self.path)
        if not db_id:
            self._error(404, "Not found")
            return
        validate_database_id(db_id)
        body = self._read_json()
        if sub == "rows" and sub_id:
            cells = body.get("cells") if isinstance(body.get("cells"), dict) else body
            if not isinstance(cells, dict):
                self._error(400, "cells object required")
                return
            self._json(update_row(db_id, sub_id, cells))
            return
        if sub == "columns" and sub_id:
            self._json(update_column(db_id, sub_id, body if isinstance(body, dict) else {}))
            return
        if sub == "views" and sub_id:
            self._json(update_view(db_id, sub_id, body if isinstance(body, dict) else {}))
            return
        if not sub:
            self._json(update_database_meta(db_id, body if isinstance(body, dict) else {}))
            return
        self._error(404, "Not found")

    def handle_database_delete(self):
        db_id, sub, sub_id = _database_path_parts(self.path)
        if not db_id:
            self._error(404, "Not found")
            return
        validate_database_id(db_id)
        if sub == "rows" and sub_id:
            delete_row(db_id, sub_id)
            self._json({"status": "ok"})
            return
        if sub == "columns" and sub_id:
            delete_column(db_id, sub_id)
            self._json({"status": "ok"})
            return
        if sub == "views" and sub_id:
            delete_view(db_id, sub_id)
            self._json({"status": "ok"})
            return
        if not sub:
            delete_database(db_id)
            self._json({"status": "ok"})
            return
        self._error(404, "Not found")

    def handle_create_notebook(self):
        self._error(410, "Notebooks are deprecated; organize notes with folders")

    def handle_update_notebook(self):
        self._error(410, "Notebooks are deprecated; organize notes with folders")

    def handle_delete_notebook(self):
        self._error(410, "Notebooks are deprecated; organize notes with folders")

    def handle_create_article_folder(self):
        body = self._read_json()
        name = str(body.get("name") or "").strip()[:100]
        if not name:
            self._error(400, "Folder name is required")
            return
        folder = create_article_folder(
            name=name,
            parent_id=body.get("parent_id") or None,
            icon=str(body.get("icon") or "")[:16],
            sort_order=int(body.get("sort_order") or 0),
        )
        self._json({"status": "ok", "folder": folder, "folders": list_article_folders()})

    def handle_update_article_folder(self):
        fid = urllib.parse.unquote(self.path.rstrip("/").rsplit("/", 1)[-1])
        body = self._read_json()
        new_parent = body.get("parent_id")
        if new_parent is not None and new_parent and _is_folder_ancestor(fid, new_parent):
            self._error(400, "Cannot move folder under its descendant")
            return
        update_article_folder(fid, **body)
        self._json({"status": "ok", "folders": list_article_folders()})

    def handle_delete_article_folder(self):
        fid = urllib.parse.unquote(self.path.rstrip("/").rsplit("/", 1)[-1])
        delete_article_folder(fid)
        self._json({"status": "ok", "folders": list_article_folders()})

    def handle_move_articles_to_folder(self):
        body = self._read_json()
        article_ids = body.get("article_ids") or []
        folder_id = body.get("folder_id") or None
        if not isinstance(article_ids, list) or not article_ids:
            self._error(400, "article_ids is required")
            return
        move_articles_to_folder(article_ids, folder_id)
        self._json({"status": "ok"})

    def handle_note_backlinks(self):
        parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
        if len(parts) < 5:
            self._error(400, "Invalid path")
            return
        note_id = urllib.parse.unquote(parts[3])
        # Use the indexed note_links table — it has target_note_id +
        # target_anchor + source note title, all in one query.
        links = get_note_backlinks(note_id)
        out = []
        seen_source = set()
        for lk in links:
            sid = lk["id"]
            if sid in seen_source:
                continue
            seen_source.add(sid)
            out.append({
                "id": sid,
                "title": lk.get("title") or sid,
                "target_anchor": lk.get("target_anchor"),
                "anchor_heading": lk.get("heading"),
            })
        self._json({"backlinks": out})

    def handle_create_note(self):
        body = self._read_json()
        title = str(body.get("title") or "Untitled").strip()[:200] or "Untitled"
        rel_dir = str(body.get("dir") or body.get("rel_dir") or "").replace("\\", "/").strip("/")
        article_id = str(body.get("article_id") or "").strip()[:128] or None
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(body.get("slug") or title).strip())[:80] or "note"

        if article_id:
            note_id = f"art_{article_id}__{slug}"
            md_path = note_file_for(note_id)
            if not md_path.exists():
                md_path.write_text(f"# {title}\n\n", encoding="utf-8")
            path_out = f"notes/{note_id}.md"
            folder = str(body.get("folder") or "").strip()[:200]
        else:
            # Prefer human-readable filename under the chosen directory.
            base_name = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", title).strip("._") or "Untitled"
            if not base_name.lower().endswith(".md"):
                base_name = f"{base_name}.md"
            if rel_dir:
                require_managed_workspace_path(rel_dir)
                target_dir = resolve_workspace_rel_path(rel_dir, must_exist=False)
            else:
                try:
                    target_dir = resolve_workspace_rel_path("notes", must_exist=False)
                except ValueError:
                    target_dir = storage.NOTES_DIR
            target_dir.mkdir(parents=True, exist_ok=True)
            md_path = target_dir / base_name
            if md_path.exists():
                stem = Path(base_name).stem
                md_path = target_dir / f"{stem}_{int(time.time())}.md"
            md_path.write_text(f"# {title}\n\n", encoding="utf-8")
            try:
                root = _workspace_static_root()
                path_out = md_path.resolve().relative_to(root).as_posix()
            except ValueError:
                path_out = f"notes/{md_path.name}"
            note_id = note_id_for_workspace_path(path_out)
            folder = f"path:{path_out}"

        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": note_id,
            "title": title,
            "created_at": now,
            "modified_at": now,
            "tags": [],
            "folder": folder,
            "notebook_id": "nb_default",
            "parent_id": None,
            "article_id": article_id,
            "links": [],
            "path": path_out,
        }
        upsert_note(entry)
        self._json(entry)

    def handle_save_note(self):
        note_id = self._note_id_from_path()
        body = self._read_json()
        content = str(body.get("content") or "")
        title = str(body.get("title") or "").strip()[:200]
        # Allow binding/saving by workspace path (folder-first).
        req_path = str(body.get("path") or "").replace("\\", "/").strip("/")
        if req_path:
            if not req_path.lower().endswith((".md", ".markdown")):
                self._error(400, "Only markdown paths can be saved as notes")
                return
            require_managed_workspace_path(req_path)
            md_path = resolve_workspace_rel_path(req_path, must_exist=False)
            note_id = note_id_for_workspace_path(req_path)
            folder = f"path:{req_path}"
        else:
            if note_id == "workspace_note":
                self._error(400, "path required for workspace notes")
                return
            existing_path = workspace_path_for_note_id(note_id)
            if existing_path:
                require_managed_workspace_path(existing_path)
            md_path = resolve_note_markdown_path(note_id)
            folder = None
        if not md_path.exists() and "content" not in body:
            self._error(404, "Note not found")
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        existing = next((n for n in get_all_notes() if n["id"] == note_id), {"id": note_id})
        existing["title"] = title or existing.get("title", "") or md_path.stem
        existing["modified_at"] = now
        if folder:
            existing["folder"] = folder
        if "tags" in body:
            tags = body["tags"]
            existing["tags"] = [str(t).strip()[:50] for t in tags if str(t).strip()] if isinstance(tags, list) else []
        # Folder-first: ignore notebook/parent organization.
        existing["notebook_id"] = "nb_default"
        existing["parent_id"] = None
        if "doc_icon" in body:
            existing["doc_icon"] = str(body.get("doc_icon") or "").strip()[:16]
        if "article_id" in body:
            existing["article_id"] = (str(body.get("article_id") or "").strip()[:128] or None)
        existing.setdefault("created_at", now)
        upsert_note(existing)
        if "content" in body:
            md_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                rows = sync_note_blocks(note_id, content)
                annotated = inject_block_anchors(content, rows)
                md_path.write_text(annotated, encoding="utf-8")
                sync_note_links(note_id, annotated)
            except Exception as exc:  # noqa: BLE001
                print(f"sync_note_blocks/links failed for {note_id}: {exc}")
                md_path.write_text(content, encoding="utf-8")
        try:
            saved = md_path.read_text(encoding="utf-8")
        except OSError:
            saved = content if "content" in body else ""
        out = {"status": "ok", "content": saved, "id": note_id}
        ws_path = workspace_path_for_note_id(note_id) or req_path
        if ws_path:
            out["path"] = ws_path
        self._json(out)

    def handle_delete_note(self):
        note_id = self._note_id_from_path()
        existing_path = workspace_path_for_note_id(note_id)
        if existing_path:
            require_managed_workspace_path(existing_path)
        md_path = resolve_note_markdown_path(note_id)
        if md_path.exists():
            md_path.unlink()
        delete_note(note_id)
        self._json({"status": "ok"})

    def handle_rename_note(self):
        note_id = self._note_id_from_path()
        body = self._read_json()
        new_title = str(body.get("title") or "").strip()[:200]
        if not new_title:
            self._error(400, "Title is required")
            return
        existing = next((n for n in get_all_notes() if n["id"] == note_id), None)
        if not existing:
            self._error(404, "Note not found")
            return
        existing["title"] = new_title
        existing["modified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        upsert_note(existing)
        self._json({"status": "ok", "title": new_title})

    # Attachments --------------------------------------------------------

    def handle_get_attachments(self):
        article_id = validate_article_id(urllib.parse.unquote(urllib.parse.urlsplit(self.path).path.split("/")[3]))
        rows = list_article_attachments(article_id)
        files = []
        for r in rows:
            name = r["name"]
            files.append({
                "name": name,
                "size": r["size"],
                "modified": r["mtime"],
                "url": f"articles/{urllib.parse.quote(article_id)}/{urllib.parse.quote(name)}",
            })
        self._json({"attachments": files})

    def handle_get_article_notes(self):
        """GET /api/articles/<id>/notes — list every note that
        references this article (scoped or via @-mention), and
        include a count for header badges."""
        article_id = validate_article_id(urllib.parse.unquote(urllib.parse.urlsplit(self.path).path.split("/")[3]))
        notes = get_notes_for_article(article_id)
        # Decorate each note with a one-line content preview (first
        # non-heading line) so the list view in the article pane
        # shows something useful without a second round-trip.
        out = []
        for n in notes:
            preview = ""
            try:
                p = note_file_for(n["id"])
                if p.exists():
                    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                        s = line.strip()
                        if s and not s.startswith("#") and not s.startswith("<!--"):
                            preview = s[:120]
                            break
            except OSError:
                pass
            out.append({
                "id": n["id"],
                "title": n.get("title", ""),
                "tags": n.get("tags", []),
                "modified_at": n.get("modified_at", ""),
                "preview": preview,
                "scoped": bool(n.get("article_id") == article_id),
            })
        self._json({"notes": out, "count": len(out)})

    def handle_upload_attachment(self):
        article_id = validate_article_id(urllib.parse.unquote(urllib.parse.urlsplit(self.path).path.split("/")[3]))
        article_dir = article_dir_for(article_id)
        attachments_dir = article_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        length = int(self.headers.get("Content-Length", 0))
        if not length:
            self._error(400, "Empty payload")
            return
        body = self.rfile.read(length)
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self._error(400, "Must be multipart/form-data")
            return
        boundary = ""
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self._error(400, "No boundary found")
            return

        boundary_bytes = boundary.encode()
        uploaded: list[str] = []
        for part in body.split(b"--" + boundary_bytes):
            if b"Content-Disposition" not in part:
                continue
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue
            headers_raw = part[:header_end].decode(errors="replace")
            content = part[header_end + 4:]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            if 'name="file"' not in headers_raw:
                continue
            filename = "attachment.bin"
            disposition = next(
                (line for line in headers_raw.splitlines() if line.lower().startswith("content-disposition")),
                "",
            )
            match = re.search(r'filename="([^"]*)"', disposition) or re.search(r"filename=([^;\r\n]+)", disposition)
            if match:
                filename = Path(match.group(1).strip()).name or filename
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "attachment.bin"
            file_path = attachments_dir / safe_name
            file_path.write_bytes(content)
            upsert_article_attachment(article_id, safe_name, file_path)
            uploaded.append(safe_name)
        if uploaded:
            self._json({"status": "ok", "filenames": uploaded})
        else:
            self._error(400, "No file found in payload")

    def handle_delete_attachment(self):
        parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
        article_id = validate_article_id(urllib.parse.unquote(parts[3]))
        filename = urllib.parse.unquote(parts[5])
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            self._error(400, "Invalid filename")
            return
        ok = delete_article_attachment(article_id, filename)
        if ok:
            self._json({"status": "ok"})
        else:
            self._error(404, "File not found")


    # ------------------------------------------------------------------
    # Update & data-root endpoints
    # ------------------------------------------------------------------

    def handle_apply_update(self) -> None:
        """POST /api/apply-update — launch a detached PowerShell updater.

        The PS script downloads the update, waits for KBase.exe to exit,
        applies the update (silent NSIS or portable zip extract), and
        restarts the app — all independently of the current process.

        The frontend should call pywebview.api.quit_app() after receiving
        the "ok" response so the updater can proceed.
        """
        if not getattr(sys, "frozen", False):
            self._error(400, "一键更新仅支持打包版 KBase.exe")
            return
        data = self._read_json()
        asset_url = (data.get("assetUrl") or "").strip()
        if not asset_url:
            self._error(400, "assetUrl is required")
            return

        ok = apply_update(asset_url)
        if not ok:
            self._error(500, "无法启动更新程序")
            return

        self._json({
            "ok": True,
            "message": "更新程序已启动，窗口即将关闭…",
            "closeWindow": True,
        })

    def handle_set_data_root(self) -> None:
        """POST /api/data-root — persist a new data root path."""
        data = self._read_json()
        new_root = (data.get("dataRoot") or "").strip()
        path_info = set_data_root(new_root)
        self._json(path_info)

    def handle_workspace_open(self) -> None:
        """POST /api/workspace/open — open a workspace directory.

        Full filesystem scan and article reconcile run in the background so the
        UI can switch instantly (large / synced disks otherwise appear stuck).
        """
        data = self._read_json()
        ws_path = (data.get("path") or "").strip()
        if not ws_path:
            self._error(400, "path is required")
            return
        want_scan = bool(data.get("scan", True))
        bind_data = bool(data.get("bindData", True))
        try:
            # Never scan synchronously — Baidu Sync / large trees block for minutes.
            ws = open_workspace(ws_path, scan=False)
            if bind_data:
                data_root = _activate_workspace_session(ws, heavy=want_scan, scan=want_scan)
            else:
                data_root = None
                if want_scan:
                    def _scan_only() -> None:
                        try:
                            ws.scan(full=True)
                        except Exception:
                            pass

                    threading.Thread(
                        target=_scan_only,
                        daemon=True,
                        name="ws-open-scan",
                    ).start()
        except (OSError, ValueError) as exc:
            self._error(400, str(exc))
            return
        payload: dict = {"ok": True, "workspace": ws.info(), "scanPending": want_scan}
        if data_root:
            payload["dataRoot"] = data_root
            # Article reconcile is expensive; client refreshes via GET /api/articles.
            payload["articlesPending"] = True
        self._json(payload)

    def handle_workspace_create(self) -> None:
        """POST /api/workspace/create — create and open a new workspace."""
        data = self._read_json()
        path = (data.get("path") or "").strip()
        name = (data.get("name") or "").strip()
        parent = (data.get("parent") or "").strip()
        if not path:
            if not parent or not name:
                self._error(400, "需要 path，或 parent + name")
                return
            path = str(Path(parent) / name)
        elif name and Path(path).name != name:
            pass
        try:
            ws = create_workspace(path, name=name or None, scan=False)
            data_root = _activate_workspace_session(ws, heavy=True, scan=True)
        except (OSError, ValueError) as exc:
            self._error(400, str(exc))
            return
        self._json({
            "ok": True,
            "workspace": ws.info(),
            "dataRoot": data_root,
            "articlesPending": True,
            "scanPending": True,
        })

    def handle_workspace_delete(self) -> None:
        """POST /api/workspace/delete — remove workspace from recents (optional file delete)."""
        data = self._read_json()
        ws_path = (data.get("path") or "").strip()
        if not ws_path:
            self._error(400, "path is required")
            return
        delete_files = bool(data.get("deleteFiles", False))
        try:
            result = destroy_workspace(ws_path, delete_files=delete_files)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        except OSError as exc:
            self._error(500, f"删除失败: {exc}")
            return
        payload: dict = {"ok": True, **result}
        ws = get_active_workspace()
        if ws is not None:
            payload["workspace"] = ws.info()
            if result.get("wasActive"):
                payload["dataRoot"] = _activate_workspace_session(ws)
                payload["articles"] = scan_articles()
        else:
            payload["workspace"] = None
            payload["articles"] = []
        self._json(payload)

    def handle_workspace_info(self) -> None:
        """GET /api/workspace/info — active workspace metadata."""
        ws = get_active_workspace()
        if ws is None:
            self._json({"active": False, "workspace": None, "legacyDataRoot": str(storage.DATA_ROOT)})
            return
        self._json({"active": True, "workspace": ws.info(), "legacyDataRoot": str(storage.DATA_ROOT)})

    def handle_workspace_sources_get(self) -> None:
        """List linked, read-only folders."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        self._json({"sources": ws.sources()})

    def handle_workspace_source_add(self) -> None:
        """Link an existing folder without changing it."""
        data = self._read_json()
        path = str(data.get("path") or "").strip()
        name = str(data.get("name") or "").strip() or None
        if not path:
            self._error(400, "path is required")
            return
        try:
            ws = require_active_workspace()
            source = ws.add_source(path, name=name)
        except (RuntimeError, OSError, ValueError) as exc:
            self._error(400, str(exc))
            return

        def _scan_source() -> None:
            try:
                ws.scan(full=True)
                from workspace_index import rebuild_index

                rebuild_index(ws)
                from workspace_watch import start_workspace_watcher

                start_workspace_watcher(ws)
            except Exception:
                pass

        threading.Thread(target=_scan_source, daemon=True, name="ws-source-scan").start()
        self._json({"ok": True, "source": source, "sources": ws.sources(), "scanPending": True})

    def handle_workspace_source_remove(self) -> None:
        """Unlink source metadata only; never delete source files."""
        data = self._read_json()
        source_id = str(data.get("sourceId") or "").strip()
        try:
            ws = require_active_workspace()
            result = ws.remove_source(source_id)
        except (RuntimeError, OSError, ValueError) as exc:
            self._error(400, str(exc))
            return

        def _refresh_after_remove() -> None:
            try:
                from workspace_index import rebuild_index

                rebuild_index(ws)
                from workspace_watch import start_workspace_watcher

                start_workspace_watcher(ws)
            except Exception:
                pass

        threading.Thread(
            target=_refresh_after_remove,
            daemon=True,
            name="ws-source-remove-refresh",
        ).start()
        self._json({"ok": True, **result, "sources": ws.sources()})

    def handle_workspace_import_managed(self) -> None:
        """Copy a linked file into managed-files/inbox."""
        data = self._read_json()
        rel = str(data.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            self._error(400, "path is required")
            return
        try:
            ws = require_active_workspace()
            result = ws.import_managed_file(rel)
        except (RuntimeError, OSError, ValueError) as exc:
            self._error(400, str(exc))
            return
        self._json({"ok": True, **result})

    def handle_workspace_scan(self) -> None:
        """POST /api/workspace/scan — reconcile sidecars with disk."""
        data = self._read_json() if self.headers.get("Content-Length") else {}
        full = bool((data or {}).get("full", True))
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        stats = ws.scan(full=full)
        if full:
            try:
                from workspace_ingest import start_workspace_ingest

                start_workspace_ingest(ws)
            except Exception:
                pass
        self._json({"ok": True, "stats": stats, "workspace": ws.info()})

    def handle_workspace_documents_get(self) -> None:
        """GET /api/workspace/documents — list workspace documents."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        kind = (qs.get("kind", [""])[0] or "").strip() or None
        query = (qs.get("q", [""])[0] or "").strip() or None
        docs = ws.list_documents(kind=kind, query=query)
        self._json({"documents": docs})

    def handle_workspace_tree_get(self) -> None:
        """GET /api/workspace/tree — filesystem folder/file tree for the active workspace."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        include_derivations = (qs.get("derivations", ["0"])[0] or "0").strip() in ("1", "true", "yes")
        payload = ws.list_directory_tree(include_derivations=include_derivations)
        payload["documents"] = ws.list_documents()
        self._json(payload)

    def handle_workspace_search(self) -> None:
        """GET /api/workspace/search?q= — full-text search in workspace."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from workspace_search import search_workspace_documents

        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        query = (qs.get("q", [""])[0] or "").strip()
        if not query:
            self._error(400, "q is required")
            return
        limit = int(qs.get("limit", ["20"])[0] or 20)
        kind = (qs.get("kind", [""])[0] or "").strip() or None
        hits = search_workspace_documents(ws, query, limit=limit, kind=kind)
        self._json({"query": query, "hits": hits, "count": len(hits)})

    def handle_workspace_preparse(self, doc_id: str) -> None:
        """POST /api/workspace/documents/<doc_id>/preparse — Word → .parsed.md."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from derivations import preparse_word_document

        result = preparse_word_document(ws, doc_id)
        if not result:
            self._error(400, "无法预处理该文档（需为工作空间内的 Word 文件）")
            return
        self._json({"ok": True, "result": result})

    def handle_workspace_bookmarks_get(self) -> None:
        """GET /api/workspace/bookmarks — list URL bookmarks."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from bookmarks import list_bookmarks

        self._json({"bookmarks": list_bookmarks(ws)})

    def handle_workspace_reindex(self) -> None:
        """GET/POST /api/workspace/reindex — rebuild FTS index.db."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from workspace_index import rebuild_index

        stats = rebuild_index(ws)
        self._json({"ok": True, "stats": stats})

    def handle_workspace_bookmarks_create(self) -> None:
        """POST /api/workspace/bookmarks — save a URL bookmark."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from bookmarks import create_bookmark

        data = self._read_json()
        url = (data.get("url") or "").strip()
        title = (data.get("title") or "").strip()
        try:
            record = create_bookmark(ws, url, title=title)
        except ValueError as exc:
            self._error(400, str(exc))
            return
        self._json({"ok": True, "bookmark": record})

    def handle_workspace_bookmark_delete(self, doc_id: str) -> None:
        """DELETE /api/workspace/bookmarks/<doc_id>."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        from bookmarks import delete_bookmark

        if not delete_bookmark(ws, doc_id):
            self._error(404, "bookmark not found")
            return
        self._json({"ok": True})

    def handle_workspace_document_get(self, doc_id: str) -> None:
        """GET /api/workspace/documents/<doc_id> — single document sidecar."""
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        doc = ws.load_document(doc_id)
        if not doc:
            self._error(404, "document not found")
            return
        self._json({"document": doc})

    def handle_workspace_article_derivations(self, article_id: str) -> None:
        """GET /api/workspace/articles/<id>/derivations — legacy article bridge."""
        try:
            ws = require_active_workspace()
            aid = validate_article_id(article_id)
        except (RuntimeError, ValueError) as exc:
            self._error(400, str(exc))
            return
        from derivations import lookup_article_derivations

        result = lookup_article_derivations(ws, aid)
        if not result:
            self._json({"articleId": aid, "derivations": {}, "docId": None})
            return
        self._json(result)

    def handle_workspace_ingest_status(self) -> None:
        """GET /api/workspace/ingest-status — background PDF ingest progress."""
        from workspace_ingest import ingest_status

        self._json(ingest_status())

    def handle_workspace_library_status(self) -> None:
        """GET /api/workspace/library-status — scattered PDF / pending counts."""
        from workspace_ingest import library_status

        try:
            require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        self._json(library_status())

    def handle_workspace_ingest_run(self) -> None:
        """POST /api/workspace/ingest-run — manually trigger PDF ingest."""
        from workspace_ingest import start_workspace_ingest

        data = self._read_json()
        force = bool(data.get("force", False))
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        started = start_workspace_ingest(ws, force=force)
        self._json({"ok": True, "started": started})

    def handle_workspace_organize_preview(self) -> None:
        """GET /api/workspace/organize-preview — dry-run organize plan."""
        from literature_organize import organize_preview

        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        target_dir = (qs.get("targetDir", [""])[0] or "").strip() or None
        try:
            require_active_workspace()
            plan = organize_preview(target_dir=target_dir)
        except (RuntimeError, ValueError) as exc:
            self._error(400, str(exc))
            return
        self._json(plan)

    def handle_workspace_organize_literature(self) -> None:
        """POST /api/workspace/organize-literature — move PDFs into literature/."""
        from literature_organize import organize_literature

        data = self._read_json()
        dry_run = bool(data.get("dryRun", False))
        move = bool(data.get("move", True))
        target_dir = (data.get("targetDir") or "").strip() or None
        try:
            require_active_workspace()
            report = organize_literature(dry_run=dry_run, target_dir=target_dir, move=move)
        except (RuntimeError, ValueError) as exc:
            self._error(400, str(exc))
            return
        if not dry_run:
            payload: dict = {"ok": True, **report, "articles": scan_articles()}
            self._json(payload)
        else:
            self._json(report)

    def handle_workspace_settings_save(self) -> None:
        """POST /api/workspace/settings — update workspace.json flags."""
        data = self._read_json()
        try:
            ws = require_active_workspace()
        except RuntimeError as exc:
            self._error(400, str(exc))
            return
        manifest = ws.load_manifest()
        for key in (
            "ingestOnOpen",
            "autoClassifyPdfs",
            "autoExtractMetadata",
            "classifyUseLlm",
            "literatureDir",
        ):
            if key in data:
                manifest[key] = data[key]
        ws.save_manifest(manifest)
        self._json({"ok": True, "workspace": ws.info()})

    def handle_workspace_file_read(self) -> None:
        """GET/POST: read a text file by workspace-relative path."""
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        rel = (qs.get("path", [""])[0] or "").strip()
        if not rel and self.command == "POST":
            body = self._read_json()
            rel = str(body.get("path") or "").strip()
        if not rel:
            self._error(400, "path required")
            return
        target = resolve_workspace_rel_path(rel, must_exist=True)
        if not target.is_file():
            self._error(404, "Not a file")
            return
        size = target.stat().st_size
        ws = get_active_workspace()
        source_readonly = bool(ws and ws.is_readonly_path(rel))
        if size > TEXT_FILE_MAX_BYTES:
            raw = target.read_bytes()[:TEXT_FILE_MAX_BYTES]
            text = raw.decode("utf-8", errors="replace")
            self._json({
                "path": rel.replace("\\", "/"),
                "content": text,
                "size": size,
                "readonly": True,
                "truncated": True,
            })
            return
        text = target.read_text(encoding="utf-8", errors="replace")
        self._json({
            "path": rel.replace("\\", "/"),
            "content": text,
            "size": size,
            "readonly": source_readonly,
            "truncated": False,
        })

    def handle_workspace_mkdir(self) -> None:
        body = self._read_json()
        rel = str(body.get("path") or body.get("dir") or "").replace("\\", "/").strip("/")
        if not rel:
            self._error(400, "path required")
            return
        require_managed_workspace_path(rel)
        target = resolve_workspace_rel_path(rel, must_exist=False)
        target.mkdir(parents=True, exist_ok=True)
        self._json({"ok": True, "path": rel})

    def handle_workspace_write(self) -> None:
        body = self._read_json()
        rel = str(body.get("path") or "").replace("\\", "/").strip("/")
        content = body.get("content")
        if not rel:
            self._error(400, "path required")
            return
        if content is None:
            self._error(400, "content required")
            return
        target = resolve_save_path(rel)
        if isinstance(content, str) and len(content.encode("utf-8")) > TEXT_FILE_MAX_BYTES:
            self._error(400, "File too large to save via editor")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(content), encoding="utf-8")
        self._json({"ok": True, "path": rel})

    def handle_workspace_rename(self) -> None:
        body = self._read_json()
        src = str(body.get("from") or body.get("src") or "").replace("\\", "/").strip("/")
        dst = str(body.get("to") or body.get("dst") or "").replace("\\", "/").strip("/")
        if not src or not dst:
            self._error(400, "from and to required")
            return
        require_managed_workspace_path(src)
        require_managed_workspace_path(dst)
        src_path = resolve_workspace_rel_path(src, must_exist=True)
        dst_path = resolve_workspace_rel_path(dst, must_exist=False)
        if dst_path.exists():
            self._error(409, "Destination already exists")
            return
        dst_path.parent.mkdir(parents=True, exist_ok=True)
        src_path.rename(dst_path)
        if src.lower().endswith((".md", ".markdown")):
            old_id = note_id_for_workspace_path(src)
            new_id = note_id_for_workspace_path(dst)
            existing = next((n for n in get_all_notes() if n.get("id") == old_id), None)
            if existing:
                delete_note(old_id)
                existing["id"] = new_id
                existing["folder"] = f"path:{dst}"
                existing["title"] = existing.get("title") or Path(dst).stem
                existing["modified_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                upsert_note(existing)
        self._json({"ok": True, "from": src, "to": dst})

    def handle_workspace_delete_path(self) -> None:
        body = self._read_json()
        rel = str(body.get("path") or "").replace("\\", "/").strip("/")
        if not rel:
            self._error(400, "path required")
            return
        require_managed_workspace_path(rel)
        target = resolve_workspace_rel_path(rel, must_exist=True)
        if target.is_dir():
            # Drop SQLite note rows whose path lives under this directory.
            prefix = rel.rstrip("/") + "/"
            for note in list(get_all_notes()):
                folder = str(note.get("folder") or "")
                if folder.startswith("path:"):
                    npath = folder[5:]
                    if npath == rel or npath.startswith(prefix):
                        try:
                            delete_note(note["id"])
                        except Exception:
                            pass
            shutil.rmtree(target)
        else:
            target.unlink()
            if rel.lower().endswith((".md", ".markdown")):
                delete_note(note_id_for_workspace_path(rel))
        self._json({"ok": True, "path": rel})

    def handle_skills_install(self) -> None:
        """POST /api/skills/install — install tools + skill file to target directory."""
        data = self._read_json()
        target = str(data.get("target") or "").strip().lower()
        directory = str(data.get("directory") or "").strip()
        preferences = str(data.get("preferences") or "").strip()

        # Resolve target directory
        home = Path.home()
        targets = {
            "claude_global": home / ".claude" / "skills",
            "claude_project": Path.cwd() / ".claude" / "skills",
            "codex_global": home / ".codex" / "skills",
            "custom": Path(directory) if directory else None,
        }
        install_dir = targets.get(target)
        if not install_dir or (target == "custom" and not directory):
            self._error(400, "Invalid target. Choices: claude_global, claude_project, codex_global, custom")
            return
        if target == "custom" and not directory:
            self._error(400, "directory is required for custom target")
            return

        try:
            install_dir = Path(install_dir)
            install_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._error(500, f"Cannot create directory: {exc}")
            return

        # Build skill file content
        tools_dir = PACKAGE_DIR / "tools"
        tool_list: list[str] = []
        if tools_dir.is_dir():
            for f in sorted(tools_dir.iterdir()):
                if f.suffix == ".py" and f.name != "__init__.py" and f.name != "_client.py":
                    tool_list.append(f.name)

        prefix = "# KBase Skill\n\n"
        prefix += "KBase is a local-first knowledge management app running at http://localhost:8765.\n"
        prefix += "Server must be running before using these tools.\n\n"
        prefix += "## Available Tools\n\n"
        for name in tool_list:
            prefix += f"- `python kb/tools/{name}`\n"
        prefix += "\nEach tool takes `--arg` parameters and returns JSON to stdout.\n\n"
        prefix += "## Workflows\n\n"
        prefix += "### Read a paper and take notes\n"
        prefix += "1. `python kb/tools/search_articles.py --query \"topic\"`\n"
        prefix += "2. `python kb/tools/get_article.py --id \"found_id\"`\n"
        prefix += "3. `python kb/tools/create_note.py --title \"Summary\" --content \"...\"`\n\n"

        if preferences:
            prefix += "## User Preferences\n\n"
            prefix += preferences.strip() + "\n"

        # Write skill file
        skill_path = install_dir / "kbase.md"
        try:
            skill_path.write_text(prefix, encoding="utf-8")
        except OSError as exc:
            self._error(500, f"Cannot write skill file: {exc}")
            return

        # Copy tools to a sub-directory
        tools_target = install_dir / "kbase-tools"
        tools_target.mkdir(parents=True, exist_ok=True)
        try:
            import shutil
            for f in sorted(tools_dir.iterdir()):
                if f.suffix == ".py":
                    shutil.copy2(f, tools_target / f.name)
        except OSError as exc:
            self._error(500, f"Cannot copy tools: {exc}")
            return

        self._json({
            "status": "ok",
            "message": f"Skills installed to {install_dir}",
            "directory": str(install_dir),
            "files": len(tool_list) + 1,
        })

    def handle_skills_preview(self) -> None:
        """GET /api/skills/preview — preview the skill file content."""
        tools_dir = PACKAGE_DIR / "tools"
        tool_list: list[str] = []
        if tools_dir.is_dir():
            for f in sorted(tools_dir.iterdir()):
                if f.suffix == ".py" and f.name != "__init__.py" and f.name != "_client.py":
                    tool_list.append(f.name)

        prefix = "# KBase Skill\n\n"
        prefix += "KBase is a local-first knowledge management app running at http://localhost:8765.\n\n"
        prefix += "## Available Tools\n\n"
        for name in tool_list:
            prefix += f"- `python kb/tools/{name}`\n"

        self._json({
            "content": prefix,
            "tool_count": len(tool_list),
        })




# ---------------------------------------------------------------------------
# Note helpers
# ---------------------------------------------------------------------------


def validate_note_id(note_id: str) -> str:
    note_id = str(note_id or "").strip()
    if not note_id:
        raise ValueError("Note id is required")
    # Placeholder used by path-based saves (body.path is authoritative).
    if note_id == "workspace_note":
        return note_id
    path = Path(note_id)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or any(ch in INVALID_ARTICLE_CHARS or ord(ch) < 32 for ch in note_id)
        or note_id in {".", ".."}
    ):
        raise ValueError("Invalid note id")
    return note_id


def note_file_for(note_id: str) -> Path:
    note_id = validate_note_id(note_id)
    storage.NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return storage.NOTES_DIR / f"{note_id}.md"


# ---------------------------------------------------------------------------
# Reference import and duplicate helpers
# ---------------------------------------------------------------------------


def _normalize_reference_doi(value) -> str:
    doi = _clean_bib_value(value).lower()
    doi = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", doi)
    return doi.strip().rstrip(".,;)")


def _normalize_reference_title(value) -> str:
    return re.sub(r"[\W_]+", "", _clean_bib_value(value).casefold(), flags=re.UNICODE)


def _reference_year(value) -> str:
    match = re.search(r"(?:18|19|20|21)\d{2}", _clean_bib_value(value))
    return match.group(0) if match else ""


def _reference_match_keys(item: dict) -> list[tuple[str, str]]:
    keys: list[tuple[str, str]] = []
    doi = _normalize_reference_doi(item.get("doi"))
    if doi:
        keys.append((f"doi:{doi}", "DOI 相同"))
    title = _normalize_reference_title(item.get("title"))
    year = _reference_year(item.get("year"))
    if len(title) >= 8 and year:
        keys.append((f"title:{title}|{year}", "标题和年份相同"))
    return keys


def _find_duplicate_article_groups(articles: list[dict]) -> list[dict]:
    """Group likely duplicate references without deleting or merging files."""
    parent = list(range(len(articles)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    matches: dict[str, list[int]] = {}
    labels: dict[str, str] = {}
    for index, article in enumerate(articles):
        for key, label in _reference_match_keys(article):
            matches.setdefault(key, []).append(index)
            labels[key] = label
    for indexes in matches.values():
        for index in indexes[1:]:
            union(indexes[0], index)

    grouped: dict[int, list[int]] = {}
    for index in range(len(articles)):
        grouped.setdefault(find(index), []).append(index)
    reasons: dict[int, set[str]] = {}
    for key, indexes in matches.items():
        if len(indexes) > 1:
            reasons.setdefault(find(indexes[0]), set()).add(labels[key])

    result = []
    for root, indexes in grouped.items():
        if len(indexes) < 2:
            continue
        items = []
        for index in indexes:
            article = articles[index]
            items.append({
                "id": article.get("id"),
                "title": article.get("title") or article.get("id"),
                "authors": article.get("authors") or _split_authors(article.get("author")),
                "year": article.get("year") or "",
                "doi": article.get("doi") or "",
                "venue": article.get("venue") or "",
            })
        items.sort(key=lambda item: (_clean_bib_value(item.get("title")).casefold(), str(item.get("id"))))
        result.append({"reasons": sorted(reasons.get(root) or {"元数据相似"}), "items": items})
    result.sort(key=lambda group: _clean_bib_value(group["items"][0].get("title")).casefold())
    return result


def _first_reference_value(fields: dict[str, list[str]], *names: str) -> str:
    for name in names:
        values = fields.get(name) or []
        if values and _clean_bib_value(values[0]):
            return _clean_bib_value(values[0])
    return ""


def _parse_ris_records(text: str) -> list[dict]:
    raw_records: list[dict[str, list[str]]] = []
    fields: dict[str, list[str]] = {}
    last_tag = ""
    for raw_line in text.splitlines():
        match = re.match(r"^([A-Z0-9]{2})\s{0,2}-\s?(.*)$", raw_line.rstrip())
        if not match:
            continuation = raw_line.strip()
            if continuation and last_tag and fields.get(last_tag):
                fields[last_tag][-1] = f"{fields[last_tag][-1]} {continuation}".strip()
            continue
        tag, value = match.group(1), match.group(2).strip()
        if tag == "TY" and fields:
            raw_records.append(fields)
            fields = {}
        if tag == "ER":
            if fields:
                raw_records.append(fields)
            fields, last_tag = {}, ""
            continue
        fields.setdefault(tag, []).append(value)
        last_tag = tag
    if fields:
        raw_records.append(fields)

    records = []
    for item in raw_records:
        title = _first_reference_value(item, "TI", "T1", "CT", "BT")
        authors = [
            _clean_bib_value(author)
            for author in [*(item.get("AU") or []), *(item.get("A1") or [])]
            if _clean_bib_value(author)
        ]
        records.append({
            "title": title,
            "authors": authors,
            "year": _reference_year(_first_reference_value(item, "PY", "Y1", "DA")),
            "venue": _first_reference_value(item, "JO", "JF", "T2", "JA", "PB"),
            "doi": _normalize_reference_doi(_first_reference_value(item, "DO")),
            "abstract": _first_reference_value(item, "AB", "N2"),
            "tags": [_clean_bib_value(tag) for tag in item.get("KW") or [] if _clean_bib_value(tag)],
            "url": _first_reference_value(item, "UR", "L1"),
            "category": _first_reference_value(item, "TY"),
            "kind": "paper",
        })
    return records


def _unbrace_bib_value(value: str) -> str:
    value = value.strip()
    while len(value) >= 2 and value[0] == "{" and value[-1] == "}":
        value = value[1:-1].strip()
    value = re.sub(r"\\([{}_%&#])", r"\1", value)
    return _clean_bib_value(value.replace("{", "").replace("}", ""))


def _parse_bibtex_fields(body: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    index = 0
    while index < len(body):
        while index < len(body) and (body[index].isspace() or body[index] == ","):
            index += 1
        name_match = re.match(r"[A-Za-z][\w-]*", body[index:])
        if not name_match:
            break
        name = name_match.group(0).lower()
        index += len(name_match.group(0))
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body) or body[index] != "=":
            break
        index += 1
        while index < len(body) and body[index].isspace():
            index += 1
        if index >= len(body):
            fields[name] = ""
            break
        if body[index] == "{":
            start, depth = index + 1, 1
            index += 1
            while index < len(body) and depth:
                if body[index] == "{" and body[index - 1] != "\\":
                    depth += 1
                elif body[index] == "}" and body[index - 1] != "\\":
                    depth -= 1
                index += 1
            value = body[start:index - 1] if depth == 0 else body[start:]
        elif body[index] == '"':
            start = index + 1
            index += 1
            while index < len(body):
                if body[index] == '"' and body[index - 1] != "\\":
                    break
                index += 1
            value = body[start:index]
            index = min(index + 1, len(body))
        else:
            start = index
            while index < len(body) and body[index] != ",":
                index += 1
            value = body[start:index]
        fields[name] = _unbrace_bib_value(value)
    return fields


def _parse_bibtex_records(text: str) -> list[dict]:
    records = []
    entry_re = re.compile(r"@([A-Za-z]+)\s*([({])")
    position = 0
    while True:
        match = entry_re.search(text, position)
        if not match:
            break
        entry_type = match.group(1).lower()
        opening = match.group(2)
        closing = "}" if opening == "{" else ")"
        index, depth, quoted = match.end(), 1, False
        while index < len(text) and depth:
            char = text[index]
            escaped = index > 0 and text[index - 1] == "\\"
            if char == '"' and not escaped:
                quoted = not quoted
            elif not quoted and not escaped:
                if char == opening:
                    depth += 1
                elif char == closing:
                    depth -= 1
            index += 1
        content = text[match.end():index - 1] if depth == 0 else text[match.end():]
        position = max(index, match.end())
        if entry_type in {"comment", "preamble", "string"} or "," not in content:
            continue
        fields = _parse_bibtex_fields(content.split(",", 1)[1])
        authors = [
            _unbrace_bib_value(author)
            for author in re.split(r"\s+and\s+", fields.get("author", ""), flags=re.I)
            if _unbrace_bib_value(author)
        ]
        tags = [tag.strip() for tag in re.split(r"\s*[;,]\s*", fields.get("keywords", "")) if tag.strip()]
        records.append({
            "title": fields.get("title", ""),
            "authors": authors,
            "year": _reference_year(fields.get("year") or fields.get("date")),
            "venue": fields.get("journal") or fields.get("booktitle") or fields.get("publisher") or fields.get("school") or "",
            "doi": _normalize_reference_doi(fields.get("doi")),
            "abstract": fields.get("abstract", ""),
            "tags": tags,
            "url": fields.get("url", ""),
            "category": entry_type,
            "kind": "paper",
        })
    return records


def _creator_name(creator: dict) -> str:
    if not isinstance(creator, dict):
        return ""
    if creator.get("literal") or creator.get("name"):
        return _clean_bib_value(creator.get("literal") or creator.get("name"))
    return _clean_bib_value(" ".join(
        str(creator.get(key) or "").strip()
        for key in ("given", "firstName", "family", "lastName")
        if creator.get(key)
    ))


def _parse_json_reference_records(text: str) -> list[dict]:
    payload = json.loads(text)
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        payload = payload["items"]
    elif isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("JSON reference import must contain an object or array")
    records = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        creators = item.get("author") if isinstance(item.get("author"), list) else item.get("creators") or []
        authors = [name for name in (_creator_name(creator) for creator in creators) if name]
        issued = item.get("issued") or {}
        date_parts = issued.get("date-parts") if isinstance(issued, dict) else []
        issued_year = date_parts[0][0] if date_parts and isinstance(date_parts[0], list) and date_parts[0] else ""
        tags_value = item.get("tags") or item.get("keyword") or []
        if isinstance(tags_value, str):
            tags = [tag.strip() for tag in re.split(r"\s*[;,]\s*", tags_value) if tag.strip()]
        else:
            tags = [
                _clean_bib_value(tag.get("tag") if isinstance(tag, dict) else tag)
                for tag in tags_value if _clean_bib_value(tag.get("tag") if isinstance(tag, dict) else tag)
            ]
        venue = item.get("container-title") or item.get("publicationTitle") or item.get("publisher") or ""
        if isinstance(venue, list):
            venue = venue[0] if venue else ""
        records.append({
            "title": _clean_bib_value(item.get("title")),
            "authors": authors,
            "year": _reference_year(issued_year or item.get("date") or item.get("year")),
            "venue": _clean_bib_value(venue),
            "doi": _normalize_reference_doi(item.get("DOI") or item.get("doi")),
            "abstract": _clean_bib_value(item.get("abstract") or item.get("abstractNote")),
            "tags": tags,
            "url": _clean_bib_value(item.get("URL") or item.get("url")),
            "category": _clean_bib_value(item.get("type") or item.get("itemType")),
            "kind": "paper",
        })
    return records


def _parse_reference_records(text: str, fmt: str, filename: str) -> tuple[str, list[dict]]:
    aliases = {
        "ris": "ris", "bib": "bibtex", "bibtex": "bibtex",
        "json": "json", "csl": "json", "csljson": "json", "zotero": "json",
    }
    detected = aliases.get(fmt.lower().lstrip("."), "")
    safe_filename = filename.replace("\\", "/").rsplit("/", 1)[-1].lower()
    if not detected:
        if safe_filename.endswith(".ris"):
            detected = "ris"
        elif safe_filename.endswith(".bib"):
            detected = "bibtex"
        elif safe_filename.endswith(".json"):
            detected = "json"
    if not detected:
        stripped = text.lstrip()
        if re.search(r"^TY\s{0,2}-", stripped, flags=re.M):
            detected = "ris"
        elif stripped.startswith("@"):
            detected = "bibtex"
        elif stripped.startswith(("[", "{")):
            detected = "json"
    if detected == "ris":
        records = _parse_ris_records(text)
    elif detected == "bibtex":
        records = _parse_bibtex_records(text)
    elif detected == "json":
        records = _parse_json_reference_records(text)
    else:
        raise ValueError("Unsupported reference format; use RIS, BibTeX, or CSL/Zotero JSON")
    if len(records) > 5000:
        raise ValueError("A single import is limited to 5000 references")
    return detected, records


def _import_reference_records(text: str, *, fmt: str, filename: str, skip_duplicates: bool) -> dict:
    detected, records = _parse_reference_records(text, fmt, filename)
    existing_keys = {
        key
        for article in get_all_articles()
        for key, _label in _reference_match_keys(article)
    }
    source_name = filename.replace("\\", "/").rsplit("/", 1)[-1][:255]
    imported: list[dict] = []
    skipped = 0
    errors: list[dict] = []
    for index, record in enumerate(records, start=1):
        title = _clean_bib_value(record.get("title"))
        if not title:
            errors.append({"record": index, "error": "missing title"})
            continue
        record_keys = {key for key, _label in _reference_match_keys(record)}
        if skip_duplicates and record_keys.intersection(existing_keys):
            skipped += 1
            continue
        authors = [
            _clean_bib_value(author)
            for author in record.get("authors") or []
            if _clean_bib_value(author)
        ]
        article_id = f"ref_{uuid.uuid4().hex[:16]}"
        while get_article(article_id) is not None:
            article_id = f"ref_{uuid.uuid4().hex[:16]}"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        tags = list(dict.fromkeys(
            _clean_bib_value(tag) for tag in record.get("tags") or [] if _clean_bib_value(tag)
        ))
        info = {
            "title": title,
            "author": "; ".join(authors),
            "authors": authors,
            "doi": _normalize_reference_doi(record.get("doi")),
            "year": _reference_year(record.get("year")),
            "venue": _clean_bib_value(record.get("venue")),
            "abstract": _clean_bib_value(record.get("abstract")),
            "category": _clean_bib_value(record.get("category")),
            "tags": tags,
            "url": _clean_bib_value(record.get("url")),
            "document_kind": record.get("kind") or "paper",
            "import_file": source_name,
            "extracted_at": now,
            "extraction_reason": f"import:{detected}",
        }
        article = {
            "id": article_id,
            "title": title,
            "author": info["author"],
            "authors": authors,
            "pages": 0,
            "date_added": now,
            "category": info["category"],
            "doi": info["doi"],
            "year": info["year"],
            "venue": info["venue"],
            "abstract": info["abstract"],
            "translated": False,
            "summarized": False,
            "pdf_available": False,
            "md_available": False,
            "file_available": False,
            "converting": False,
            "source_filename": "",
            "kind": info["document_kind"],
            "metadata_extracted": True,
            "metadata_extracted_at": now,
            "metadata_source": f"import:{detected}",
            "parser": detected,
            "preparse_error": "",
            "tags": tags,
        }
        folder = article_dir_for(article_id)
        folder.mkdir(parents=True, exist_ok=False)
        try:
            storage._atomic_write_json(folder / f"{article_id}_info.json", info)
            upsert_article(article)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        existing_keys.update(record_keys)
        imported.append({
            "id": article_id, "title": title, "year": info["year"], "doi": info["doi"]
        })
    return {
        "format": detected,
        "parsed": len(records),
        "imported": len(imported),
        "skipped": skipped,
        "errors": errors,
        "articles": imported,
    }


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------


def _clean_bib_value(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _escape_bib_value(value):
    return _clean_bib_value(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _split_authors(value):
    return [item.strip() for item in re.split(r"\s*(?:;|,|\band\b|和)\s*", _clean_bib_value(value), flags=re.I) if item.strip()]


def _bib_key_part(value, fallback="kbase"):
    value = re.sub(r"[^\w\s:-]+", "", _clean_bib_value(value), flags=re.UNICODE)
    value = re.sub(r"\s+", "", value)
    return (value or fallback)[:32]


def _article_bib_key(article, used):
    authors = article.get("authors") if isinstance(article.get("authors"), list) else []
    if not authors:
        authors = _split_authors(article.get("author", ""))
    author_token = (authors[0] if authors else article.get("author") or "kbase").split()
    author_part = _bib_key_part(author_token[-1] if author_token else "kbase", "kbase")
    year_match = re.search(r"\d{4}", str(article.get("year") or article.get("date_added") or ""))
    year_part = _bib_key_part(article.get("year") or (year_match.group(0) if year_match else "nd"), "nd")
    title_part = _bib_key_part((_clean_bib_value(article.get("title")) or article.get("id") or "item").split()[0], article.get("id") or "item")
    base = f"{author_part}{year_part}{title_part}"
    key = base
    suffix = 2
    while key in used:
        key = f"{base}{suffix}"
        suffix += 1
    used.add(key)
    return key


def _article_to_bibtex(article, used):
    entry_type = "article" if (article.get("kind") or "paper") == "paper" else "misc"
    authors = article.get("authors") if isinstance(article.get("authors"), list) else []
    if not authors:
        authors = _split_authors(article.get("author", ""))
    fields = [
        ("title", article.get("title") or article.get("source_filename") or article.get("id")),
        ("author", " and ".join(_escape_bib_value(a) for a in authors if _clean_bib_value(a))),
        ("year", article.get("year")),
        ("journal" if entry_type == "article" else "howpublished", article.get("venue")),
        ("doi", article.get("doi")),
        ("keywords", ", ".join(article.get("tags") or []) if isinstance(article.get("tags"), list) else ""),
        ("note", "; ".join(
            item for item in [
                f"Source file: {article.get('source_filename')}" if article.get("source_filename") else "",
                f"KBase ID: {article.get('id')}" if article.get("id") else "",
            ] if item
        )),
    ]
    body = [f"  {key} = {{{_escape_bib_value(value)}}}" for key, value in fields if _clean_bib_value(value)]
    return f"@{entry_type}{{{_article_bib_key(article, used)},\n" + ",\n".join(body) + "\n}"


def _article_to_ris(article) -> str:
    authors = article.get("authors") if isinstance(article.get("authors"), list) else []
    if not authors:
        authors = _split_authors(article.get("author", ""))
    lines = ["TY  - JOUR" if (article.get("kind") or "paper") == "paper" else "TY  - GEN"]
    fields = [
        ("TI", article.get("title") or article.get("source_filename") or article.get("id")),
        *[("AU", author) for author in authors],
        ("PY", _reference_year(article.get("year"))),
        ("JF", article.get("venue")),
        ("DO", _normalize_reference_doi(article.get("doi"))),
        ("AB", article.get("abstract")),
        *[("KW", tag) for tag in (article.get("tags") or [])],
    ]
    for tag, value in fields:
        cleaned = _clean_bib_value(value)
        if cleaned:
            lines.append(f"{tag}  - {cleaned}")
    lines.extend(["ER  -", ""])
    return "\n".join(lines)


def _article_to_csl_json(article) -> dict:
    authors = article.get("authors") if isinstance(article.get("authors"), list) else []
    if not authors:
        authors = _split_authors(article.get("author", ""))
    value = {
        "id": article.get("id"),
        "type": "article-journal" if (article.get("kind") or "paper") == "paper" else "document",
        "title": article.get("title") or article.get("source_filename") or article.get("id"),
        "author": [{"literal": author} for author in authors if _clean_bib_value(author)],
        "container-title": article.get("venue") or "",
        "DOI": _normalize_reference_doi(article.get("doi")),
        "abstract": article.get("abstract") or "",
        "keyword": ", ".join(article.get("tags") or []),
    }
    year = _reference_year(article.get("year"))
    if year:
        value["issued"] = {"date-parts": [[int(year)]]}
    return {key: item for key, item in value.items() if item not in ("", [], None)}


def _export_stem(article):
    raw = article.get("title") or article.get("source_filename") or article.get("id") or "kbase_item"
    return sanitize_article_id(raw)[:80] or str(article.get("id") or "kbase_item")


def _unique_archive_name(name, used):
    base = Path(name).stem
    suffix = Path(name).suffix
    candidate = name
    index = 2
    while candidate in used:
        candidate = f"{base}_{index}{suffix}"
        index += 1
    used.add(candidate)
    return candidate

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


PACKAGE_DIR = Path(__file__).resolve().parent


def _activate_workspace_session(ws, *, heavy: bool = True, scan: bool = False) -> dict:
    """Bind workspace root as runtime data root and optionally refresh indexes."""
    from storage import bind_data_root_runtime

    info = bind_data_root_runtime(ws.root, literature_dir=ws.literature_dir_name())
    if not heavy:
        return info

    def _bg() -> None:
        if scan:
            try:
                ws.scan(full=True)
            except Exception:
                pass
        try:
            from workspace_index import rebuild_index

            rebuild_index(ws)
        except Exception:
            pass
        try:
            from workspace_watch import start_workspace_watcher

            start_workspace_watcher(ws)
        except Exception:
            pass
        try:
            from workspace_ingest import start_workspace_ingest

            start_workspace_ingest(ws)
        except Exception:
            pass

    threading.Thread(target=_bg, daemon=True, name="ws-activate").start()
    return info


def _bootstrap_workspace() -> None:
    """Open the last (or default) workspace and bind it as the data root.

    Heavy scan/index/ingest runs in a background thread so HTTP can listen
    immediately (Baidu Sync / large trees otherwise block startup for minutes).
    """
    from app_config import get_last_workspace_path

    try:
        last = get_last_workspace_path()
        ws_path = Path(last) if last else storage.DATA_ROOT
        if not ws_path.is_dir():
            ws_path = storage.DATA_ROOT
        # Bind first without a full filesystem scan so the server can start.
        ws = open_workspace(ws_path, scan=False)
        _activate_workspace_session(ws, heavy=True, scan=True)
        print(f" Workspace: {ws.root}")
    except Exception as exc:  # noqa: BLE001
        print(f" Workspace bootstrap skipped: {exc}")


def start_server() -> ReusableThreadingTCPServer:
    ensure_directories()
    load_local_env()
    print(" Knowledge Base Server")
    _bootstrap_workspace()
    print(f" Data root: {storage.DATA_ROOT}")
    print(f" Database:  {storage.DB_PATH}")
    articles = get_all_articles()
    notes = get_all_notes()
    print(f" Articles: {len(articles)} (legacy)")
    print(f" Notes:    {len(notes)} (legacy)")
    print(f" Listening on 0.0.0.0:{PORT}  (reachable as http://localhost:{PORT} from this machine,")
    print(f"                  and as http://<lan-ip>:{PORT} from other devices on the same network)")
    print(f" [!] No authentication — anyone on your local network can read & modify the library.")
    print(f"    Restrict via your OS firewall, or use a reverse proxy with auth.")

    httpd = ReusableThreadingTCPServer(("", PORT), KBHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


if __name__ == "__main__":
    httpd = start_server()
    print(" Press Ctrl+C to stop")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n Server stopped")
        httpd.shutdown()
