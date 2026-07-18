"""Unified data storage layer.

Resolves the runtime data root directory and provides a single SQLite-backed
store for articles, notes, tags, workspaces, conversion history, translations
and chat sessions. The legacy kb-index.json / notes_index.json /
library_chat_sessions.json files are no longer read or written by the app.

Data root resolution (no environment variable overrides):
- New packaged installs: ``~/Documents/KBase``
- Existing packaged installs with data beside the executable keep that path
- Source run: ``<repo root>/data`` (parent of the ``kb`` package)

The data root is created on first access. A minimal ``local.env`` is generated
on first launch with empty values for the eight known keys.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Iterator

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_KB_PKG_DIR = Path(__file__).resolve().parent

if getattr(sys, "frozen", False):
    _REPO_ROOT = Path(sys.executable).resolve().parent
else:
    _REPO_ROOT = _KB_PKG_DIR.parent

REPO_ROOT: Path = _REPO_ROOT

if sys.platform == "win32":
    _CONFIG_ROOT = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kbase"
else:
    _CONFIG_ROOT = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kbase"

_DATA_PATH_FILE: Path = _CONFIG_ROOT / "data_path.txt"
_LEGACY_DATA_PATH_FILE: Path = _REPO_ROOT / "data_path.txt"


def _packaged_default_root() -> Path:
    """Choose a writable user workspace while preserving existing installs."""
    legacy = _REPO_ROOT / "data"
    articles = legacy / "articles"
    try:
        has_articles = articles.is_dir() and next(articles.iterdir(), None) is not None
    except OSError:
        has_articles = False
    if (legacy / ".kbase" / "index.db").exists() or (legacy / "local.env").exists() or has_articles:
        return legacy
    return Path.home() / "Documents" / "KBase"


_STATIC_DEFAULT_ROOT = _packaged_default_root() if getattr(sys, "frozen", False) else _REPO_ROOT / "data"

# Honour a user-chosen data root override (written by set_data_root).
_DATA_ROOT_OVERRIDE: Path | None = None
_override_file = _DATA_PATH_FILE if _DATA_PATH_FILE.exists() else _LEGACY_DATA_PATH_FILE
if _override_file.exists():
    try:
        candidate = Path(_override_file.read_text(encoding="utf-8").strip())
        if candidate.is_absolute():
            _DATA_ROOT_OVERRIDE = candidate
    except Exception:
        pass

# When KBase is installed system-wide (e.g. via a Linux distro package that
# drops the code under /opt/kbase or /usr/lib/kbase), the repo root is read-only
# but the user still needs a writable data directory. Fall back to the
# XDG-compliant per-user data dir in that case. Detection is intentionally
# conservative: only triggers when the repo root itself is unwritable.
if _DATA_ROOT_OVERRIDE is None and not getattr(sys, "frozen", False):
    try:
        # If we can write a probe file, the repo root is fine — keep default.
        probe = _REPO_ROOT / ".kbase_write_probe"
        probe.touch(exist_ok=True)
        probe.unlink(missing_ok=True)
    except (OSError, PermissionError):
        _xdg = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
        _user_data = Path(_xdg) / "kbase" / "data"
        try:
            _user_data.mkdir(parents=True, exist_ok=True)
            _DATA_ROOT_OVERRIDE = _user_data
        except Exception:
            pass

DATA_ROOT: Path = _DATA_ROOT_OVERRIDE or _STATIC_DEFAULT_ROOT
ARTICLES_DIR: Path = DATA_ROOT / "articles"
NOTES_DIR: Path = DATA_ROOT / "notes"
KBASE_DIR: Path = DATA_ROOT / ".kbase"
DB_PATH: Path = KBASE_DIR / "index.db"
LOGS_DIR: Path = KBASE_DIR / "logs"
CHAT_SESSIONS_DIR: Path = KBASE_DIR / "chat_sessions"
CHAT_SESSIONS_INDEX: Path = KBASE_DIR / "chat_sessions_index.json"
LOCAL_ENV: Path = DATA_ROOT / "local.env"
LLM_CONFIG_FILE: Path = DATA_ROOT / "llm_config.json"
LOW_MEMORY_CONFIG: Path = DATA_ROOT / "low_memory_config.json"


def resolve_literature_dir(root: Path) -> str:
    """Read ``literatureDir`` from workspace manifest, default ``.literature``."""
    manifest = root / ".kbase" / "workspace.json"
    if manifest.exists():
        try:
            import json

            data = json.loads(manifest.read_text(encoding="utf-8"))
            lit = str(data.get("literatureDir") or "").strip("/")
            if lit:
                return lit
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    if (root / ".literature").is_dir():
        return ".literature"
    if (root / "literature").is_dir():
        return "literature"
    if (root / "articles").is_dir():
        return "articles"
    return ".literature"


# aid → path relative to ARTICLES_DIR ("" means flat ARTICLES_DIR/<aid>)
_ARTICLE_DIR_RELS: dict[str, str] = {}


def _article_dirs_cache_path() -> Path:
    return KBASE_DIR / "article_dirs.json"


def load_article_dir_cache() -> dict[str, str]:
    """Load persisted article-dir relative paths (supports nested lit layout)."""
    global _ARTICLE_DIR_RELS
    path = _article_dirs_cache_path()
    if not path.exists():
        return dict(_ARTICLE_DIR_RELS)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            _ARTICLE_DIR_RELS = {
                str(k): str(v).replace("\\", "/").strip("/")
                for k, v in data.items()
                if k and isinstance(v, str)
            }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return dict(_ARTICLE_DIR_RELS)


def save_article_dir_cache(mapping: dict[str, str] | None = None) -> None:
    """Persist article-dir map atomically."""
    global _ARTICLE_DIR_RELS
    if mapping is not None:
        _ARTICLE_DIR_RELS = {
            str(k): str(v).replace("\\", "/").strip("/")
            for k, v in mapping.items()
            if k and isinstance(v, str)
        }
    try:
        KBASE_DIR.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(_article_dirs_cache_path(), _ARTICLE_DIR_RELS)
    except Exception:
        pass


def register_article_dir(article_id: str, folder: Path) -> None:
    """Remember where an article folder lives under ARTICLES_DIR."""
    aid = str(article_id or "").strip()
    if not aid:
        return
    try:
        base = ARTICLES_DIR.resolve()
        folder_r = folder.resolve()
        rel = folder_r.relative_to(base).as_posix()
    except (OSError, ValueError):
        rel = aid
    _ARTICLE_DIR_RELS[aid] = "" if rel == aid else rel


def resolve_article_dir(article_id: str, *, create: bool = False) -> Path:
    """Resolve article folder under ARTICLES_DIR (flat or nested)."""
    aid = str(article_id or "").strip()
    if not aid or aid in {".", ".."} or "/" in aid or "\\" in aid:
        raise ValueError("Invalid article id")

    if not _ARTICLE_DIR_RELS:
        load_article_dir_cache()

    rel = _ARTICLE_DIR_RELS.get(aid)
    if rel is not None:
        target = ARTICLES_DIR / rel if rel else ARTICLES_DIR / aid
        if target.is_dir() or create:
            if create:
                target.mkdir(parents=True, exist_ok=True)
            return target

    flat = ARTICLES_DIR / aid
    if flat.is_dir():
        register_article_dir(aid, flat)
        return flat

    # Slow fallback: walk once for nested layout.
    try:
        if ARTICLES_DIR.is_dir():
            for dirpath, dirnames, filenames in os.walk(ARTICLES_DIR):
                name = Path(dirpath).name
                if name != aid:
                    continue
                folder = Path(dirpath)
                if (
                    (folder / "original.pdf").exists()
                    or any(f.lower().endswith(".pdf") for f in filenames)
                    or (folder / f"{aid}_meta.json").exists()
                    or (folder / f"{aid}.md").exists()
                ):
                    register_article_dir(aid, folder)
                    save_article_dir_cache()
                    return folder
                # Don't descend into a matched-name folder further.
                dirnames[:] = []
    except OSError:
        pass

    if create:
        flat.mkdir(parents=True, exist_ok=True)
        register_article_dir(aid, flat)
        return flat
    return flat


def bind_data_root_runtime(root: Path, *, literature_dir: str | None = None) -> dict:
    """Switch the in-process data root (used when changing workspace)."""
    global DATA_ROOT, ARTICLES_DIR, NOTES_DIR, KBASE_DIR, DB_PATH, LOGS_DIR
    global CHAT_SESSIONS_DIR, CHAT_SESSIONS_INDEX, LOCAL_ENV, LLM_CONFIG_FILE, LOW_MEMORY_CONFIG
    global _ARTICLE_DIR_RELS

    resolved = Path(root).resolve()
    DATA_ROOT = resolved
    lit_name = (literature_dir or resolve_literature_dir(resolved)).strip("/") or ".literature"
    ARTICLES_DIR = DATA_ROOT / lit_name
    _ARTICLE_DIR_RELS = {}
    NOTES_DIR = DATA_ROOT / "notes"
    KBASE_DIR = DATA_ROOT / ".kbase"
    DB_PATH = KBASE_DIR / "index.db"
    LOGS_DIR = KBASE_DIR / "logs"
    CHAT_SESSIONS_DIR = KBASE_DIR / "chat_sessions"
    CHAT_SESSIONS_INDEX = KBASE_DIR / "chat_sessions_index.json"
    LOCAL_ENV = DATA_ROOT / "local.env"
    LLM_CONFIG_FILE = DATA_ROOT / "llm_config.json"
    LOW_MEMORY_CONFIG = DATA_ROOT / "low_memory_config.json"
    ensure_directories()
    init_db()
    load_local_env()
    load_article_dir_cache()
    try:
        import engines._paths as engine_paths

        engine_paths.ARTICLES_DIR = ARTICLES_DIR
        engine_paths.DATA_ROOT = DATA_ROOT
        engine_paths.LOW_MEMORY_CONFIG = LOW_MEMORY_CONFIG
    except ImportError:
        pass
    return get_data_root_info()


def default_data_root() -> Path:
    """The original default data root, unaffected by ``bind_data_root_runtime``.

    ``DATA_ROOT`` is rebound whenever a workspace is opened, so callers that
    need the *static* default (e.g. fallback selection when removing the
    active workspace) must use this instead of reading ``DATA_ROOT`` directly.
    """
    return Path(_DATA_ROOT_OVERRIDE or _STATIC_DEFAULT_ROOT)


# Static / read-only assets shipped with the package (index.html, assets/, ...)
PACKAGE_DIR: Path = _KB_PKG_DIR
STATIC_INDEX_HTML: Path = PACKAGE_DIR / "index.html"
STATIC_ASSETS: Path = PACKAGE_DIR / "assets"

# ---------------------------------------------------------------------------
# local.env bootstrap
# ---------------------------------------------------------------------------

LOCAL_ENV_TEMPLATE = """# KBase configuration - generated automatically on first launch.
# Fill in your API credentials below, or set them from the application's
# Settings page. Empty values mean the corresponding feature is disabled.

