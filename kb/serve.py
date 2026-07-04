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
import zipfile
from pathlib import Path

import storage
from storage import (
    ARTICLES_DIR,
    DATA_ROOT,
    DB_PATH,
    KBASE_DIR,
    LOCAL_ENV,
    LLM_CONFIG_FILE,
    LOW_MEMORY_CONFIG,
    NOTES_DIR,
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
    create_database,
    delete_column,
    delete_database,
    delete_row,
    delete_view,
    list_databases,
    load_database,
    public_field_types,
    render_database,
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
ALLOWED_SAVE_SUFFIXES = {".md", ".json", ".txt"}
_LOG_LOCK = threading.Lock()
_CHAT_LOCK = threading.Lock()

# Ensure the runtime layout exists on first import.
ensure_directories()
load_local_env()


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
    base = ARTICLES_DIR.resolve()
    target = (ARTICLES_DIR / article_id).resolve()
    if target == base or not _is_inside(target, base):
        raise ValueError("Invalid article path")
    return target


def article_id_from_request_path(request_path: str) -> str:
    path = urllib.parse.urlsplit(request_path).path.rstrip("/")
    return validate_article_id(urllib.parse.unquote(path.rsplit("/", 1)[-1]))


def resolve_save_path(filepath: str) -> Path:
    """Allow saving only inside the article folders or notes folder, with
    restricted filename suffix. The file must end with .md / .json / .txt and
    must be a single component inside the article or notes directory.
    """
    rel = Path(str(filepath or ""))
    if (
        rel.is_absolute()
        or rel.drive
        or rel.anchor
        or any(part == ".." for part in rel.parts)
        or not rel.parts
    ):
        raise ValueError("Invalid save path")

    articles_root = ARTICLES_DIR.resolve()
    notes_root = NOTES_DIR.resolve()

    target = (DATA_ROOT / rel).resolve()
    if _is_inside(target, articles_root) and target != articles_root:
        if target.suffix.lower() not in ALLOWED_SAVE_SUFFIXES:
            raise ValueError("Saving into article folders is only allowed for .md/.json/.txt files")
        return target
    if _is_inside(target, notes_root) and target != notes_root:
        if target.suffix.lower() not in ALLOWED_SAVE_SUFFIXES:
            raise ValueError("Saving into notes is only allowed for .md/.json/.txt files")
        return target
    raise ValueError("Saving is only allowed for article files and notes")


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
    with get_conn() as conn:
        existing = {
            row["id"]: dict(row)
            for row in conn.execute("SELECT * FROM articles").fetchall()
        }

        for folder in sorted(ARTICLES_DIR.iterdir()) if ARTICLES_DIR.exists() else []:
            if not folder.is_dir():
                continue
            aid = folder.name
            article = existing.pop(aid, None)
            trans_exists = (folder / f"{aid}_translated.md").exists()
            summary_exists = (folder / f"{aid}_summary.md").exists()
            pdf_exists = (folder / "original.pdf").exists()
            md_exists = (folder / f"{aid}.md").exists()

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
                    article["tags"] = tags[:8]

            # Page count from meta
            if meta.get("page_stats") and not article.get("pages"):
                article["pages"] = len(meta["page_stats"])

            upsert_article(article)

        # Any leftover rows whose article folder is gone: drop them.
        for aid in list(existing.keys()):
            if not (ARTICLES_DIR / aid).exists():
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
    folder = KBASE_DIR / "logs" / article_id
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

        # Snapshot versioned copy
        article_dir = article_dir_for(article_id)
        md_file = article_dir / f"{article_id}.md"
        versioned = article_dir / f"{article_id}_{engine_name}.md"
        if md_file.exists():
            try:
                shutil.copy2(md_file, versioned)
            except OSError:
                pass
            record_article_history_safe(article_id, engine_name, versioned)

        # Drop outdated derived files. Translated goes to *_translated_old.md.
        for suffix in ("_calibrated.md", "_translated.md", "_summary.md"):
            derived = article_dir / f"{article_id}{suffix}"
            if not derived.exists():
                continue
            try:
                if suffix == "_translated.md":
                    shutil.move(str(derived), str(article_dir / f"{article_id}_translated_old.md"))
                else:
                    derived.unlink()
            except OSError as exc:
                log(f"Failed to handle {derived}: {exc}")

        record_conversion(article_id, engine_name, "success")
        set_conv_status(article_id, "conversion", "done", "解析完成")
        update_article_fields(article_id, {
            "md_available": md_file.exists(),
            "parser": engine_name,
        })
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
                self._json({"articles": scan_articles()})
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
            elif path == "/api/notes":
                self._json({"notes": get_all_notes()})
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
                self._json(check_for_update())
            elif path == "/api/data-root":
                self._json(get_data_root_info())
            elif path == "/api/databases":
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
        if LOW_MEMORY_CONFIG.exists():
            try:
                runtime = json.loads(LOW_MEMORY_CONFIG.read_text(encoding="utf-8"))
            except Exception:
                runtime = {}
        for key in ("DOCPARSER_API_URL", "DOCPARSER_ENGINE"):
            env_val = os.environ.get(key)
            if env_val:
                runtime[key] = env_val
        return runtime

    def serve_static(self, path: str) -> None:
        # Translate URL path to filesystem path under data/ or package dir.
        relative = path.lstrip("/")
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
                    target = ARTICLES_DIR / aid / safe_name
                else:
                    self._error(404, "Asset not found")
                    return
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
            else:
                self._error(404, "Not found")
        except ValueError as exc:
            self._error(400, str(exc))
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
        if export_format not in {"bibtex", "pdf", "markdown"}:
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
        md_path = note_file_for(note_id)
        if not md_path.exists():
            self._error(404, "Note not found")
            return
        content = md_path.read_text(encoding="utf-8")
        meta = next((n for n in get_all_notes() if n["id"] == note_id), None)
        self._json({"id": note_id, "content": content, "meta": meta})

    def handle_get_note_blocks(self):
        note_id = self._note_id_from_path()
        # Verify the note exists.
        md_path = note_file_for(note_id)
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
        if sub:
            self._error(404, "Not found")
            return
        qs = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        if qs.get("render", [""])[0] in ("1", "true", "yes"):
            view_id = qs.get("view", [""])[0] or None
            self._json(render_database(db_id, view_id or None))
            return
        self._json(load_database(db_id))

    def handle_create_database(self):
        body = self._read_json()
        name = str(body.get("name") or "Untitled").strip()[:120] or "Untitled"
        self._json(create_database(name))

    def handle_database_post(self):
        db_id, sub, _sub_id = _database_path_parts(self.path)
        if not db_id:
            self._error(404, "Not found")
            return
        validate_database_id(db_id)
        body = self._read_json()
        if sub == "rows":
            row = add_row(db_id, body.get("cells") if isinstance(body.get("cells"), dict) else None)
            self._json(row)
            return
        if sub == "columns":
            col = add_column(
                db_id,
                str(body.get("name") or "新列"),
                str(body.get("type") or "text"),
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
            )
            self._json(view)
            return
        self._error(404, "Not found")

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
        body = self._read_json()
        name = str(body.get("name") or "").strip()[:100]
        if not name:
            self._error(400, "Notebook name is required")
            return
        nb = {
            "name": name,
            "icon": str(body.get("icon") or "📓")[:16],
            "sort_order": int(body.get("sort_order") or 0),
        }
        upsert_notebook(nb)
        self._json({"status": "ok", "notebooks": list_notebooks()})

    def handle_update_notebook(self):
        nb_id = urllib.parse.unquote(self.path.rstrip("/").rsplit("/", 1)[-1])
        body = self._read_json()
        nb = dict(body)
        nb["id"] = nb_id
        upsert_notebook(nb)
        self._json({"status": "ok", "notebooks": list_notebooks()})

    def handle_delete_notebook(self):
        nb_id = urllib.parse.unquote(self.path.rstrip("/").rsplit("/", 1)[-1])
        delete_notebook(nb_id)
        self._json({"status": "ok", "notebooks": list_notebooks()})

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
        title = str(body.get("title") or "Untitled").strip()[:200]
        folder = str(body.get("folder") or "").strip()[:200]
        # Article-scoped notes (a.k.a. "文章小记") get a stable
        # slug-based id so the same article always re-opens the
        # same note file. The id is `<article_id>__<slug>`. Free
        # notebook notes get the usual timestamp-based id.
        article_id = str(body.get("article_id") or "").strip()[:128] or None
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(body.get("slug") or title).strip())[:80] or "note"
        if article_id:
            note_id = f"art_{article_id}__{slug}"
        else:
            ts = time.strftime("%Y%m%d_%H%M%S")
            uid = os.urandom(4).hex()
            note_id = f"note_{ts}_{uid}"
        md_path = note_file_for(note_id)
        if not md_path.exists():
            md_path.write_text(f"# {title}\n\n", encoding="utf-8")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": note_id,
            "title": title,
            "created_at": now,
            "modified_at": now,
            "tags": [],
            "folder": folder,
            "notebook_id": str(body.get("notebook_id") or "").strip()[:64] or None,
            "parent_id": body.get("parent_id") or None,
            "article_id": article_id,
            "links": [],
        }
        upsert_note(entry)
        self._json(entry)

    def handle_save_note(self):
        note_id = self._note_id_from_path()
        body = self._read_json()
        content = str(body.get("content") or "")
        title = str(body.get("title") or "").strip()[:200]
        md_path = note_file_for(note_id)
        if not md_path.exists():
            self._error(404, "Note not found")
            return
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        existing = next((n for n in get_all_notes() if n["id"] == note_id), {"id": note_id})
        existing["title"] = title or existing.get("title", "")
        existing["modified_at"] = now
        if "tags" in body:
            tags = body["tags"]
            existing["tags"] = [str(t).strip()[:50] for t in tags if str(t).strip()] if isinstance(tags, list) else []
        if "folder" in body:
            existing["folder"] = str(body.get("folder") or "").strip()[:200]
        if "parent_id" in body:
            new_parent = body.get("parent_id") or None
            if new_parent and _is_note_ancestor(note_id, new_parent):
                self._error(400, "Cannot move note under its descendant")
                return
            existing["parent_id"] = new_parent
        if "doc_icon" in body:
            existing["doc_icon"] = str(body.get("doc_icon") or "").strip()[:16]
        if "article_id" in body:
            existing["article_id"] = (str(body.get("article_id") or "").strip()[:128] or None)
        if "notebook_id" in body:
            existing["notebook_id"] = str(body.get("notebook_id") or "").strip()[:64] or None
        existing.setdefault("created_at", now)
        upsert_note(existing)
        if "content" in body:
            try:
                # Rebuild block anchors. We then rewrite the saved file
                # with stable `<!--kb-block:anchor-->` markers so the
                # frontend can map the rendered DOM back to anchors.
                rows = sync_note_blocks(note_id, content)
                annotated = inject_block_anchors(content, rows)
                if annotated != content:
                    md_path.write_text(annotated, encoding="utf-8")
                # Build the cross-note link index used by the backlinks
                # panel.
                sync_note_links(note_id, annotated)
            except Exception as exc:  # noqa: BLE001
                print(f"sync_note_blocks/links failed for {note_id}: {exc}")
        # Return the annotated content so the client cache stays
        # in sync with disk.
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                saved = f.read()
        except OSError:
            saved = content if "content" in body else ""
        self._json({"status": "ok", "content": saved})

    def handle_delete_note(self):
        note_id = self._note_id_from_path()
        md_path = note_file_for(note_id)
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
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    return NOTES_DIR / f"{note_id}.md"


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


def start_server() -> ReusableThreadingTCPServer:
    ensure_directories()
    load_local_env()
    print(" Knowledge Base Server")
    print(f" Data root: {DATA_ROOT}")
    print(f" Database:  {DB_PATH}")
    articles = scan_articles()
    notes = get_all_notes()
    print(f" Articles: {len(articles)}")
    print(f" Notes:    {len(notes)}")
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
