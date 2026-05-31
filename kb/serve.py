"""Knowledge Base Server - article listing, upload, conversion, save."""
import http.server
import socketserver
import io
import os
import sys
import json
import re
import shutil
import subprocess
import time
import threading
import urllib.parse
import urllib.request
import urllib.error
import zipfile
from pathlib import Path

from llm_config import (
    public_llm_config,
    resolve_llm_settings,
    save_llm_config_from_public,
)

PORT = 8765
DIR = Path(__file__).parent.absolute()
ARTICLES_DIR = DIR / "articles"
NOTES_DIR = DIR / "notes"
INDEX_FILE = DIR / "kb-index.json"
NOTES_INDEX_FILE = DIR / "notes_index.json"
INVALID_ARTICLE_CHARS = set("/\\:*?\"<>|'")


def _is_inside(path: Path, base: Path) -> bool:
    try:
        return path == base or path.is_relative_to(base)
    except AttributeError:
        try:
            return os.path.commonpath([str(path), str(base)]) == str(base)
        except ValueError:
            return False


def sanitize_article_id(value: str) -> str:
    """Create a filesystem-safe single-folder article id from an upload name."""
    base = Path(value or "upload").stem or "upload"
    article_id = re.sub(r"[\s.]+", "_", base.strip())
    article_id = "".join(
        "_" if ch in INVALID_ARTICLE_CHARS or ord(ch) < 32 else ch
        for ch in article_id
    )
    article_id = re.sub(r"_+", "_", article_id).strip(" ._")
    return article_id or f"upload_{int(time.time())}"


def validate_article_id(article_id: str) -> str:
    """Accept only a single safe path component as an article id."""
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
    rel = Path(str(filepath or ""))
    if (
        rel.is_absolute()
        or rel.drive
        or rel.anchor
        or any(part == ".." for part in rel.parts)
    ):
        raise ValueError("Invalid save path")

    target = (DIR / rel).resolve()
    dir_root = DIR.resolve()
    articles_root = ARTICLES_DIR.resolve()
    notes_root = NOTES_DIR.resolve()
    config_path = (DIR / "low_memory_config.json").resolve()

    if target == config_path:
        return target
    if _is_inside(target, articles_root) and target != articles_root:
        return target
    if _is_inside(target, notes_root) and target != notes_root:
        return target
    if not _is_inside(target, dir_root):
        raise ValueError("Invalid save path")
    raise ValueError("Saving is only allowed for article files and settings")


def load_env():
    """Load local.env into os.environ."""
    env_file = DIR.parent / "local.env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ[key.strip()] = val.strip()


load_env()

os.chdir(str(DIR))


import db_api

def load_index():
    return db_api.get_all_articles()

def save_index(idx):
    # Fallback/stub for compatibility, actually we update individual items now
    pass


def load_runtime_config():
    config_path = DIR / "low_memory_config.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _read_json_file(path):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _original_file_for(folder):
    pdf = folder / "original.pdf"
    if pdf.exists():
        return pdf
    try:
        originals = [
            p for p in folder.iterdir()
            if p.is_file() and p.stem == "original" and p.suffix.lower() != ".md"
        ]
    except Exception:
        return None
    return sorted(originals)[0] if originals else None


def _apply_info_payload(article, info):
    if not isinstance(info, dict) or not info:
        return
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


