"""Unified data storage layer.

Resolves the runtime data root directory and provides a single SQLite-backed
store for articles, notes, tags, workspaces, conversion history, translations
and chat sessions. The legacy kb-index.json / notes_index.json /
library_chat_sessions.json files are no longer read or written by the app.

Data root resolution (no environment variable overrides):
- Packaged build (PyInstaller): ``<exe dir>/data``
- Source run: ``<repo root>/data`` (parent of the ``kb`` package)

The data root is created on first access. A minimal ``local.env`` is generated
on first launch with empty values for the eight known keys.
"""
from __future__ import annotations

import json
import os
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

DATA_ROOT: Path = _REPO_ROOT / "data"
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
LLM_MODEL=deepseek-chat

# Alibaba Cloud DocMind (RAM access with AK/SK)
DOCMIND_ACCESS_KEY_ID=
DOCMIND_ACCESS_KEY_SECRET=
DOCMIND_REGION=cn-hangzhou

# DeconBear DocParser (cloud GPU-accelerated PDF parsing)
DOCPARSER_API_URL=https://your-cloud-parser.com
DOCPARSER_API_KEY=
DOCPARSER_ENGINE=struct
"""


def ensure_directories() -> None:
    """Create the full data layout and generate local.env on first run."""
    for path in (
        DATA_ROOT,
        ARTICLES_DIR,
        NOTES_DIR,
        KBASE_DIR,
        LOGS_DIR,
        CHAT_SESSIONS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)
    if not LOCAL_ENV.exists():
        LOCAL_ENV.write_text(LOCAL_ENV_TEMPLATE, encoding="utf-8")


def load_local_env() -> None:
    """Load data/local.env into os.environ without overriding existing vars."""
    if not LOCAL_ENV.exists():
        return
    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


def _write_local_env(updates: dict[str, str]) -> None:
    """Merge ``updates`` into local.env, preserving comments and ordering.

    Lines whose key appears in ``updates`` are rewritten in place; any
    unknown key is appended at the bottom of the file.
    """
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
    LOCAL_ENV.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
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
    preparse_error TEXT
);

CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    title TEXT,
    created_at TEXT,
    modified_at TEXT,
    folder TEXT
);

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
-- matches the SiYuan-style navigation we are replicating).
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

CREATE INDEX IF NOT EXISTS idx_articles_date_added ON articles(date_added);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_translation_article ON translation_state(article_id);
CREATE INDEX IF NOT EXISTS idx_conv_history_article ON conversion_history(article_id);
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
_CONNECTIONS: set[int] = set()


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
            ):
                if col not in existing_cols:
                    conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {decl}")
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


def _article_to_values(article: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    authors = article.get("authors")
    if isinstance(authors, list):
        out["authors_json"] = json.dumps(authors, ensure_ascii=False)
    else:
        out["authors_json"] = "[]"
    for k, v in article.items():
        if k == "authors":
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
                                 notebook_id, parent_id, sort_order, doc_icon)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 title=excluded.title,
                 created_at=excluded.created_at,
                 modified_at=excluded.modified_at,
                 folder=excluded.folder,
                 notebook_id=excluded.notebook_id,
                 parent_id=excluded.parent_id,
                 sort_order=excluded.sort_order,
                 doc_icon=excluded.doc_icon""",
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
    """Parse `[[X]]` and `[[X#Y]]` patterns in content, resolve X
    against the notes table (by id or title) and Y against the
    block anchors of the target note. Stores resolved pairs in
    `note_links`. The table is wiped for this note first so deleted
    references don't linger.
    """
    pattern = re.compile(r"\[\[([^\]]+)\]\]")
    out: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    with get_conn() as conn:
        for match in pattern.finditer(content or ""):
            target = match.group(1).strip()
            if not target:
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