# OpenAI-compatible chat API
LLM_API_KEY=
LLM_API_URL=https://api.deepseek.com/v1/chat/completions
LLM_MODEL=deepseek-v4-flash

# Alibaba Cloud DocMind (RAM access with AK/SK)
DOCMIND_ACCESS_KEY_ID=
DOCMIND_ACCESS_KEY_SECRET=
DOCMIND_REGION=cn-hangzhou

# DeconBear DocParser (cloud GPU-accelerated PDF parsing)
DOCPARSER_API_URL=https://your-cloud-parser.com
DOCPARSER_API_KEY=
DOCPARSER_ENGINE=struct

# Cloud OCR PDF parsing (default conversion engine).
# OCR_PROVIDER_TYPE picks which cloud OCR to use:
#   qwen     — 通义千问 / 百炼 Qwen-VL-OCR (OpenAI-compatible chat/completions)
#   custom   — generic multipart HTTP endpoint (set OCR_API_URL + OCR_API_KEY)
#   unisound — 云知声 OCR; see the UNISOUND_* block below
OCR_PROVIDER_TYPE=qwen
OCR_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
OCR_API_KEY=
OCR_PROVIDER=qwen
OCR_MODEL=qwen-vl-ocr-latest
OCR_LANG=zh-CN+en

# Unisound U1 Doc Parser (Token Plan / 开放平台 统一接入).
# 异步 PDF 解析：上传文件 -> 提交任务 -> 轮询 -> 下载 Markdown
# Set UNISOUND_TOKEN_PLAN=1 if your key starts with "tp-" (Token Plan);
# leave 0 / blank for general API access.
UNISOUND_API_KEY=
UNISOUND_BASE_URL=https://maas-api.hivoice.cn
UNISOUND_MODEL=u1-ocr
UNISOUND_TOKEN_PLAN=0

