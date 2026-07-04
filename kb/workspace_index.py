"""Workspace SQLite FTS index (rebuildable from sidecars + files)."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from workspace import Workspace
from workspace_search import _readable_paths

_FTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    kind TEXT,
    path TEXT,
    title TEXT,
    mtime REAL
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    doc_id UNINDEXED,
    path UNINDEXED,
    title,
    body,
    tokenize='unicode61'
);
"""


def index_db_path(ws: Workspace) -> Path:
    return ws.kbase / "index.db"


def rebuild_index(ws: Workspace) -> dict[str, Any]:
    db_path = index_db_path(ws)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript("DROP TABLE IF EXISTS fts; DROP TABLE IF EXISTS documents;")
        conn.executescript(_FTS_SCHEMA)
        indexed = 0
        skipped = 0
        for doc in ws.list_documents():
            if doc.get("status") == "missing":
                skipped += 1
                continue
            doc_id = str(doc.get("id") or "")
            title = str(doc.get("title") or "")
            kind = str(doc.get("kind") or "")
            for rel in _readable_paths(ws, doc):
                abs_path = ws.root / rel
                if not abs_path.is_file():
                    continue
                try:
                    body = abs_path.read_text(encoding="utf-8", errors="replace")
                    mtime = abs_path.stat().st_mtime
                except OSError:
                    skipped += 1
                    continue
                conn.execute(
                    "INSERT INTO documents (doc_id, kind, path, title, mtime) VALUES (?, ?, ?, ?, ?)",
                    (doc_id, kind, rel, title, mtime),
                )
                conn.execute(
                    "INSERT INTO fts (doc_id, path, title, body) VALUES (?, ?, ?, ?)",
                    (doc_id, rel, title, body[:500_000]),
                )
                indexed += 1
        conn.commit()
    finally:
        conn.close()
    return {"indexed": indexed, "skipped": skipped, "db": str(db_path)}


def search_fts(ws: Workspace, query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    db_path = index_db_path(ws)
    if not db_path.exists() or not (query or "").strip():
        return []
    q = query.strip().replace('"', '""')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT f.doc_id, f.path, f.title, snippet(fts, 3, '[', ']', '…', 20) AS snippet
            FROM fts f
            WHERE fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (q, limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()
    return [
        {
            "doc_id": row["doc_id"],
            "path": row["path"],
            "title": row["title"],
            "snippet": row["snippet"],
            "source": "fts",
        }
        for row in rows
    ]
