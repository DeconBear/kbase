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
    delete_workspace,
    ensure_directories,
    get_all_articles,
    get_all_notes,
    get_article,
    get_conn,
    list_article_attachments,
    list_article_history,
    list_chat_sessions,
    list_conversion_history,
    list_workspaces,
    load_chat_session_file,
    load_local_env,
    load_translation_state,
    public_local_env,
    record_conversion,
    remove_item_from_workspace,
    replace_article_tags,
    save_chat_index,
    save_chat_session_file,
    save_translation_state,
    update_article_fields,
    upsert_article,
    upsert_article_attachment,
    upsert_note,
    upsert_workspace,
)
from llm_config import (
    public_llm_config,
    resolve_llm_settings,
    save_llm_config_from_public,
)

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
        record_article_history_check = list_article_history  # noqa: F841
    except Exception:
        pass
    from storage import record_article_history
    record_article_history(article_id, engine, file_path)


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
)

SENSITIVE_KEYS = {
    "LLM_API_KEY",
    "DOCMIND_ACCESS_KEY_ID",
    "DOCMIND_ACCESS_KEY_SECRET",
    "DOCPARSER_API_KEY",
}


def _mask_value(key: str, value: str) -> str:
    if not value:
        return ""
    if key in SENSITIVE_KEYS:
        if len(value) <= 8:
            return "•" * len(value)
        return f"{value[:4]}…{value[-4:]}"
    return value


def public_env() -> dict:
    data = public_local_env()
    return {k: {"value": _mask_value(k, v), "set": bool(v)} for k, v in data.items() if k in KNOWN_ENV_KEYS}


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
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length))

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
            elif path == "/api/notes":
                self._json({"notes": get_all_notes()})
            elif path.startswith("/api/notes/") and path.endswith("/backlinks"):
                self.handle_note_backlinks()
            elif path.startswith("/api/notes/"):
                self.handle_get_note()
            elif path == "/api/library-chat/sessions":
                self.handle_library_chat_sessions()
            elif path.startswith("/api/library-chat/sessions/"):
                self.handle_library_chat_session_get()
            elif path == "/api/workspaces":
                self._json({"workspaces": list_workspaces()})
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
                target = PACKAGE_DIR / relative
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
            elif path == "/api/library-chat/ask":
                self.handle_library_chat_ask()
            elif path == "/api/library-chat/sessions":
                self.handle_library_chat_sessions_create()
            elif path == "/api/library-chat/sessions/delete":
                self.handle_library_chat_session_delete()
            elif path == "/api/library-chat/sessions/clear":
                self.handle_library_chat_session_clear()
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
            elif path.startswith("/api/articles/") and path.endswith("/attachments"):
                self.handle_upload_attachment()
            elif path.startswith("/api/articles/") and path.endswith("/history/delete"):
                self.handle_history_delete()
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
            elif path.startswith("/api/notes/") and path.endswith("/rename"):
                self.handle_rename_note()
            elif path.startswith("/api/notes/"):
                self.handle_save_note()
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
            elif path.startswith("/api/articles/") and "/attachments/" in path:
                self.handle_delete_attachment()
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

            base = Path(filename).stem or "upload"
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
        if "tags" in updates:
            replace_article_tags(article_id, updates.get("tags") or [])
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

    def handle_note_backlinks(self):
        parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
        if len(parts) < 5:
            self._error(400, "Invalid path")
            return
        note_id = urllib.parse.unquote(parts[3])
        current = next((n for n in get_all_notes() if n["id"] == note_id), None)
        if not current or not current.get("title"):
            self._json({"backlinks": []})
            return
        title = current["title"]
        pattern = re.compile(r"\[\[" + re.escape(title) + r"\]\]")
        backlinks = []
        for n in get_all_notes():
            if n["id"] == note_id:
                continue
            md_path = note_file_for(n["id"])
            if not md_path.exists():
                continue
            try:
                if pattern.search(md_path.read_text(encoding="utf-8")):
                    backlinks.append({"id": n["id"], "title": n.get("title", "")})
            except OSError:
                continue
        self._json({"backlinks": backlinks})

    def handle_create_note(self):
        body = self._read_json()
        title = str(body.get("title") or "Untitled").strip()[:200]
        folder = str(body.get("folder") or "").strip()[:200]
        ts = time.strftime("%Y%m%d_%H%M%S")
        uid = os.urandom(4).hex()
        note_id = f"note_{ts}_{uid}"
        md_path = note_file_for(note_id)
        md_path.write_text(f"# {title}\n\n", encoding="utf-8")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": note_id,
            "title": title,
            "created_at": now,
            "modified_at": now,
            "tags": [],
            "folder": folder,
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
        md_path.write_text(content, encoding="utf-8")
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        existing = next((n for n in get_all_notes() if n["id"] == note_id), {"id": note_id})
        existing["title"] = title or existing.get("title", "")
        existing["modified_at"] = now
        if "tags" in body:
            tags = body["tags"]
            existing["tags"] = [str(t).strip()[:50] for t in tags if str(t).strip()] if isinstance(tags, list) else []
        if "folder" in body:
            existing["folder"] = str(body.get("folder") or "").strip()[:200]
        existing.setdefault("created_at", now)
        upsert_note(existing)
        self._json({"status": "ok"})

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
    print(f" Listening on http://localhost:{PORT}")

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