# Per-task LLM routing (empty = use the global active provider/model
# in the LLM 模型 pane). Format: provider id + model name as configured
# in the LLM 模型 pane's provider/model lists.
CHAT_PROVIDER=
CHAT_MODEL=
TRANSLATION_PROVIDER=
TRANSLATION_MODEL=
"""


def ensure_directories() -> None:
    """Create the full data layout and generate local.env on first run."""
    for path in (
        DATA_ROOT,
        ARTICLES_DIR,
        NOTES_DIR,
        KBASE_DIR,
        KBASE_DIR / "databases",
        KBASE_DIR / "database_attachments",
        LOGS_DIR,
        CHAT_SESSIONS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    if not LOCAL_ENV.exists():
        LOCAL_ENV.write_text(LOCAL_ENV_TEMPLATE, encoding="utf-8")
    else:
        # Backfill: if a newer LOCAL_ENV_TEMPLATE has keys the existing file
        # doesn't know about, append them so the UI surfaces them and they
        # can be edited/saved. Existing user-set values are preserved.
        try:
            existing = LOCAL_ENV.read_text(encoding="utf-8")
        except Exception:
            existing = ""
        have = {line.split("=", 1)[0].strip()
                for line in existing.splitlines()
                if line.strip() and not line.strip().startswith("#") and "=" in line}
        missing: list[str] = []
        for tmpl_line in LOCAL_ENV_TEMPLATE.splitlines():
            stripped = tmpl_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in tmpl_line:
                continue
            key = tmpl_line.split("=", 1)[0].strip()
            if key not in have:
                missing.append(tmpl_line)
        if missing:
            with LOCAL_ENV.open("a", encoding="utf-8") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write("\n# --- keys backfilled by a newer version ---\n")
                for line in missing:
                    f.write(line + "\n")


def get_data_root_info() -> dict:
    """Return the current data root and whether it has been overridden."""
    return {
        "dataRoot": str(DATA_ROOT),
        "defaultDataRoot": str(default_data_root()),
        "installRoot": str(REPO_ROOT),
        "isPackaged": bool(getattr(sys, "frozen", False)),
        "isOverridden": _DATA_ROOT_OVERRIDE is not None,
        "isDefaultActive": DATA_ROOT.resolve() == default_data_root().resolve(),
        "isLegacyInstallRoot": bool(
            _DATA_ROOT_OVERRIDE is None
            and getattr(sys, "frozen", False)
            and _STATIC_DEFAULT_ROOT == (_REPO_ROOT / "data")
        ),
    }


def set_data_root(new_root: str) -> dict:
    """Persist a user-chosen data root override.

    Writes *new_root* to the per-user application configuration directory.
    The application must be restarted for the change to take effect on all
    modules because the path constants are resolved at import time.

    Returns a dict with ``{ok, message, dataRoot}`` suitable for the
    frontend.
    """
    global _DATA_ROOT_OVERRIDE

    if not str(new_root or "").strip():
        return {"ok": False, "message": "路径不能为空"}
    candidate = Path(new_root).expanduser().resolve()
    if not candidate.is_absolute():
        return {"ok": False, "message": "路径必须是绝对路径"}

    if candidate.exists() and not candidate.is_dir():
        return {"ok": False, "message": "路径必须是文件夹"}

    data_root_str = str(candidate)

    tmp = _DATA_PATH_FILE.with_suffix(".tmp")
    try:
        _DATA_PATH_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(data_root_str, encoding="utf-8")
        os.replace(tmp, _DATA_PATH_FILE)
    except OSError as e:
        return {"ok": False, "message": f"无法写入配置文件: {e}"}

    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    _DATA_ROOT_OVERRIDE = candidate

    return {
        "ok": True,
        "message": "数据路径已保存，请重启应用生效",
        "dataRoot": data_root_str,
        "needsRestart": True,
    }


def load_local_env() -> None:
    """Load env files into os.environ without overriding existing vars.

    Precedence (lowest to highest, i.e. later wins on collision only when the
    variable was not yet set in ``os.environ``):
      1. Repo-root ``.env``                 — committed? no (gitignored), for
         source-mode debug. Keys are NOT exposed to the Settings UI.
      2. Repo-root ``.env.local``           — local-only override of #1.
      3. ``data/local.env``                — managed by the Settings UI and
         the application itself.
    """
    sources: list[Path] = []
    for name in (".env", ".env.local"):
        candidate = _REPO_ROOT / name
        if candidate.exists() and candidate.is_file():
            sources.append(candidate)
    if LOCAL_ENV.exists():
        sources.append(LOCAL_ENV)
    for path in sources:
        try:
            _apply_env_file(path)
        except Exception:
            continue


def _apply_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # .env style allows quoted values; strip matching quotes.
        v = val.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        os.environ.setdefault(key.strip(), v)


def _write_local_env(updates: dict[str, str]) -> None:
    """Merge ``updates`` into local.env, preserving comments and ordering.

    Lines whose key appears in ``updates`` are rewritten in place; any
    unknown key is appended at the bottom of the file.

    Protected by ``_ENV_LOCK`` to prevent lost updates from concurrent
    settings saves (ThreadingTCPServer can serve multiple requests).
    """
    with _ENV_LOCK:
        ensure_directories()
        existing_lines: list[str] = []
        if LOCAL_ENV.exists():
            existing_lines = LOCAL_ENV.read_text(encoding="utf-8").splitlines()
        seen: set[str] = set()
        new_lines: list[str] = []
        for line in existing_lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in line:
                new_lines.append(line)
                continue
            key, _, _ = line.partition("=")
            key = key.strip()
            if key in updates:
                new_lines.append(f"{key}={updates[key]}")
                seen.add(key)
            else:
                new_lines.append(line)
        for key, value in updates.items():
            if key not in seen:
                new_lines.append(f"{key}={value}")
        tmp = LOCAL_ENV.with_suffix(".env.tmp")
        tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.replace(tmp, LOCAL_ENV)
        # refresh process env so subsequent LLM calls see the new value
        for key, value in updates.items():
            os.environ[key] = value


def public_local_env() -> dict[str, str]:
    """Return all configured env keys with their values masked."""
    if not LOCAL_ENV.exists():
        return {}
    out: dict[str, str] = {}
    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        out[key.strip()] = val.strip()
    return out


# ---------------------------------------------------------------------------
# SQLite layer
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
    id TEXT PRIMARY KEY,
    title TEXT,
    author TEXT,
    authors_json TEXT,
    pages INTEGER DEFAULT 0,
    date_added TEXT,
    category TEXT,
    doi TEXT,
    year TEXT,
    venue TEXT,
    abstract TEXT,
    translated INTEGER DEFAULT 0,
    has_old_translation INTEGER DEFAULT 0,
    summarized INTEGER DEFAULT 0,
    pdf_available INTEGER DEFAULT 0,
    md_available INTEGER DEFAULT 0,
    file_available INTEGER DEFAULT 0,
    converting INTEGER DEFAULT 0,
    source_filename TEXT,
    kind TEXT,
    metadata_extracted INTEGER DEFAULT 0,
    metadata_extracted_at TEXT,
    metadata_source TEXT,
    parser TEXT,
    preparse_error TEXT,
    folder_id TEXT,
    pdf_file TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT,
    modified_at TEXT,
    folder TEXT
);
-- article_id is added below by an idempotent ALTER TABLE check
-- (init_db). A note may be either a free-standing notebook note
-- (article_id IS NULL) or a "文章小记" scoped to a single paper
-- (article_id set). When set, the note is also discoverable from
-- the article's notes tab and the floating note window.

-- Notebook container: each note belongs to a notebook. A default
-- "Inbox" notebook is created for legacy data.
CREATE TABLE IF NOT EXISTS notebooks (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT,
    sort_order INTEGER DEFAULT 0,
    closed INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);

-- Note document tree: parent_id lets notes nest as Sub-document
-- under another note. Depth is bounded by the application (the
-- notebook -> document -> sub-document layer is intentional and
-- matches the notebook navigation model we are implementing).
-- The new columns notebook_id, parent_id, sort_order, doc_icon are
-- added by an idempotent ALTER TABLE check in init_db (see below
-- the schema block).

-- Block anchors: stable IDs for H1/H2/H3 headings so they can be
-- referenced from any other note via `[[note-id#anchor]]`. The
-- `anchor` field is a URL-safe slug derived from the heading text.
CREATE TABLE IF NOT EXISTS note_blocks (
    id TEXT PRIMARY KEY,
    note_id TEXT NOT NULL,
    anchor TEXT NOT NULL,
    heading TEXT,
    level INTEGER,
    sort_order INTEGER DEFAULT 0,
    UNIQUE(note_id, anchor)
);
CREATE INDEX IF NOT EXISTS idx_note_blocks_note ON note_blocks(note_id);
CREATE INDEX IF NOT EXISTS idx_note_blocks_anchor ON note_blocks(note_id, anchor);

-- Cross-note links: every `[[X]]` or `[[X#Y]]` in a note resolves
-- to a target_note_id (resolved from X by id or title) and an
-- optional target_anchor. The note_links table is the source of
-- truth for the backlinks panel.
CREATE TABLE IF NOT EXISTS note_links (
    source_note_id TEXT,
    source_anchor TEXT,
    target_note_id TEXT,
    target_anchor TEXT,
    raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_note_links_target ON note_links(target_note_id);
CREATE INDEX IF NOT EXISTS idx_note_links_source ON note_links(source_note_id);

CREATE TABLE IF NOT EXISTS tags (
    item_id TEXT,
    tag TEXT,
    item_type TEXT,
    UNIQUE(item_id, tag, item_type)
);

CREATE TABLE IF NOT EXISTS article_history (
    article_id TEXT,
    engine TEXT,
    file_path TEXT,
    updated_at TEXT,
    PRIMARY KEY (article_id, engine)
);

CREATE TABLE IF NOT EXISTS article_attachments (
    article_id TEXT,
    name TEXT,
    path TEXT,
    size INTEGER,
    mtime REAL,
    PRIMARY KEY (article_id, name)
);

CREATE TABLE IF NOT EXISTS translation_state (
    article_id TEXT PRIMARY KEY,
    status TEXT,
    percent REAL DEFAULT 0,
    current INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    message TEXT,
    started_at TEXT,
    completed_at TEXT,
    target_language TEXT,
    output_file TEXT
);

CREATE TABLE IF NOT EXISTS conversion_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id TEXT,
    engine TEXT,
    status TEXT,
    ts TEXT
);

CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS workspace_items (
    workspace_id TEXT,
    item_id TEXT,
    item_type TEXT,
    PRIMARY KEY (workspace_id, item_id),
    FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS article_folders (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    parent_id TEXT,
    icon TEXT,
    sort_order INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_article_folders_parent ON article_folders(parent_id);

CREATE INDEX IF NOT EXISTS idx_articles_date_added ON articles(date_added);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_translation_article ON translation_state(article_id);
CREATE INDEX IF NOT EXISTS idx_conv_history_article ON conversion_history(article_id);

CREATE TABLE IF NOT EXISTS databases (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    icon TEXT,
    description TEXT,
    file_path TEXT NOT NULL,
    row_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 1,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS database_cell_index (
    database_id TEXT NOT NULL,
    row_id TEXT NOT NULL,
    column_id TEXT NOT NULL,
    text_value TEXT,
    PRIMARY KEY (database_id, row_id, column_id)
);
CREATE INDEX IF NOT EXISTS idx_database_cell_text ON database_cell_index(text_value);
"""