def _clean_bib_value(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _escape_bib_value(value):
    return _clean_bib_value(value).replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _split_authors(value):
    return [
        item.strip()
        for item in re.split(r"\s*(?:;|,|\band\b|和)\s*", _clean_bib_value(value), flags=re.I)
        if item.strip()
    ]


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
    body = [
        f"  {key} = {{{_escape_bib_value(value)}}}"
        for key, value in fields
        if _clean_bib_value(value)
    ]
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


def _preferred_markdown_file(article_id):
    folder = article_dir_for(article_id)
    for suffix in ("_calibrated.md", ".md", "_translated.md"):
        candidate = folder / f"{article_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


# ===== Note helpers =====

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


def load_notes_index():
    return db_api.get_all_notes()

def save_notes_index(idx):
    pass


def scan_articles(idx):
    """Sync index with filesystem - add new folders, update existing statuses."""
    if not ARTICLES_DIR.exists():
        save_index(idx)
        return idx

    existing = {a["id"]: a for a in idx["articles"]}
    for folder in sorted(ARTICLES_DIR.iterdir()):
        if not folder.is_dir():
            continue

        trans_exists = (folder / f"{folder.name}_translated.md").exists()
        summary_exists = (folder / f"{folder.name}_summary.md").exists()
        pdf_exists = (folder / "original.pdf").exists()
        md_exists = (folder / f"{folder.name}.md").exists()
        original_file = _original_file_for(folder)
        file_available = original_file is not None
        meta = _read_json_file(folder / f"{folder.name}_meta.json")
        info = _read_json_file(folder / f"{folder.name}_info.json")

        if folder.name in existing:
            a = existing[folder.name]
            a.setdefault("kind", "paper" if pdf_exists else "file")
            a.setdefault("source_filename", "")
            a.setdefault("authors", [])
            a.setdefault("doi", "")
            a.setdefault("year", "")
            a.setdefault("venue", "")
            a.setdefault("abstract", "")
            a.setdefault("metadata_extracted", False)
            a.setdefault("metadata_source", "")
            a["file_available"] = file_available
            if meta:
                if meta.get("source_filename") and not a.get("source_filename"):
                    a["source_filename"] = meta["source_filename"]
                if meta.get("document_kind"):
                    a["kind"] = meta["document_kind"]
                elif meta.get("source") == "pymupdf" and not a.get("kind"):
                    a["kind"] = "paper"
                if meta.get("title") and (not a.get("title") or a.get("title") == folder.name):
                    a["title"] = meta["title"]
                page_stats = meta.get("page_stats") or []
                if page_stats and not a.get("pages"):
                    a["pages"] = len(page_stats)
            if original_file and not a.get("source_filename"):
                a["source_filename"] = original_file.name
            _apply_info_payload(a, info)
            if trans_exists != a.get("translated"):
                a["translated"] = trans_exists
            
            old_trans_exists = (folder / f"{folder.name}_translated_old.md").exists()
            if old_trans_exists != a.get("has_old_translation"):
                a["has_old_translation"] = old_trans_exists

            if summary_exists != a.get("summarized"):
                a["summarized"] = summary_exists
            if pdf_exists != a.get("pdf_available"):
                a["pdf_available"] = pdf_exists
            if md_exists != a.get("md_available"):
                a["md_available"] = md_exists
            if md_exists and a.get("converting"):
                a.pop("converting", None)
            # Remove converting flag if process seems dead (no log update for 5 min)
            log_file = folder / "conversion.log"
            if a.get("converting") and log_file.exists():
                age = time.time() - log_file.stat().st_mtime
                if age > 300:
                    a.pop("converting", None)
            continue

        # New article
        toc = meta.get("table_of_contents", [])
        title = meta.get("title", "")
        author = ""
        if toc:
            title = title or toc[0].get("title", "").replace("\n", " ")
            if len(toc) > 1:
                author = toc[1].get("title", "").replace("\n", " ")

        page_stats = meta.get("page_stats", [])
        pages = len(page_stats)
        kind = meta.get("document_kind") or ("paper" if pdf_exists else "file")

        article = {
            "id": folder.name,
            "title": title or folder.name,
            "author": author or "",
            "authors": [],
            "pages": pages,
            "date_added": time.strftime("%Y-%m-%d %H:%M"),
            "category": "",
            "tags": [],
            "translated": trans_exists,
            "has_old_translation": (folder / f"{folder.name}_translated_old.md").exists(),
            "summarized": summary_exists,
            "pdf_available": pdf_exists,
            "md_available": md_exists,
            "file_available": file_available,
            "source_filename": meta.get("source_filename") or (original_file.name if original_file else ""),
            "kind": kind,
            "doi": "",
            "year": "",
            "venue": "",
            "abstract": "",
            "metadata_extracted": False,
            "metadata_source": "",
        }
        _apply_info_payload(article, info)
        idx["articles"].append(article)
        print(f"  Discovered: {folder.name}")
    save_index(idx)
    return idx


# ===== Conversion status tracking =====
# In-memory status for active conversions (survives page refresh)
_conv_status = {}
_conv_lock = threading.Lock()
_translation_threads = {}
_translation_lock = threading.Lock()
_metadata_threads = {}
_metadata_lock = threading.Lock()


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def set_conv_status(article_id, status, message="", log=""):
    with _conv_lock:
        _conv_status[article_id] = {
            "status": status,  # "running" | "done" | "error"
            "message": message,
            "log": log,
            "updated": time.time()
        }


def get_conv_status(article_id):
    with _conv_lock:
        return _conv_status.get(article_id, None)


def run_conversion(pdf_path, article_id, engine_name="marker", docparser_engine=None):
    """Run PDF conversion using the specified engine in background."""
    log_path = ARTICLES_DIR / article_id / "conversion.log"

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        with _conv_lock:
            if article_id in _conv_status:
                s = _conv_status[article_id]
                s["log"] = s.get("log", "") + msg + "\n"
                s["updated"] = time.time()

    try:
        log(f"=== Conversion started at {time.strftime('%H:%M:%S')} ===")
        log(f"Engine: {engine_name}")
        log(f"PDF: {pdf_path}")

        set_conv_status(article_id, "running", f"启动 {engine_name} 引擎...", log="")

        from engines import get_engine
        engine = get_engine(engine_name)
        
        if engine_name == "docparser" and docparser_engine:
            success = engine.run(pdf_path, article_id, log_callback=log, engine=docparser_engine)
        else:
            success = engine.run(pdf_path, article_id, log_callback=log)

        if success:
            log("=== Conversion completed successfully ===")
            # Save versioned copy: {id}_{engine}.md
            md_file = ARTICLES_DIR / article_id / f"{article_id}.md"
            versioned = ARTICLES_DIR / article_id / f"{article_id}_{engine_name}.md"
            if md_file.exists():
                try:
                    shutil.copy2(md_file, versioned)
                except Exception:
                    pass
            
            # Delete or backup outdated derived files
            for suffix in ["_calibrated.md", "_translated.md", "_summary.md"]:
                derived_file = ARTICLES_DIR / article_id / f"{article_id}{suffix}"
                if derived_file.exists():
                    try:
                        if suffix == "_translated.md":
                            shutil.move(str(derived_file), str(ARTICLES_DIR / article_id / f"{article_id}_translated_old.md"))
                        else:
                            derived_file.unlink()
                    except Exception as e:
                        print(f"Failed to handle {derived_file}: {e}")

            # Record in conversion history
            _record_conv(article_id, engine_name, "success")
            set_conv_status(article_id, "done", "解析完成", log="")
        else:
            log("=== Conversion FAILED ===")
            _record_conv(article_id, engine_name, "fail")
            set_conv_status(article_id, "error", "解析失败，查看日志了解详情")
            return

        # Update index
        import db_api
        db_api.update_article(article_id, {
            "md_available": True,
            "parser": engine_name,
            "converting": False
        })
        _start_extract_info(article_id, reason=f"parsed:{engine_name}", allow_parallel=True)

    except ValueError as e:
        log(f"ERROR: {e}")
        set_conv_status(article_id, "error", str(e))
    except Exception as e:
        import traceback
        log(f"FATAL ERROR: {e}")
        log(traceback.format_exc())
        set_conv_status(article_id, "error", f"系统错误: {e}")


def _run_calibrate(article_id, log_callback):
    """Background calibration runner."""
    log_path = ARTICLES_DIR / article_id / "calibrate.log"
    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass
        log_callback(msg)

    try:
        from calibrate import calibrate
        ok = calibrate(article_id, log_callback=log)
        if ok:
            set_conv_status(article_id, "done", "校准完成")
            _start_extract_info(article_id, reason="calibrated", allow_parallel=True)
        else:
            set_conv_status(article_id, "error", "校准失败")
    except Exception as e:
        import traceback
        log(f"Calibrate error: {e}\n{traceback.format_exc()}")
        set_conv_status(article_id, "error", str(e))


def _translation_state(article_id):
    article_dir = article_dir_for(article_id)
    state_file = article_dir / f"{article_id}_translation_state.json"
    if state_file.exists():
        try:
            return json.loads(state_file.read_text(encoding="utf-8"))
        except Exception as e:
            return {"status": "error", "message": str(e)}
    if (article_dir / f"{article_id}_translated.md").exists():
        return {"status": "done", "message": "翻译完成", "percent": 100}
    return {"status": "idle", "message": "", "percent": 0}


def _run_translate(article_id, mode="update", target_language="Simplified Chinese", extra_prompt=""):
    log_path = ARTICLES_DIR / article_id / "translation.log"

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    try:
        from translate import translate_article
        ok = translate_article(article_id, mode=mode, target_language=target_language, extra_prompt=extra_prompt, log_callback=log)
        if ok:
            import db_api
            db_api.update_article(article_id, {
                "translated": True,
                "has_old_translation": True
            })
    except Exception as e:
        import traceback
        log(f"Translation error: {e}\n{traceback.format_exc()}")
        try:
            from translate import write_state
            write_state(article_id, status="error", message=str(e))
        except Exception:
            pass
    finally:
        with _translation_lock:
            _translation_threads.pop(article_id, None)


def _run_extract_info(article_id, provider_id="", model="", reason="auto"):
    log_path = ARTICLES_DIR / article_id / "metadata.log"

    def log(msg):
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(msg + "\n")
        except Exception:
            pass

    try:
        from document_info import extract_document_info
        extract_document_info(
            article_id,
            log_callback=log,
            provider_id=provider_id,
            model=model,
            reason=reason,
        )
    except Exception as e:
        import traceback
        log(f"Metadata extraction error: {e}\n{traceback.format_exc()}")
    finally:
        with _metadata_lock:
            if _metadata_threads.get(article_id) is threading.current_thread():
                _metadata_threads.pop(article_id, None)


def _start_extract_info(article_id, provider_id="", model="", reason="auto", allow_parallel=False):
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


def _record_conv(article_id, engine, status):
    """Record conversion attempt in article's conversions.json."""
    hist_file = ARTICLES_DIR / article_id / "conversions.json"
    history = []
    if hist_file.exists():
        try:
            history = json.loads(hist_file.read_text(encoding="utf-8"))
        except Exception:
            pass
    history.append({
        "engine": engine,
        "status": status,
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
    })
    hist_file.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")


# ===== HTTP Handler =====
class KBHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f" [{self.client_address[0]}] {args[0]}")

    def end_headers(self):
        # Prevent browser caching for all responses (single-page app, always up-to-date)
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        request_path = urllib.parse.urlsplit(self.path).path
        if request_path == "/api/articles":
            self.serve_json(scan_articles(load_index()))
        elif request_path == "/api/workspaces":
            self.handle_get_workspaces()
        elif request_path.startswith("/api/workspaces/") and request_path.endswith("/items"):
            self.handle_get_workspace_items()
        elif request_path == "/api/settings":
            self.serve_json(load_runtime_config())
        elif request_path == "/api/llm-config":
            self.serve_json(public_llm_config())
        elif request_path == "/api/library-chat/sessions":
            try:
                from library_chat import list_sessions
                self.serve_json(list_sessions())
            except Exception as e:
                self.serve_error_json(500, str(e))
        elif request_path.startswith("/api/library-chat/sessions/"):
            try:
                from library_chat import get_session
                session_id = urllib.parse.unquote(
                    request_path.rstrip("/").rsplit("/", 1)[-1]
                )
                self.serve_json(get_session(session_id))
            except Exception as e:
                self.serve_error_json(404, str(e))
        elif request_path.startswith("/api/translation-status/"):
            try:
                article_id = article_id_from_request_path(self.path)
                article_dir_for(article_id)
            except ValueError as e:
                self.send_error(400, str(e))
                return
            state = _translation_state(article_id)
            with _translation_lock:
                thread = _translation_threads.get(article_id)
                if thread and thread.is_alive():
                    state["status"] = "running"
                    state["message"] = state.get("message") or "后台翻译中"
            self.serve_json(state)
        elif request_path.startswith("/api/conversion-status/"):
            try:
                article_id = article_id_from_request_path(self.path)
                article_dir = article_dir_for(article_id)
            except ValueError as e:
                self.send_error(400, str(e))
                return
            status = get_conv_status(article_id)
            log_path = article_dir / "conversion.log"
            log_content = ""
            if log_path.exists():
                try:
                    log_content = log_path.read_text(encoding="utf-8")
                except Exception:
                    pass
            if status is None:
                status = {"status": "unknown", "message": "", "log": log_content}
            else:
                status = dict(status)
                status["log"] = log_content  # always use file log for full content
            self.serve_json(status)
        elif request_path == "/api/notes":
            self.serve_json(load_notes_index())
        elif request_path.startswith("/api/notes/") and request_path.endswith("/backlinks"):
            self.handle_note_backlinks()
        elif request_path.startswith("/api/notes/"):
            self.handle_get_note()
        elif request_path.startswith("/api/conversion-history/"):
            try:
                article_id = article_id_from_request_path(self.path)
                article_dir = article_dir_for(article_id)
            except ValueError as e:
                self.send_error(400, str(e))
                return
            hist_file = article_dir / "conversions.json"
            if hist_file.exists():
                try:
                    history = json.loads(hist_file.read_text(encoding="utf-8"))
                except Exception:
                    history = []
            else:
                history = []
            # Enumerate available versioned MD files
            versions = []
            if article_dir.exists():
                for f in sorted(article_dir.iterdir()):
                    name = f.name
                    if name.endswith(".md") and name.startswith(f"{article_id}_"):
                        # e.g. {id}_pymupdf.md, {id}_marker.md, {id}_docmind.md
                        engine = name.rsplit("_", 1)[-1].replace(".md", "")
                        if engine in {"pymupdf", "marker", "docmind", "docparser"}:
                            versions.append({"engine": engine, "file": name})
            self.serve_json({"history": history, "versions": versions})
        elif request_path.startswith("/api/articles/") and request_path.endswith("/attachments"):
            self.handle_get_attachments()
        elif request_path == "/api/export":
            try:
                from urllib.parse import parse_qs, urlsplit
                qs = parse_qs(urlsplit(self.path).query)
                self.handle_export(
                    force_ids=qs.get("ids", [""])[0].split(","), 
                    force_format=qs.get("format", [""])[0]
                )
            except Exception as e:
                self.send_error(500, str(e))
        else:
            super().do_GET()

    def do_POST(self):
        if self.path == "/api/upload":
            self.handle_upload()
        elif self.path == "/api/library-chat/ask":
            self.handle_library_chat_ask()
        elif self.path == "/api/library-chat/sessions":
            self.handle_library_chat_create_session()
        elif self.path == "/api/library-chat/sessions/delete":
            self.handle_library_chat_delete_session()
        elif self.path == "/api/library-chat/sessions/clear":
            self.handle_library_chat_clear_session()
        elif self.path == "/api/chat":
            self.handle_chat()
        elif self.path == "/api/llm-config":
            self.handle_llm_config()
        elif self.path.startswith("/api/calibrate/"):
            self.handle_calibrate()
        elif self.path.startswith("/api/translate/"):
            self.handle_translate()
        elif self.path.startswith("/api/extract-info/"):
            self.handle_extract_info()
        elif self.path.startswith("/api/open-folder/"):
            self.handle_open_folder()
        elif self.path == "/api/export":
            self.handle_export()
        elif self.path.startswith("/api/convert/"):
            self.handle_convert()
        elif self.path == "/api/articles/update":
            self.handle_article_update()
        elif self.path == "/api/articles/delete":
            self.handle_article_delete()
        elif self.path == "/api/config/docparser":
            self.handle_config_docparser()
        elif self.path == "/api/notes":
            self.handle_create_note()
        elif self.path.startswith("/api/articles/") and self.path.endswith("/attachments"):
            self.handle_upload_attachment()
        elif self.path == "/api/workspaces":
            self.handle_create_workspace()
        elif self.path.startswith("/api/workspaces/") and self.path.endswith("/items"):
            self.handle_add_workspace_items()
        elif self.path == "/api/batch/delete":
            self.handle_batch_delete()
        elif self.path == "/api/batch/export":
            self.handle_batch_export()
        elif self.path == "/api/batch/import":
            self.handle_batch_import()
        else:
            self.send_error(404)

    def do_PUT(self):
        if self.path == "/save":
            self.handle_save_file()
        elif self.path == "/api/llm-config":
            self.handle_llm_config()
        elif self.path == "/api/articles/update":
            self.handle_article_update()
        elif self.path.startswith("/api/notes/") and self.path.endswith("/rename"):
            self.handle_rename_note()
        elif self.path.startswith("/api/notes/"):
            self.handle_save_note()
        else:
            super().do_PUT()

    def do_DELETE(self):
        if self.path.startswith("/api/notes/"):
            self.handle_delete_note()
        elif self.path.startswith("/api/articles/") and "/attachments/" in self.path:
            self.handle_delete_attachment()
        elif self.path.startswith("/api/workspaces/") and self.path.endswith("/items"):
            self.handle_remove_workspace_items()
        elif self.path.startswith("/api/workspaces/"):
            self.handle_delete_workspace()
        else:
            self.send_error(404)

    def serve_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def serve_error_json(self, status, message):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps({"error": message}, ensure_ascii=False).encode())

    def serve_download(self, data, filename, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def handle_llm_config(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len)) if content_len else {}
            self.serve_json(save_llm_config_from_public(body))
        except Exception as e:
            self.serve_error_json(400, str(e))

    def handle_config_docparser(self):
        try:
            body = self.read_json_body()
            api_key = body.get("api_key", "").strip()
            engine = body.get("engine", "").strip()
            
            env_file = DIR.parent / "local.env"
            if env_file.exists():
                lines = env_file.read_text(encoding="utf-8").splitlines()
            else:
                lines = []
            
            if api_key:
                os.environ["DOCPARSER_API_KEY"] = api_key
            if engine:
                os.environ["DOCPARSER_ENGINE"] = engine
            
            new_lines = []
            found_key = False
            found_engine = False
            for line in lines:
                if line.startswith("DOCPARSER_API_KEY=") and api_key:
                    new_lines.append(f"DOCPARSER_API_KEY={api_key}")
                    found_key = True
                elif line.startswith("DOCPARSER_ENGINE=") and engine:
                    new_lines.append(f"DOCPARSER_ENGINE={engine}")
                    found_engine = True
                else:
                    new_lines.append(line)
            
            if api_key and not found_key:
                new_lines.append(f"DOCPARSER_API_KEY={api_key}")
            if engine and not found_engine:
                new_lines.append(f"DOCPARSER_ENGINE={engine}")
                
            env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.serve_error_json(400, str(e))

    def read_json_body(self):
        content_len = int(self.headers.get("Content-Length", 0))
        if not content_len:
            return {}
        body = json.loads(self.rfile.read(content_len))
        if not isinstance(body, dict):
            raise ValueError("Expected JSON object")
        return body

    def handle_library_chat_ask(self):
        try:
            body = self.read_json_body()
            if body.get("stream"):
                self.handle_library_chat_stream(body)
                return
            from library_chat import ask_library_question
            result = ask_library_question(
                body.get("question") or body.get("message") or "",
                session_id=body.get("session_id") or "",
                provider_id=body.get("provider_id") or body.get("provider") or "",
                model=body.get("model") or "",
                workspace_id=body.get("workspace_id") or "",
            )
            self.serve_json(result)
        except Exception as e:
            self.serve_error_json(500, str(e))

    def _send_sse_headers(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.send_header("X-Accel-Buffering", "no")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.close_connection = True

    def _write_sse_data(self, data):
        if isinstance(data, str):
            payload = data
        else:
            payload = json.dumps(data, ensure_ascii=False)
        for line in payload.splitlines() or [""]:
            self.wfile.write(f"data: {line}\n".encode("utf-8"))
        self.wfile.write(b"\n")
        self.wfile.flush()

    @staticmethod
    def _delta_from_sse_data(data):
        if not data or data == "[DONE]":
            return ""
        try:
            payload = json.loads(data)
            choice = (payload.get("choices") or [{}])[0]
            delta = choice.get("delta") or choice.get("message") or {}
            return delta.get("content") or ""
        except Exception:
            return ""

    def handle_library_chat_stream(self, body):
        from library_chat import (
            finalize_library_answer,
            list_sessions,
            prepare_library_question,
        )

        provider_id = body.get("provider_id") or body.get("provider") or ""
        model = body.get("model") or ""
        settings = resolve_llm_settings(provider_id=provider_id, model=model)
        if not settings.get("api_url"):
            raise ValueError(f"LLM provider {settings.get('provider_name', '')} has no API URL")
        if not settings.get("api_key"):
            raise ValueError(f"LLM API key not configured for {settings.get('provider_name', '')}")

        prepared = prepare_library_question(
            body.get("question") or body.get("message") or "",
            session_id=body.get("session_id") or "",
            provider_id=provider_id,
            model=model,
            workspace_id=body.get("workspace_id") or "",
        )

        self._send_sse_headers()
        self._write_sse_data({
            "type": "meta",
            "sources": prepared["sources"],
            **list_sessions(),
        })

        req_body = {
            "model": settings["model"],
            "messages": prepared["api_messages"],
            "temperature": body.get("temperature", 0.35),
            "max_tokens": body.get("max_tokens", 8192),
            "stream": True,
        }
        answer = ""
        try:
            req = urllib.request.Request(
                settings["api_url"],
                data=json.dumps(req_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings['api_key']}",
                    "Accept": "text/event-stream",
                },
            )
            with urllib.request.urlopen(req, timeout=300) as resp:
                upstream_content_type = resp.headers.get("Content-Type", "")
                if "text/event-stream" in upstream_content_type:
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        if line.startswith(b"data:"):
                            data_text = line[5:].decode("utf-8", errors="replace").strip()
                            delta = self._delta_from_sse_data(data_text)
                            if delta:
                                answer += delta
                                self._write_sse_data({"choices": [{"delta": {"content": delta}}]})
                        elif line.strip():
                            self.wfile.write(line)
                            self.wfile.flush()
                else:
                    payload = json.loads(resp.read())
                    answer = (payload.get("choices") or [{}])[0].get("message", {}).get("content", "")
                    self._write_sse_data({"choices": [{"delta": {"content": answer}}]})

            result = finalize_library_answer(prepared, answer)
            self._write_sse_data({"type": "done", **result})
            self._write_sse_data("[DONE]")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._write_sse_data({"type": "error", "message": str(e)})
                self._write_sse_data("[DONE]")
            except Exception:
                pass

    def handle_library_chat_create_session(self):
        try:
            from library_chat import create_session
            body = self.read_json_body()
            self.serve_json(create_session(body.get("title") or "新会话"))
        except Exception as e:
            self.serve_error_json(400, str(e))

    def handle_library_chat_delete_session(self):
        try:
            from library_chat import delete_session
            body = self.read_json_body()
            self.serve_json(delete_session(str(body.get("session_id") or "")))
        except Exception as e:
            self.serve_error_json(400, str(e))

    def handle_library_chat_clear_session(self):
        try:
            from library_chat import clear_session
            body = self.read_json_body()
            self.serve_json(clear_session(str(body.get("session_id") or "")))
        except Exception as e:
            self.serve_error_json(400, str(e))

    def handle_save_file(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_len)
            data = json.loads(body)
            filepath = data.get("path", "")
            content = data.get("content", "")

            try:
                abs_path = resolve_save_path(filepath)
            except ValueError as e:
                self.send_error(403, str(e))
                return

            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_text(content, encoding="utf-8")
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.send_error(500, str(e))

    def handle_chat(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
        except Exception:
            self.serve_error_json(400, "Invalid JSON")
            return
        if not isinstance(body, dict):
            self.serve_error_json(400, "Expected JSON object")
            return

        try:
            settings = resolve_llm_settings(
                provider_id=body.get("provider_id") or body.get("provider") or "",
                model=body.get("model") or "",
            )
        except Exception as e:
            self.serve_error_json(400, str(e))
            return

        if not settings.get("api_url"):
            self.serve_error_json(
                500,
                f"LLM provider {settings.get('provider_name', '')} has no API URL",
            )
            return
        if not settings.get("api_key"):
            self.serve_error_json(
                500,
                f"LLM API key not configured for {settings.get('provider_name', '')}",
            )
            return

        req_body = {
            "model": settings["model"],
            "messages": body.get("messages", []),
            "temperature": body.get("temperature", 0.3),
            "max_tokens": body.get("max_tokens", 4096),
        }
        if "stream" in body:
            req_body["stream"] = body["stream"]
        stream_requested = bool(req_body.get("stream"))

        try:
            req = urllib.request.Request(
                settings["api_url"],
                data=json.dumps(req_body).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {settings['api_key']}",
                    "Accept": "text/event-stream" if stream_requested else "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=300 if stream_requested else 120) as resp:
                upstream_content_type = resp.headers.get("Content-Type", "")
                if stream_requested and "text/event-stream" in upstream_content_type:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.send_header("X-Accel-Buffering", "no")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.close_connection = True
                    while True:
                        line = resp.readline()
                        if not line:
                            break
                        self.wfile.write(line)
                        self.wfile.flush()
                else:
                    result = resp.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(result)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except urllib.error.HTTPError as e:
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(e.read())
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_upload(self):
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            self.send_error(400, "Expected multipart/form-data")
            return

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len)

        boundary = None
        for part in content_type.split(";"):
            part = part.strip()
            if part.startswith("boundary="):
                boundary = part.split("=", 1)[1].strip('"')
                break
        if not boundary:
            self.send_error(400, "No boundary found")
            return

        boundary_bytes = boundary.encode()
        parts = body.split(b"--" + boundary_bytes)
        for part in parts:
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
                (
                    line for line in headers_raw.splitlines()
                    if line.lower().startswith("content-disposition")
                ),
                "",
            )
            match = re.search(r'filename="([^"]*)"', disposition)
            if not match:
                match = re.search(r"filename=([^;\r\n]+)", disposition)
            if match:
                filename = Path(match.group(1).strip()).name or filename

            base = Path(filename).stem or "upload"
            article_id = sanitize_article_id(filename)
            ext = Path(filename).suffix.lower()
            if not re.fullmatch(r"\.[a-z0-9]{1,12}", ext or ""):
                ext = ".bin"

            import db_api
            db_api.update_article(article_id, {
                "converting": False,
                "has_old_translation": True
            })
            idx = load_index()
            existing = [a for a in idx["articles"] if a["id"] == article_id]
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
            except ImportError as e:
                preparse_error = f"Document parsing unavailable: {e}"
                if is_pdf:
                    _record_conv(article_id, "pymupdf", "fail")
            else:
                try:
                    kind = material_kind_from_filename(filename)
                    if is_pdf:
                        parsed = quick_parse_pdf(article_id, original_path, filename)
                        title = parsed.get("title") or title
                        pages = parsed.get("pages") or 0
                        md_available = True
                        _record_conv(article_id, "pymupdf", "success")
                    else:
                        parsed = ingest_non_pdf_file(article_id, original_path, filename)
                        title = parsed.get("title") or title
                        kind = parsed.get("kind") or kind
                        md_available = True
                except Exception as e:
                    preparse_error = str(e)
                    if is_pdf:
                        _record_conv(article_id, "pymupdf", "fail")

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
            }
            if preparse_error:
                article["preparse_error"] = preparse_error
            import db_api
            db_api.add_article(article)

            info_extraction = ""
            if md_available:
                started = _start_extract_info(article_id, reason="upload")
                info_extraction = "running" if started else "already_running"

            self.serve_json({
                "status": "ok",
                "article": article,
                "preparsed": md_available,
                "preparse_error": preparse_error,
                "info_extraction": info_extraction,
            })
            return

        self.send_error(400, "No file field found")

    def handle_calibrate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
            article_dir = article_dir_for(article_id)

            md_file = article_dir / f"{article_id}.md"
            if not md_file.exists():
                self.send_error(404, "No markdown to calibrate")
                return

            from calibrate import calibrate

            def log(msg):
                set_conv_status(article_id, "running", msg)

            set_conv_status(article_id, "running", "校准中...", log="")

            thread = threading.Thread(
                target=_run_calibrate,
                args=(article_id, log),
                daemon=True
            )
            thread.start()
            self.serve_json({"status": "calibrating", "id": article_id})
        except Exception as e:
            self.send_error(500, str(e))

    def handle_translate(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len)) if content_len else {}
            article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
            mode = body.get("mode", "update")
            target_language = body.get("target_language", "Simplified Chinese")
            extra_prompt = body.get("extra_prompt", "")
            article_dir = article_dir_for(article_id)

            md_file = article_dir / f"{article_id}_calibrated.md"
            if not md_file.exists():
                md_file = article_dir / f"{article_id}.md"
            if not md_file.exists():
                self.send_error(404, "No markdown to translate")
                return

            with _translation_lock:
                thread = _translation_threads.get(article_id)
                if thread and thread.is_alive():
                    self.serve_json({"status": "running", "id": article_id, "message": "翻译已在后台运行"})
                    return
                try:
                    from translate import write_state
                    write_state(
                        article_id,
                        status="running",
                        message="后台翻译已启动",
                        done=0,
                        total="?",
                        percent=0,
                    )
                except Exception:
                    pass
                thread = threading.Thread(target=_run_translate, args=(article_id, mode, target_language, extra_prompt), daemon=True)
                _translation_threads[article_id] = thread
                thread.start()
            self.serve_json({"status": "running", "id": article_id, "message": "后台翻译已启动"})
        except Exception as e:
            self.send_error(500, str(e))

    def handle_extract_info(self):
        try:
            body = self.read_json_body()
            article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
            article_dir = article_dir_for(article_id)
            if not article_dir.exists():
                self.serve_error_json(404, "Article not found")
                return
            md_file = article_dir / f"{article_id}.md"
            calibrated_file = article_dir / f"{article_id}_calibrated.md"
            translated_file = article_dir / f"{article_id}_translated.md"
            if not (md_file.exists() or calibrated_file.exists() or translated_file.exists()):
                self.serve_error_json(404, "No markdown available for metadata extraction")
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
                self.serve_json({
                    "status": "running" if started else "already_running",
                    "id": article_id,
                    "reason": reason,
                })
                return

            from document_info import extract_document_info
            result = extract_document_info(
                article_id,
                provider_id=provider_id,
                model=model,
                reason=reason,
            )
            idx = scan_articles(load_index())
            article = next((a for a in idx.get("articles", []) if a.get("id") == article_id), None)
            self.serve_json({"status": "ok", "id": article_id, **result, "article": article})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_open_folder(self):
        try:
            body = self.read_json_body()
            article_id = validate_article_id(body.get("id", "") or article_id_from_request_path(self.path))
            article_dir = article_dir_for(article_id)
            if not article_dir.exists():
                self.serve_error_json(404, "Article folder not found")
                return
            if os.name == "nt":
                os.startfile(str(article_dir))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(article_dir)])
            else:
                subprocess.Popen(["xdg-open", str(article_dir)])
            self.serve_json({"status": "ok", "path": str(article_dir)})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_export(self, force_ids=None, force_format=None):
        try:
            if force_ids is not None:
                ids = force_ids
                export_format = str(force_format).lower().strip()
            else:
                body = self.read_json_body()
                export_format = str(body.get("format") or "").lower().strip()
                ids = body.get("ids") or []
                
            if isinstance(ids, str):
                ids = [ids]
            ids = [validate_article_id(item) for item in ids if item]
            ids = list(dict.fromkeys(ids))
            if not ids:
                self.serve_error_json(400, "No articles selected")
                return
            if export_format not in {"bibtex", "pdf", "markdown"}:
                self.serve_error_json(400, "Unsupported export format")
                return

            idx = scan_articles(load_index())
            by_id = {a.get("id"): a for a in idx.get("articles", [])}
            selected = [by_id[item] for item in ids if item in by_id]
            if not selected:
                self.serve_error_json(404, "Selected articles were not found")
                return

            stamp = time.strftime("%Y%m%d_%H%M%S")
            if export_format == "bibtex":
                used = set()
                content = ("\n\n".join(_article_to_bibtex(article, used) for article in selected) + "\n").encode("utf-8")
                self.serve_download(content, f"kbase_export_{stamp}.bib", "application/x-bibtex; charset=utf-8")
                return

            archive = io.BytesIO()
            used_names = set()
            missing = []
            with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for article in selected:
                    article_id = article.get("id")
                    try:
                        if export_format == "pdf":
                            source = article_dir_for(article_id) / "original.pdf"
                            ext = ".pdf"
                        else:
                            source = _preferred_markdown_file(article_id)
                            ext = ".md"
                    except Exception:
                        source = None
                    if not source or not source.exists():
                        missing.append(article_id)
                        continue
                    name = _unique_archive_name(f"{_export_stem(article)}{ext}", used_names)
                    zf.write(source, name)
                if missing:
                    zf.writestr(
                        "missing.txt",
                        "以下条目没有可导出的文件:\n" + "\n".join(missing) + "\n",
                    )
            payload = archive.getvalue()
            if not used_names:
                self.serve_error_json(404, "No files available for this export")
                return
            suffix = "pdf" if export_format == "pdf" else "markdown"
            self.serve_download(payload, f"kbase_{suffix}_{stamp}.zip", "application/zip")
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_convert(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            article_id = validate_article_id(body.get("id", ""))
            engine = body.get("engine", "marker")
            docparser_engine = body.get("docparser_engine", "").strip()

            if engine == "docparser":
                api_key = body.get("api_key", "").strip()
                env_file = DIR.parent / "local.env"
                if env_file.exists():
                    lines = env_file.read_text(encoding="utf-8").splitlines()
                else:
                    lines = []
                
                if api_key:
                    os.environ["DOCPARSER_API_KEY"] = api_key
                if docparser_engine:
                    os.environ["DOCPARSER_ENGINE"] = docparser_engine

                new_lines = []
                found_key = False
                found_engine = False
                for line in lines:
                    if line.startswith("DOCPARSER_API_KEY=") and api_key:
                        new_lines.append(f"DOCPARSER_API_KEY={api_key}")
                        found_key = True
                    elif line.startswith("DOCPARSER_ENGINE=") and docparser_engine:
                        new_lines.append(f"DOCPARSER_ENGINE={docparser_engine}")
                        found_engine = True
                    else:
                        new_lines.append(line)
                if api_key and not found_key:
                    new_lines.append(f"DOCPARSER_API_KEY={api_key}")
                if docparser_engine and not found_engine:
                    new_lines.append(f"DOCPARSER_ENGINE={docparser_engine}")
                
                env_file.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

            article_dir = article_dir_for(article_id)
            pdf_path = article_dir / "original.pdf"
            if not pdf_path.exists():
                self.send_error(404, "PDF not found")
                return

            # Set converting flag
            import db_api
            db_api.update_article(article_id, {
                "converting": True
            })

            thread = threading.Thread(
                target=run_conversion,
                args=(str(pdf_path), article_id, engine, docparser_engine),
                daemon=True
            )
            thread.start()
            self.serve_json({"status": "converting", "id": article_id, "engine": engine})
        except Exception as e:
            self.send_error(500, str(e))

    def handle_article_update(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            article_id = validate_article_id(body.get("id", ""))
            updates = body.get("updates", {})

            allowed = {
                "title", "author", "authors", "pages", "date_added", "category", "tags",
                "translated", "summarized", "pdf_available", "md_available",
                "converting", "kind", "source_filename", "doi", "year", "venue",
                "abstract", "metadata_extracted", "metadata_extracted_at",
                "metadata_source", "file_available", "parser",
            }
            filtered_updates = {k: v for k, v in updates.items() if k in allowed}
            import db_api
            db_api.update_article(article_id, filtered_updates)
            
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.send_error(500, str(e))

    def handle_article_delete(self):
        try:
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len))
            article_id = validate_article_id(body.get("id", ""))

            import db_api
            db_api.delete_article(article_id)

            article_dir = article_dir_for(article_id)
            if article_dir.exists():
                shutil.rmtree(article_dir)

            self.serve_json({"status": "ok"})
        except Exception as e:
            self.send_error(500, str(e))

    # ===== Note handlers =====

    def _note_id_from_path(self):
        path = urllib.parse.urlsplit(self.path).path.rstrip("/")
        parts = path.strip("/").split("/")
        if len(parts) >= 3 and parts[:2] == ["api", "notes"]:
            return validate_note_id(urllib.parse.unquote(parts[2]))
        raise ValueError("Invalid note path")

    def handle_get_note(self):
        try:
            note_id = self._note_id_from_path()
            idx = load_notes_index()
            note = next((n for n in idx["notes"] if n["id"] == note_id), None)
            if not note:
                self.serve_error_json(404, "Note not found")
                return
                
            md_path = note_file_for(note_id)
            if md_path.exists():
                from utils_yaml import parse_frontmatter
                _, content = parse_frontmatter(md_path)
            else:
                content = ""
                
            self.serve_json({"id": note_id, "content": content, "meta": note})
        except ValueError as e:
            self.serve_error_json(400, str(e))
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_note_backlinks(self):
        """Search all notes server-side for [[title]] backlinks to the given note."""
        try:
            # Extract note_id from path like /api/notes/{id}/backlinks
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            # parts = ['', 'api', 'notes', '{id}', 'backlinks']
            if len(parts) < 5:
                self.serve_error_json(400, "Invalid path")
                return
            note_id = urllib.parse.unquote(parts[3])
            idx = load_notes_index()
            current_note = next((n for n in idx["notes"] if n["id"] == note_id), None)
            if not current_note:
                self.serve_json({"backlinks": []})
                return
            title = current_note.get("title", "")
            if not title:
                self.serve_json({"backlinks": []})
                return
            import re
            pattern = re.compile(r"\[\[" + re.escape(title) + r"\]\]")
            backlinks = []
            for n in idx["notes"]:
                if n["id"] == note_id:
                    continue
                md_path = note_file_for(n["id"])
                if not md_path.exists():
                    continue
                try:
                    content = md_path.read_text(encoding="utf-8")
                    if pattern.search(content):
                        backlinks.append({"id": n["id"], "title": n.get("title", "")})
                except Exception:
                    continue
            self.serve_json({"backlinks": backlinks})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_create_note(self):
        try:
            body = self.read_json_body()
            title = str(body.get("title") or "Untitled").strip()[:200]
            folder = str(body.get("folder") or "").strip()[:200]

            ts = time.strftime("%Y%m%d_%H%M%S")
            uid = os.urandom(4).hex()
            note_id = f"note_{ts}_{uid}"

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
            import db_api
            db_api.add_note(entry)
            
            # The add_note will create the file with frontmatter but empty content, so let's set initial content
            md_path = note_file_for(note_id)
            from utils_yaml import write_frontmatter
            write_frontmatter(md_path, entry, f"# {title}\n\n")

            self.serve_json(entry)
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_save_note(self):
        try:
            note_id = self._note_id_from_path()
            content_len = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(content_len)) if content_len else {}
            content = str(body.get("content") or "")
            title = str(body.get("title") or "").strip()[:200]

            updates = {
                "modified_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            if title:
                updates["title"] = title
            if "tags" in body:
                tags = body.get("tags")
                updates["tags"] = [str(t).strip()[:50] for t in tags if str(t).strip()] if isinstance(tags, list) else []
            if "folder" in body:
                updates["folder"] = str(body.get("folder") or "").strip()[:200]
                
            import db_api
            # db_api.update_note updates DB and frontmatter, but doesn't update content.
            # We must update content directly
            md_path = note_file_for(note_id)
            from utils_yaml import parse_frontmatter, write_frontmatter
            if md_path.exists():
                meta, _ = parse_frontmatter(md_path)
            else:
                meta = {"id": note_id, "type": "note"}
            meta.update(updates)
            write_frontmatter(md_path, meta, content)
            
            db_api.update_note(note_id, updates)

            self.serve_json({"status": "ok"})
        except ValueError as e:
            self.serve_error_json(400, str(e))
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_delete_note(self):
        try:
            note_id = self._note_id_from_path()
            md_path = note_file_for(note_id)
            if md_path.exists():
                md_path.unlink()
            import db_api
            db_api.delete_note(note_id)
            self.serve_json({"status": "ok"})
        except ValueError as e:
            self.serve_error_json(400, str(e))
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_rename_note(self):
        try:
            note_id = self._note_id_from_path()
            body = self.read_json_body()
            new_title = str(body.get("title") or "").strip()[:200]
            if not new_title:
                self.serve_error_json(400, "Title is required")
                return
            import db_api
            db_api.update_note(note_id, {"title": new_title, "modified_at": time.strftime("%Y-%m-%d %H:%M:%S")})
            self.serve_json({"status": "ok", "title": new_title})
        except ValueError as e:
            self.serve_error_json(400, str(e))
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_get_attachments(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            article_id = validate_article_id(urllib.parse.unquote(parts[3]))
            article_dir = article_dir_for(article_id)
            attachments_dir = article_dir / "attachments"
            files = []
            if attachments_dir.exists() and attachments_dir.is_dir():
                for f in attachments_dir.iterdir():
                    if f.is_file():
                        files.append({
                            "name": f.name,
                            "size": f.stat().st_size,
                            "modified": f.stat().st_mtime,
                            "url": f"articles/{urllib.parse.quote(article_id)}/attachments/{urllib.parse.quote(f.name)}"
                        })
            files.sort(key=lambda x: x["modified"], reverse=True)
            self.serve_json({"attachments": files})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_upload_attachment(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            article_id = validate_article_id(urllib.parse.unquote(parts[3]))
            article_dir = article_dir_for(article_id)
            attachments_dir = article_dir / "attachments"
            attachments_dir.mkdir(parents=True, exist_ok=True)

            content_len = int(self.headers.get("Content-Length", 0))
            if not content_len:
                self.send_error(400, "Empty payload")
                return
            body = self.rfile.read(content_len)

            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                self.send_error(400, "Must be multipart/form-data")
                return

            boundary = ""
            for part in content_type.split(";"):
                part = part.strip()
                if part.startswith("boundary="):
                    boundary = part.split("=", 1)[1].strip('"')
                    break
            if not boundary:
                self.send_error(400, "No boundary found")
                return

            boundary_bytes = boundary.encode()
            form_parts = body.split(b"--" + boundary_bytes)
            uploaded_filenames = []
            for part in form_parts:
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
                    (
                        line for line in headers_raw.splitlines()
                        if line.lower().startswith("content-disposition")
                    ),
                    "",
                )
                match = re.search(r'filename="([^"]*)"', disposition)
                if not match:
                    match = re.search(r"filename=([^;\r\n]+)", disposition)
                if match:
                    filename = Path(match.group(1).strip()).name or filename

                uploaded_filenames.append(filename)
                file_path = attachments_dir / filename
                file_path.write_bytes(content)

            if uploaded_filenames:
                self.serve_json({"status": "ok", "filenames": uploaded_filenames})
            else:
                self.send_error(400, "No file found in payload")
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_delete_attachment(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            article_id = validate_article_id(urllib.parse.unquote(parts[3]))
            filename = urllib.parse.unquote(parts[5])
            if not filename or ".." in filename or "/" in filename or "\\" in filename:
                self.serve_error_json(400, "Invalid filename")
                return
            article_dir = article_dir_for(article_id)
            file_path = article_dir / "attachments" / filename
            if file_path.exists() and file_path.is_file():
                file_path.unlink()
                self.serve_json({"status": "ok"})
            else:
                self.serve_error_json(404, "File not found")
        except Exception as e:
            self.serve_error_json(500, str(e))
    def handle_get_workspaces(self):
        try:
            import db_api
            ws = db_api.get_all_workspaces()
            self.serve_json({"workspaces": ws})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_create_workspace(self):
        try:
            body = self.read_json_body()
            name = str(body.get("name") or "Unnamed Workspace").strip()[:100]
            ws_id = f"ws_{int(time.time())}_{os.urandom(2).hex()}"
            import db_api
            ws = db_api.add_workspace(ws_id, name)
            self.serve_json(ws)
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_delete_workspace(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            ws_id = urllib.parse.unquote(parts[-1])
            import db_api
            db_api.delete_workspace(ws_id)
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_get_workspace_items(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            ws_id = urllib.parse.unquote(parts[-2])
            import db_api
            items = db_api.get_workspace_items(ws_id)
            self.serve_json({"items": items})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_add_workspace_items(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            ws_id = urllib.parse.unquote(parts[-2])
            body = self.read_json_body()
            items = body.get("items", [])
            import db_api
            for item in items:
                db_api.add_item_to_workspace(ws_id, item["item_id"], item["item_type"])
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_remove_workspace_items(self):
        try:
            parts = urllib.parse.urlsplit(self.path).path.rstrip("/").split("/")
            ws_id = urllib.parse.unquote(parts[-2])
            body = self.read_json_body()
            items = body.get("items", [])
            import db_api
            for item_id in items:
                db_api.remove_item_from_workspace(ws_id, item_id)
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_batch_delete(self):
        try:
            body = self.read_json_body()
            items = body.get("items", [])
            import db_api
            for item in items:
                item_id = item["item_id"]
                if item["item_type"] == "paper":
                    db_api.delete_article(item_id)
                    article_dir = article_dir_for(item_id)
                    if article_dir.exists():
                        shutil.rmtree(article_dir)
                elif item["item_type"] == "note":
                    md_path = note_file_for(item_id)
                    if md_path.exists():
                        md_path.unlink()
                    db_api.delete_note(item_id)
            self.serve_json({"status": "ok"})
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_batch_export(self):
        try:
            body = self.read_json_body()
            items = body.get("items", [])
            
            import zipfile
            import io
            zip_buffer = io.BytesIO()
            
            with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
                for item in items:
                    item_id = item["item_id"]
                    if item["item_type"] == "paper":
                        article_dir = article_dir_for(item_id)
                        md_file = article_dir / f"{item_id}.md"
                        pdf_file = article_dir / "original.pdf"
                        if md_file.exists():
                            zip_file.write(md_file, f"papers/{item_id}/{item_id}.md")
                        if pdf_file.exists():
                            zip_file.write(pdf_file, f"papers/{item_id}/original.pdf")
                    elif item["item_type"] == "note":
                        md_path = note_file_for(item_id)
                        if md_path.exists():
                            zip_file.write(md_path, f"notes/{item_id}.md")
                            
            zip_data = zip_buffer.getvalue()
            filename = f"kbase_export_{int(time.time())}.zip"
            self.serve_download(zip_data, filename, "application/zip")
        except Exception as e:
            self.serve_error_json(500, str(e))

    def handle_batch_import(self):
        # Batch import implementation would handle multipart/form-data with multiple files.
        # It parses them, saves to notes dir, extracts frontmatter and adds to DB.
        # Since this involves parsing multipart, we can use cgi.FieldStorage or just return not implemented.
        self.serve_error_json(501, "Batch import not fully implemented yet in backend")

def start_server():
    """Start the HTTP server. Returns the httpd instance."""
    print(f" Knowledge Base Server")
    print(f" Directory: {DIR}")
    idx = scan_articles(load_index())
    print(f" Articles: {len(idx['articles'])}")
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
