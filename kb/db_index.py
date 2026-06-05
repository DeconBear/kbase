import json
import re
import sqlite3
from pathlib import Path

from utils_yaml import parse_frontmatter

KB_DIR = Path(__file__).resolve().parent
DB_DIR = KB_DIR / ".kbase"
DB_PATH = DB_DIR / "index.db"

ARTICLE_COLUMNS = {
    "id": "TEXT PRIMARY KEY",
    "title": "TEXT",
    "author": "TEXT",
    "authors": "TEXT",
    "pages": "INTEGER DEFAULT 0",
    "date_added": "TEXT",
    "category": "TEXT",
    "doi": "TEXT",
    "year": "TEXT",
    "venue": "TEXT",
    "abstract": "TEXT",
    "translated": "INTEGER DEFAULT 0",
    "has_old_translation": "INTEGER DEFAULT 0",
    "summarized": "INTEGER DEFAULT 0",
    "pdf_available": "INTEGER DEFAULT 0",
    "md_available": "INTEGER DEFAULT 0",
    "file_available": "INTEGER DEFAULT 0",
    "converting": "INTEGER DEFAULT 0",
    "source_filename": "TEXT",
    "kind": "TEXT",
    "metadata_extracted": "INTEGER DEFAULT 0",
    "metadata_extracted_at": "TEXT",
    "metadata_source": "TEXT",
    "parser": "TEXT",
    "preparse_error": "TEXT",
}


def _ensure_columns(cursor, table, columns):
    existing = {
        row[1]
        for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()
    }
    for name, column_type in columns.items():
        if name not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {column_type}")


def init_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()

    column_sql = ",\n        ".join(
        f"{name} {column_type}" for name, column_type in ARTICLE_COLUMNS.items()
    )
    cursor.execute(f"""
    CREATE TABLE IF NOT EXISTS articles (
        {column_sql}
    )
    """)
    _ensure_columns(cursor, "articles", ARTICLE_COLUMNS)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT,
        modified_at TEXT,
        folder TEXT
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS tags (
        item_id TEXT,
        tag TEXT,
        item_type TEXT,
        UNIQUE(item_id, tag, item_type)
    )
    """)

    cursor.executescript("""
        CREATE TABLE IF NOT EXISTS links (
            source_id TEXT,
            target_id TEXT,
            source_type TEXT,
            target_type TEXT,
            PRIMARY KEY (source_id, target_id)
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
    """)
    conn.commit()
    return conn


def _article_from_meta(article_id, meta, md_file):
    article_dir = md_file.parent
    pdf_available = (article_dir / "original.pdf").exists()
    original_files = [
        p for p in article_dir.iterdir()
        if p.is_file() and p.stem == "original" and p.suffix.lower() != ".md"
    ]
    return {
        "id": meta.get("id", article_id),
        "title": meta.get("title", article_id),
        "author": meta.get("author", ""),
        "authors": meta.get("authors", []),
        "pages": int(meta.get("pages") or 0),
        "date_added": meta.get("date_added", ""),
        "category": meta.get("category", ""),
        "doi": meta.get("doi", ""),
        "year": meta.get("year", ""),
        "venue": meta.get("venue", ""),
        "abstract": meta.get("abstract", ""),
        "translated": bool(meta.get("translated")),
        "has_old_translation": (article_dir / f"{article_id}_translated_old.md").exists(),
        "summarized": bool(meta.get("summarized")),
        "pdf_available": pdf_available,
        "md_available": md_file.exists(),
        "file_available": bool(original_files),
        "converting": False,
        "source_filename": meta.get("source_filename") or (original_files[0].name if original_files else ""),
        "kind": meta.get("kind") or meta.get("document_kind") or ("paper" if pdf_available else "file"),
        "metadata_extracted": bool(meta.get("metadata_extracted")),
        "metadata_extracted_at": meta.get("metadata_extracted_at", ""),
        "metadata_source": meta.get("metadata_source", ""),
        "parser": meta.get("parser", ""),
        "preparse_error": meta.get("preparse_error", ""),
        "tags": meta.get("tags", []),
    }


def _insert_article(cursor, article):
    columns = list(ARTICLE_COLUMNS.keys())
    values = []
    for key in columns:
        value = article.get(key, "")
        if key == "authors":
            value = json.dumps(value if isinstance(value, list) else [])
        elif key in {
            "translated", "has_old_translation", "summarized", "pdf_available",
            "md_available", "file_available", "converting", "metadata_extracted",
        }:
            value = 1 if value else 0
        values.append(value)
    placeholders = ", ".join("?" for _ in columns)
    cursor.execute(
        f"INSERT OR REPLACE INTO articles ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def scan_and_rebuild():
    conn = init_db()
    cursor = conn.cursor()

    cursor.execute("DELETE FROM articles")
    cursor.execute("DELETE FROM notes")
    cursor.execute("DELETE FROM tags")
    cursor.execute("DELETE FROM links")

    articles_dir = KB_DIR / "articles"
    if articles_dir.exists():
        for adir in articles_dir.iterdir():
            if not adir.is_dir():
                continue
            md_file = adir / f"{adir.name}.md"
            if not md_file.exists():
                continue
            meta, _ = parse_frontmatter(md_file)
            article = _article_from_meta(adir.name, meta, md_file)
            _insert_article(cursor, article)
            for tag in article.get("tags", []):
                cursor.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (article["id"], tag, "paper"),
                )

    notes_dir = KB_DIR / "notes"
    if notes_dir.exists():
        for md_file in notes_dir.rglob("*.md"):
            meta, content = parse_frontmatter(md_file)
            if meta.get("type") != "note":
                continue
            nid = meta.get("id", md_file.stem)
            cursor.execute("""
                INSERT INTO notes (id, title, created_at, modified_at, folder)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    nid,
                    meta.get("title", md_file.stem),
                    meta.get("created_at", ""),
                    meta.get("modified_at", ""),
                    meta.get("folder", ""),
                ),
            )
            for tag in meta.get("tags", []):
                cursor.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (nid, tag, "note"),
                )

            links = re.findall(r"\[\[(.*?)\]\]", content)
            for link in links:
                link = link.split("|")[0]
                cursor.execute(
                    "INSERT OR IGNORE INTO links (source_id, target_id, source_type) VALUES (?, ?, ?)",
                    (nid, link, "note"),
                )
            for link in meta.get("links", []):
                cursor.execute(
                    "INSERT OR IGNORE INTO links (source_id, target_id, source_type) VALUES (?, ?, ?)",
                    (nid, link, "note"),
                )

    conn.commit()
    conn.close()
    print("Database rebuilt successfully.")


if __name__ == "__main__":
    scan_and_rebuild()