_BOOL_FIELDS = {
    "translated",
    "has_old_translation",
    "summarized",
    "pdf_available",
    "md_available",
    "file_available",
    "converting",
    "metadata_extracted",
}

_CONNECT_LOCK = threading.Lock()
_ENV_LOCK = threading.Lock()  # protects read-modify-write in _write_local_env


def _connect() -> sqlite3.Connection:
    ensure_directories()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def init_db() -> None:
    """Create tables if missing. Idempotent and thread-safe."""
    ensure_directories()
    with _CONNECT_LOCK:
        conn = _connect()
        try:
            conn.executescript(_SCHEMA)
            # Additive migrations: apply ALTER TABLE statements only if
            # the target column does not already exist. exec/except
            # per column keeps the script idempotent.
            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(notes)").fetchall()
            }
            for col, decl in (
                ("notebook_id", "TEXT REFERENCES notebooks(id)"),
                ("parent_id", "TEXT"),
                ("sort_order", "INTEGER DEFAULT 0"),
                ("doc_icon", "TEXT"),
                # article_id links a note to a specific paper. The note
                # then appears in that article's notes tab; the same
                # note can still @-mention other articles.
                ("article_id", "TEXT"),
            ):
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {decl}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notes_article ON notes(article_id)")
            art_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(articles)").fetchall()
            }
            if "folder_id" not in art_cols:
                conn.execute("ALTER TABLE articles ADD COLUMN folder_id TEXT")
            if "pdf_file" not in art_cols:
                conn.execute("ALTER TABLE articles ADD COLUMN pdf_file TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_articles_folder ON articles(folder_id)")
        finally:
            conn.close()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Context-managed connection. Re-uses init_db on first use."""
    init_db()
    conn = _connect()
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Article helpers
# ---------------------------------------------------------------------------


def _row_to_article(row: sqlite3.Row) -> dict[str, Any]:
    art = dict(row)
    art.pop("authors_json", None)
    authors_raw = row["authors_json"] if "authors_json" in row.keys() else ""
    try:
        art["authors"] = json.loads(authors_raw) if authors_raw else []
    except (json.JSONDecodeError, TypeError):
        art["authors"] = []
    for k in _BOOL_FIELDS:
        if k in art:
            art[k] = bool(art[k])
    return art


_ARTICLE_DB_COLUMNS = {
    "id",
    "title",
    "author",
    "authors_json",
    "pages",
    "date_added",
    "category",
    "doi",
    "year",
    "venue",
    "abstract",
    "translated",
    "has_old_translation",
    "summarized",
    "pdf_available",
    "md_available",
    "file_available",
    "converting",
    "source_filename",
    "kind",
    "metadata_extracted",
    "metadata_extracted_at",
    "metadata_source",
    "parser",
    "preparse_error",
    "folder_id",
    "pdf_file",
}


def _article_to_values(article: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    authors = article.get("authors")
    if isinstance(authors, list):
        out["authors_json"] = json.dumps(authors, ensure_ascii=False)
    else:
        out["authors_json"] = "[]"
    for k, v in article.items():
        if k in {"authors", "tags"}:
            continue
        if k not in _ARTICLE_DB_COLUMNS:
            continue
        if k in _BOOL_FIELDS:
            out[k] = 1 if v else 0
        elif k == "pages":
            try:
                out[k] = int(v or 0)
            except (TypeError, ValueError):
                out[k] = 0
        else:
            out[k] = v if v is not None else ""
    return out


def get_all_articles() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM articles").fetchall()
        articles = [_row_to_article(r) for r in rows]
        for art in articles:
            tag_rows = conn.execute(
                "SELECT tag FROM tags WHERE item_id=? AND item_type='paper'",
                (art["id"],),
            ).fetchall()
            art["tags"] = [t["tag"] for t in tag_rows]
    return articles


def get_article(article_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM articles WHERE id=?", (article_id,)
        ).fetchone()
        if not row:
            return None
        art = _row_to_article(row)
        tag_rows = conn.execute(
            "SELECT tag FROM tags WHERE item_id=? AND item_type='paper'",
            (art["id"],),
        ).fetchall()
        art["tags"] = [t["tag"] for t in tag_rows]
    return art


def upsert_article(article: dict[str, Any]) -> None:
    aid = article.get("id")
    if not aid:
        raise ValueError("Article id is required")
    payload = {k: v for k, v in article.items() if k != "tags"}
    values = _article_to_values(payload)
    columns = list(values.keys())
    placeholders = ", ".join("?" for _ in columns)
    update_clause = ", ".join(
        f"{c}=excluded.{c}" for c in columns if c != "id"
    )
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO articles ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {update_clause}",
            list(values.values()),
        )
        conn.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
        for tag in article.get("tags") or []:
            tag = str(tag).strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (aid, tag, "paper"),
                )


def update_article_fields(article_id: str, updates: dict[str, Any]) -> None:
    """Update a subset of columns. Authors and tags are handled separately."""
    if not updates:
        return
    updates = dict(updates)
    tags_value = updates.pop("tags", None)

    column_updates = {
        k: v
        for k, v in updates.items()
        if k
        in {
            "title",
            "author",
            "pages",
            "date_added",
            "category",
            "doi",
            "year",
            "venue",
            "abstract",
            "translated",
            "summarized",
            "pdf_available",
            "md_available",
            "file_available",
            "converting",
            "source_filename",
            "kind",
            "metadata_extracted",
            "metadata_extracted_at",
            "metadata_source",
            "parser",
            "preparse_error",
            "has_old_translation",
            "folder_id",
            "pdf_file",
        }
    }
    if column_updates:
        values = _article_to_values({**column_updates, "id": article_id})
        # authors_json may have been added by _article_to_values; drop it here
        values.pop("authors_json", None)
        assignments = ", ".join(f"{k}=?" for k in values)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE articles SET {assignments} WHERE id=?",
                [*values.values(), article_id],
            )

    if tags_value is not None:
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM tags WHERE item_id=? AND item_type='paper'",
                (article_id,),
            )
            for tag in tags_value or []:
                tag = str(tag).strip()
                if tag:
                    conn.execute(
                        "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                        (article_id, tag, "paper"),
                    )


def delete_article(article_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM articles WHERE id=?", (article_id,))
        conn.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (article_id,))
        conn.execute("DELETE FROM workspace_items WHERE item_id=?", (article_id,))
        conn.execute("DELETE FROM translation_state WHERE article_id=?", (article_id,))
        conn.execute("DELETE FROM conversion_history WHERE article_id=?", (article_id,))
        conn.execute("DELETE FROM article_history WHERE article_id=?", (article_id,))
        conn.execute("DELETE FROM article_attachments WHERE article_id=?", (article_id,))


def replace_article_tags(article_id: str, tags: Iterable[str]) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (article_id,))
        for tag in tags or []:
            tag = str(tag).strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (article_id, tag, "paper"),
                )


# ---------------------------------------------------------------------------
# Article history & attachments
# ---------------------------------------------------------------------------


def record_article_history(article_id: str, engine: str, file_path: Path) -> None:
    rel = str(file_path)
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO article_history (article_id, engine, file_path, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(article_id, engine) DO UPDATE SET
                 file_path=excluded.file_path,
                 updated_at=excluded.updated_at""",
            (article_id, engine, rel, time.strftime("%Y-%m-%d %H:%M:%S")),
        )


def list_article_history(article_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT engine, file_path, updated_at FROM article_history WHERE article_id=?",
            (article_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_article_history(article_id: str, engine: str) -> None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT file_path FROM article_history WHERE article_id=? AND engine=?",
            (article_id, engine),
        ).fetchone()
        if row:
            try:
                Path(row["file_path"]).unlink(missing_ok=True)
            except OSError:
                pass
        conn.execute(
            "DELETE FROM article_history WHERE article_id=? AND engine=?",
            (article_id, engine),
        )


def list_article_attachments(article_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name, path, size, mtime FROM article_attachments WHERE article_id=? ORDER BY mtime DESC",
            (article_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_article_attachment(article_id: str, name: str, path: Path) -> None:
    try:
        stat = path.stat()
    except OSError:
        size, mtime = 0, time.time()
    else:
        size, mtime = stat.st_size, stat.st_mtime
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO article_attachments (article_id, name, path, size, mtime)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(article_id, name) DO UPDATE SET
                 path=excluded.path, size=excluded.size, mtime=excluded.mtime""",
            (article_id, name, str(path), size, mtime),
        )


def delete_article_attachment(article_id: str, name: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT path FROM article_attachments WHERE article_id=? AND name=?",
            (article_id, name),
        ).fetchone()
        if not row:
            return False
        try:
            Path(row["path"]).unlink(missing_ok=True)
        except OSError:
            pass
        conn.execute(
            "DELETE FROM article_attachments WHERE article_id=? AND name=?",
            (article_id, name),
        )
        return True


# ---------------------------------------------------------------------------
# Translation & conversion history
# ---------------------------------------------------------------------------


def save_translation_state(article_id: str, **fields: Any) -> None:
    allowed = {
        "status",
        "percent",
        "current",
        "total",
        "message",
        "started_at",
        "completed_at",
        "target_language",
        "output_file",
    }
    payload = {k: v for k, v in fields.items() if k in allowed}
    if not payload:
        return
    columns = ["article_id"] + list(payload.keys())
    placeholders = ", ".join("?" for _ in columns)
    update_clause = ", ".join(f"{k}=excluded.{k}" for k in payload)
    with get_conn() as conn:
        conn.execute(
            f"INSERT INTO translation_state ({', '.join(columns)}) VALUES ({placeholders}) "
            f"ON CONFLICT(article_id) DO UPDATE SET {update_clause}",
            [article_id, *payload.values()],
        )


def load_translation_state(article_id: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM translation_state WHERE article_id=?", (article_id,)
        ).fetchone()
    return dict(row) if row else None


def record_conversion(article_id: str, engine: str, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO conversion_history (article_id, engine, status, ts) VALUES (?, ?, ?, ?)",
            (article_id, engine, status, time.strftime("%Y-%m-%d %H:%M:%S")),
        )


def list_conversion_history(article_id: str, limit: int = 50) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT engine, status, ts FROM conversion_history WHERE article_id=? "
            "ORDER BY id DESC LIMIT ?",
            (article_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def get_all_notes() -> list[dict[str, Any]]:
    ensure_default_notebook()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notes ORDER BY datetime(modified_at) DESC"
        ).fetchall()
        notes = [dict(r) for r in rows]
        for n in notes:
            tag_rows = conn.execute(
                "SELECT tag FROM tags WHERE item_id=? AND item_type='note'",
                (n["id"],),
            ).fetchall()
            n["tags"] = [t["tag"] for t in tag_rows]
    # Legacy notes without notebook_id are placed in Inbox.
    for n in notes:
        if not n.get("notebook_id"):
            n["notebook_id"] = DEFAULT_NOTEBOOK_ID
    return notes


def upsert_note(note: dict[str, Any]) -> None:
    nid = note.get("id")
    if not nid:
        raise ValueError("Note id is required")
    ensure_default_notebook()
    nb_id = note.get("notebook_id") or DEFAULT_NOTEBOOK_ID
    with get_conn() as conn:
        # Confirm the notebook exists (legacy notes may have been
        # migrated without one).
        row = conn.execute("SELECT id FROM notebooks WHERE id=?", (nb_id,)).fetchone()
        if not row:
            nb_id = DEFAULT_NOTEBOOK_ID
        conn.execute(
            """INSERT INTO notes (id, title, created_at, modified_at, folder,
                                 notebook_id, parent_id, sort_order, doc_icon,
                                 article_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 created_at=excluded.created_at,
                 modified_at=excluded.modified_at,
                 folder=excluded.folder,
                 notebook_id=excluded.notebook_id,
                 parent_id=excluded.parent_id,
                 sort_order=excluded.sort_order,
                 doc_icon=excluded.doc_icon,
                 article_id=excluded.article_id""",
            (
                nid,
                note.get("title", ""),
                note.get("created_at", ""),
                note.get("modified_at", ""),
                note.get("folder", ""),
                nb_id,
                note.get("parent_id"),
                int(note.get("sort_order") or 0),
                note.get("doc_icon", ""),
                note.get("article_id") or None,
            ),
        )
        conn.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
        for tag in note.get("tags") or []:
            tag = str(tag).strip()
            if tag:
                conn.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (nid, tag, "note"),
                )


def delete_note(nid: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM notes WHERE id=?", (nid,))
        conn.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
        conn.execute("DELETE FROM workspace_items WHERE item_id=?", (nid,))
        conn.execute("DELETE FROM note_blocks WHERE note_id=?", (nid,))


def get_notes_for_article(article_id: str) -> list[dict[str, Any]]:
    """Return every note that references the given article — either
    via notes.article_id (a scoped "文章小记") or via a `[[art-...]]`
    mention in the saved markdown (read from the on-disk .md file
    so we don't have to materialize every note's content into SQL).

    The two lists are de-duplicated by note id and sorted by
    modified_at DESC.
    """
    scoped_rows: list[dict[str, Any]] = []
    with get_conn() as conn:
        for r in conn.execute(
            "SELECT * FROM notes WHERE article_id=? ORDER BY datetime(modified_at) DESC",
            (article_id,),
        ).fetchall():
            scoped_rows.append(dict(r))
    # Mentions: walk data/notes/<id>.md looking for either
    # `[[art-link:<article_id>]]` (the new at-article-mention
    # syntax) or a bare `[[<article_id>]]` (legacy — matches the
    # raw id). Cheap because we only read each file once and the
    # directory is modest in size.
    seen = {n["id"] for n in scoped_rows}
    notes_dir = DATA_ROOT / "notes"
    if notes_dir.is_dir():
        for md_path in notes_dir.glob("*.md"):
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            matched = ("[[art-link:" + article_id + "]]" in text) or \
                      ("[[ " + article_id) in text or ("[[" + article_id + "]]" in text) or \
                      (("[[" + article_id + "#") in text)
            if not matched:
                continue
            nid = md_path.stem
            if nid in seen:
                continue
            # Pull the row from SQLite so we get title/timestamps/tags.
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT * FROM notes WHERE id=?", (nid,)
                ).fetchone()
                if not row:
                    continue
                note = dict(row)
                tag_rows = conn.execute(
                    "SELECT tag FROM tags WHERE item_id=? AND item_type='note'",
                    (nid,),
                ).fetchall()
            note["tags"] = [t["tag"] for t in tag_rows]
            if not note.get("notebook_id"):
                note["notebook_id"] = DEFAULT_NOTEBOOK_ID
            scoped_rows.append(note)
            seen.add(nid)
    scoped_rows.sort(key=lambda n: n.get("modified_at") or "", reverse=True)
    return scoped_rows


def get_article_note_count(article_id: str) -> int:
    """Cheap count for the article header badge."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notes WHERE article_id=?",
            (article_id,),
        ).fetchone()
    return int(row["c"]) if row else 0


# ---------------------------------------------------------------------------
# Notebooks
# ---------------------------------------------------------------------------

DEFAULT_NOTEBOOK_ID = "nb_default"


def ensure_default_notebook() -> None:
    """Create the built-in 'Inbox' notebook on first launch."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM notebooks WHERE id=?", (DEFAULT_NOTEBOOK_ID,)
        ).fetchone()
        if row:
            return
        conn.execute(
            """INSERT INTO notebooks (id, name, icon, sort_order, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                DEFAULT_NOTEBOOK_ID,
                "Inbox",
                "📥",
                0,
                time.strftime("%Y-%m-%d %H:%M:%S"),
                time.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def list_notebooks() -> list[dict[str, Any]]:
    ensure_default_notebook()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM notebooks ORDER BY sort_order, name"
        ).fetchall()
    return [dict(r) for r in rows]


def upsert_notebook(nb: dict[str, Any]) -> None:
    nb_id = nb.get("id") or f"nb_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
    fields = {
        "id": nb_id,
        "name": nb.get("name") or "Untitled",
        "icon": nb.get("icon") or "📓",
        "sort_order": int(nb.get("sort_order") or 0),
        "closed": int(nb.get("closed") or 0),
        "created_at": nb.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO notebooks (id, name, icon, sort_order, closed, created_at, updated_at)
               VALUES (:id, :name, :icon, :sort_order, :closed, :created_at, :updated_at)
               ON CONFLICT(id) DO UPDATE SET
                 name=excluded.name, icon=excluded.icon, sort_order=excluded.sort_order,
                 closed=excluded.closed, updated_at=excluded.updated_at""",
            fields,
        )


def delete_notebook(nb_id: str) -> None:
    if nb_id == DEFAULT_NOTEBOOK_ID:
        return  # protected
    with get_conn() as conn:
        # Move any notes in this notebook to the default notebook.
        conn.execute(
            "UPDATE notes SET notebook_id=? WHERE notebook_id=?",
            (DEFAULT_NOTEBOOK_ID, nb_id),
        )
        conn.execute("DELETE FROM notebooks WHERE id=?", (nb_id,))


# ---------------------------------------------------------------------------
# Note block anchors
# ---------------------------------------------------------------------------


def _slugify_anchor(text: str) -> str:
    """URL-safe slug used as a stable anchor inside a note."""
    s = re.sub(r"[^\w\s一-鿿-]", "", text or "").strip().lower()
    s = re.sub(r"[\s_]+", "-", s)
    return s[:80] or "section"


_BLOCK_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$", re.M)


def sync_note_blocks(note_id: str, content: str) -> list[dict[str, Any]]:
    """Parse H1-H3 headings out of markdown content, generate unique
    anchors per heading, persist them in note_blocks, and rewrite the
    stored content with stable anchor markers so the frontend can
    recover them after Vditor's markdown round-trip.

    The marker is a small HTML comment `<!--kb-block:anchor-->` placed
    immediately after the heading line. Vditor leaves HTML comments
    in `data-block` wrappers intact during round-trip, so this stays
    stable across edits.

    Returns the full set of blocks currently in the note so the
    frontend can render an outline panel and validate inbound
    `[[note-id#anchor]]` links.
    """
    seen: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for i, match in enumerate(_BLOCK_RE.finditer(content or "")):
        level = len(match.group(1))
        heading = match.group(2).strip()
        # Strip a trailing {#slug} if present.
        explicit = re.search(r"\{#([\w一-鿿-]+)\}\s*$", heading)
        if explicit:
            anchor = explicit.group(1)
            heading = heading[: explicit.start()].strip()
        else:
            anchor = _slugify_anchor(heading)
        count = seen.get(anchor, 0)
        seen[anchor] = count + 1
        if count:
            anchor = f"{anchor}-{count + 1}"
        rows.append({
            "note_id": note_id,
            "anchor": anchor,
            "heading": heading,
            "level": level,
            "sort_order": i,
        })

    with get_conn() as conn:
        conn.execute("DELETE FROM note_blocks WHERE note_id=?", (note_id,))
        for r in rows:
            block_id = f"blk_{note_id}_{r['anchor']}"
            conn.execute(
                """INSERT INTO note_blocks (id, note_id, anchor, heading, level, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                     heading=excluded.heading, level=excluded.level,
                     sort_order=excluded.sort_order""",
                (block_id, r["note_id"], r["anchor"], r["heading"], r["level"], r["sort_order"]),
            )
    return rows


_BLOCK_MARKER = re.compile(r"<!--\s*kb-block:([\w一-鿿-]+)\s*-->")


def inject_block_anchors(content: str, rows: list[dict[str, Any]]) -> str:
    """Insert or update stable `<!--kb-block:anchor-->` markers after
    each heading in the content.

    We walk headings in order, and for each one we look for an
    existing marker (either just below it, or elsewhere in the file
    carrying the same anchor) and either move it into place or
    insert a new one. Blocks whose anchor no longer corresponds to a
    current heading have their markers stripped.
    """
    if not content:
        return content
    # 1. Strip every existing kb-block marker from the source.
    stripped = _BLOCK_MARKER.sub("", content)
    # 2. Re-insert markers just after each H1-H3 line, in the same
    #    order they appear in `rows`.
    out_lines: list[str] = []
    by_anchor = {r["anchor"]: r for r in rows}
    pending_markers: list[str] = []
    pending_iter = iter(rows)
    in_code = False
    line_index = 0
    for raw_line in stripped.split("\n"):
        out_lines.append(raw_line)
        # Toggle code-fence state.
        if re.match(r"^```", raw_line):
            in_code = not in_code
        if not in_code and re.match(r"^#{1,3}\s+", raw_line):
            # Place any matching row here.
            try:
                row = next(pending_iter)
            except StopIteration:
                row = None
            if row:
                out_lines.append(f"<!--kb-block:{row['anchor']}-->")
        line_index += 1
    return "\n".join(out_lines)


def sync_note_links(note_id: str, content: str) -> list[dict[str, Any]]:
    """Parse `[[X]]`, `[[X#Y]]`, and `[[art-link:<article-id>]]`
    patterns in content, resolve the target against either the notes
    table (by id or title) or the articles table, and store
    resolved pairs in `note_links`. The table is wiped for this
    note first so deleted references don't linger.
    """
    pattern = re.compile(r"\[\[([^\]]+)\]\]")
    out: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    with get_conn() as conn:
        for match in pattern.finditer(content or ""):
            target = match.group(1).strip()
            if not target:
                continue
            # Article back-link: [[art-link:<article-id>]]
            if target.startswith("art-link:"):
                article_id = target[len("art-link:"):].strip()
                if not article_id:
                    continue
                row = conn.execute(
                    "SELECT 1 FROM articles WHERE id=? LIMIT 1", (article_id,)
                ).fetchone()
                if not row:
                    continue
                # Use a synthetic negative id so note_links.target_note_id
                # (which is a TEXT column, not FK-constrained) can carry
                # both note and article references in one table.
                key = (f"art:{article_id}", "")
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                out.append({
                    "source_note_id": note_id,
                    "source_anchor": None,
                    "target_note_id": f"art:{article_id}",
                    "target_anchor": None,
                    "raw": target,
                })
                continue
            if "#" in target:
                target_id_raw, target_anchor = target.split("#", 1)
                target_anchor = target_anchor.strip()
            else:
                target_id_raw, target_anchor = target, None
            target_id_raw = target_id_raw.strip()
            if not target_id_raw:
                continue
            # Try to resolve: by id first, then by title.
            row = conn.execute(
                "SELECT id FROM notes WHERE id=? OR title=? LIMIT 1",
                (target_id_raw, target_id_raw),
            ).fetchone()
            if not row:
                continue
            resolved_id = row["id"]
            if target_anchor:
                anchor_row = conn.execute(
                    "SELECT 1 FROM note_blocks WHERE note_id=? AND LOWER(anchor)=LOWER(?)",
                    (resolved_id, target_anchor),
                ).fetchone()
                if not anchor_row:
                    target_anchor = None  # unknown anchor, store as link-only
            key = (resolved_id, target_anchor or "")
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            out.append({
                "source_note_id": note_id,
                "source_anchor": None,
                "target_note_id": resolved_id,
                "target_anchor": target_anchor,
                "raw": target,
            })
        # Rewrite the table for this note.
        conn.execute("DELETE FROM note_links WHERE source_note_id=?", (note_id,))
        for link in out:
            conn.execute(
                """INSERT INTO note_links
                       (source_note_id, source_anchor, target_note_id, target_anchor, raw)
                   VALUES (?, ?, ?, ?, ?)""",
                (link["source_note_id"], link["source_anchor"],
                 link["target_note_id"], link["target_anchor"], link["raw"]),
            )
    return out


def get_note_backlinks(note_id: str) -> list[dict[str, Any]]:
    """Return notes that link TO this note, with link metadata."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT n.id, n.title, nl.target_anchor, b.heading, b.anchor
               FROM note_links nl
               JOIN notes n ON n.id = nl.source_note_id
               LEFT JOIN note_blocks b
                 ON b.note_id = nl.target_note_id
                AND LOWER(b.anchor) = LOWER(nl.target_anchor)
               WHERE nl.target_note_id = ?
               ORDER BY b.sort_order NULLS LAST, n.modified_at DESC""",
            (note_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_note_blocks(note_id: str) -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT anchor, heading, level FROM note_blocks WHERE note_id=? ORDER BY sort_order",
            (note_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def find_note_block(note_id: str, anchor: str) -> dict[str, Any] | None:
    """Resolve a (note_id, anchor) pair back to a block row.

    Used by `[[note-id#anchor]]` backlink resolution on the server
    side to verify the target exists before storing the link.
    """
    if not note_id or not anchor:
        return None
    with get_conn() as conn:
        row = conn.execute(
            "SELECT anchor, heading, level FROM note_blocks WHERE note_id=? AND anchor=?",
            (note_id, anchor),
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Workspaces
# ---------------------------------------------------------------------------


def get_workspace_items(workspace_id: str) -> list[dict[str, Any]]:
    if not workspace_id:
        return []
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?",
            (workspace_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_workspaces() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM workspaces ORDER BY datetime(created_at) DESC"
        ).fetchall()
        out = []
        for r in rows:
            ws = dict(r)
            items = conn.execute(
                "SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?",
                (ws["id"],),
            ).fetchall()
            ws["items"] = [dict(i) for i in items]
            out.append(ws)
    return out


def upsert_workspace(ws_id: str, name: str) -> dict[str, Any]:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO workspaces (id, name, created_at) VALUES (?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET name=excluded.name""",
            (ws_id, name, ts),
        )
    return {"id": ws_id, "name": name, "created_at": ts, "items": []}


def delete_workspace(ws_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM workspace_items WHERE workspace_id=?", (ws_id,))
        conn.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))


def add_item_to_workspace(ws_id: str, item_id: str, item_type: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO workspace_items (workspace_id, item_id, item_type) VALUES (?, ?, ?)",
            (ws_id, item_id, item_type),
        )


def remove_item_from_workspace(ws_id: str, item_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM workspace_items WHERE workspace_id=? AND item_id=?",
            (ws_id, item_id),
        )


# ---------------------------------------------------------------------------
# Chat sessions
# ---------------------------------------------------------------------------


def _session_path(sid: str) -> Path:
    return CHAT_SESSIONS_DIR / f"{sid}.json"


def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON to a temp file and atomically rename. Crash-safe."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def list_chat_sessions() -> dict[str, Any]:
    ensure_directories()
    if not CHAT_SESSIONS_INDEX.exists():
        return {"active_session_id": "", "sessions": []}
    try:
        data = json.loads(CHAT_SESSIONS_INDEX.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        data = {"active_session_id": "", "sessions": []}
    if not isinstance(data, dict):
        data = {"active_session_id": "", "sessions": []}
    data.setdefault("active_session_id", "")
    data.setdefault("sessions", [])
    return data


def save_chat_index(state: dict[str, Any]) -> None:
    _atomic_write_json(CHAT_SESSIONS_INDEX, state)


def load_chat_session_file(sid: str) -> dict[str, Any]:
    path = _session_path(sid)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_chat_session_file(sid: str, data: dict[str, Any]) -> None:
    _atomic_write_json(_session_path(sid), data)


def delete_chat_session_file(sid: str) -> None:
    path = _session_path(sid)
    if path.exists():
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Article folders
# ---------------------------------------------------------------------------


def list_article_folders() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM article_folders ORDER BY sort_order, name"
        ).fetchall()
    return [dict(r) for r in rows]


def create_article_folder(name: str, parent_id: str | None = None,
                          icon: str = "", sort_order: int = 0) -> dict[str, Any]:
    fid = f"af_{int(time.time() * 1000)}_{os.urandom(2).hex()}"
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO article_folders (id, name, parent_id, icon, sort_order, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (fid, name, parent_id, icon, sort_order, ts),
        )
    return {"id": fid, "name": name, "parent_id": parent_id,
            "icon": icon, "sort_order": sort_order, "created_at": ts}


def update_article_folder(fid: str, **fields: Any) -> None:
    allowed = {"name", "parent_id", "icon", "sort_order"}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return
    sets = ", ".join(f"{k}=?" for k in updates)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE article_folders SET {sets} WHERE id=?",
            [*updates.values(), fid],
        )


def delete_article_folder(fid: str) -> None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT parent_id FROM article_folders WHERE id=?", (fid,)
        ).fetchone()
        parent_id = row["parent_id"] if row else None
        conn.execute(
            "UPDATE articles SET folder_id=NULL WHERE folder_id=?", (fid,)
        )
        children = conn.execute(
            "SELECT id FROM article_folders WHERE parent_id=?", (fid,)
        ).fetchall()
        for child in children:
            conn.execute(
                "UPDATE article_folders SET parent_id=? WHERE id=?",
                (parent_id, child["id"]),
            )
        conn.execute("DELETE FROM article_folders WHERE id=?", (fid,))


def move_article_to_folder(article_id: str, folder_id: str | None) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE articles SET folder_id=? WHERE id=?",
            (folder_id, article_id),
        )


def move_articles_to_folder(article_ids: list[str], folder_id: str | None) -> None:
    if not article_ids:
        return
    with get_conn() as conn:
        for aid in article_ids:
            conn.execute(
                "UPDATE articles SET folder_id=? WHERE id=?",
                (folder_id, aid),
            )
