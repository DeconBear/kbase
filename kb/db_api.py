import json
import sqlite3
from pathlib import Path

from db_index import ARTICLE_COLUMNS, DB_DIR, DB_PATH, init_db
from utils_yaml import parse_frontmatter, write_frontmatter

import sys
if getattr(sys, 'frozen', False):
    KB_DIR = Path(sys.executable).parent / "data"
else:
    KB_DIR = Path(__file__).resolve().parent

ARTICLE_BOOL_FIELDS = {
    "translated",
    "has_old_translation",
    "summarized",
    "pdf_available",
    "md_available",
    "file_available",
    "converting",
    "metadata_extracted",
}


def get_conn():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    init_db().close()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _json_authors(value):
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return json.dumps(parsed, ensure_ascii=False)
        except json.JSONDecodeError:
            pass
    return "[]"


def _article_db_value(key, value):
    if key == "authors":
        return _json_authors(value)
    if key in ARTICLE_BOOL_FIELDS:
        return 1 if value else 0
    if key == "pages":
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0
    return value if value is not None else ""


def _row_to_article(row, cursor):
    article = dict(row)
    try:
        article["authors"] = json.loads(article["authors"]) if article.get("authors") else []
    except (json.JSONDecodeError, TypeError):
        article["authors"] = []
    for key in ARTICLE_BOOL_FIELDS:
        if key in article:
            article[key] = bool(article[key])
    cursor.execute(
        "SELECT tag FROM tags WHERE item_id=? AND item_type='paper'",
        (article["id"],),
    )
    article["tags"] = [t["tag"] for t in cursor.fetchall()]
    return article


def _write_article_frontmatter_if_present(aid, updates):
    md_file = KB_DIR / "articles" / aid / f"{aid}.md"
    if not md_file.exists():
        return
    meta, content = parse_frontmatter(md_file)
    meta.update(updates)
    meta["type"] = "paper"
    write_frontmatter(md_file, meta, content)


def get_all_articles():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM articles")
    rows = cursor.fetchall()

    articles = []
    categories = set()
    all_tags = set()
    for row in rows:
        article = _row_to_article(row, cursor)
        if article.get("category"):
            categories.add(article["category"])
        all_tags.update(article["tags"])
        articles.append(article)

    conn.close()
    return {
        "articles": articles,
        "categories": sorted(categories),
        "tags": sorted(all_tags),
    }


def update_article(aid, updates):
    updates = updates if isinstance(updates, dict) else {}
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM articles WHERE id=?", (aid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    allowed = [key for key in updates if key in ARTICLE_COLUMNS and key != "id"]
    if allowed:
        assignments = ", ".join(f"{key}=?" for key in allowed)
        values = [_article_db_value(key, updates[key]) for key in allowed]
        values.append(aid)
        cursor.execute(f"UPDATE articles SET {assignments} WHERE id=?", values)

    if "tags" in updates:
        cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
        tags = updates.get("tags") if isinstance(updates.get("tags"), list) else []
        for tag in tags:
            tag = str(tag).strip()
            if tag:
                cursor.execute(
                    "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (aid, tag, "paper"),
                )

    conn.commit()
    conn.close()
    _write_article_frontmatter_if_present(aid, updates)


def delete_article(aid):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM articles WHERE id=?", (aid,))
    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
    cursor.execute("DELETE FROM workspace_items WHERE item_id=?", (aid,))
    conn.commit()
    conn.close()


def add_article(article_data):
    article_data = article_data if isinstance(article_data, dict) else {}
    aid = article_data["id"]
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM articles WHERE id=?", (aid,))
    existing = cursor.fetchone()
    if existing:
        merged = dict(existing)
        merged.update(article_data)
        article_data = merged

    columns = list(ARTICLE_COLUMNS.keys())
    values = [_article_db_value(key, article_data.get(key, "")) for key in columns]
    placeholders = ", ".join("?" for _ in columns)
    update_clause = ", ".join(f"{key}=excluded.{key}" for key in columns if key != "id")
    cursor.execute(
        f"""
        INSERT INTO articles ({", ".join(columns)}) VALUES ({placeholders})
        ON CONFLICT(id) DO UPDATE SET {update_clause}
        """,
        values,
    )

    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
    for tag in article_data.get("tags", []):
        tag = str(tag).strip()
        if tag:
            cursor.execute(
                "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                (aid, tag, "paper"),
            )
    conn.commit()
    conn.close()
    _write_article_frontmatter_if_present(aid, {**article_data, "type": "paper"})


def get_all_notes():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes")
    rows = cursor.fetchall()

    notes = []
    for row in rows:
        note = dict(row)
        cursor.execute(
            "SELECT tag FROM tags WHERE item_id=? AND item_type='note'",
            (note["id"],),
        )
        note["tags"] = [t["tag"] for t in cursor.fetchall()]
        notes.append(note)

    conn.close()
    return {"notes": notes}


def update_note(nid, updates):
    updates = updates if isinstance(updates, dict) else {}
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT * FROM notes WHERE id=?", (nid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return

    note = dict(row)
    note.update(updates)
    cursor.execute(
        "UPDATE notes SET title=?, created_at=?, modified_at=?, folder=? WHERE id=?",
        (
            note.get("title"),
            note.get("created_at"),
            note.get("modified_at"),
            note.get("folder"),
            nid,
        ),
    )

    if "tags" in updates:
        cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
        tags = updates.get("tags") if isinstance(updates.get("tags"), list) else []
        for tag in tags:
            tag = str(tag).strip()
            if tag:
                cursor.execute(
                    "INSERT INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                    (nid, tag, "note"),
                )

    conn.commit()
    conn.close()

    md_file = KB_DIR / "notes" / f"{nid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
        meta.update(updates)
        write_frontmatter(md_file, meta, content)


def add_note(note_data):
    nid = note_data["id"]
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO notes (id, title, created_at, modified_at, folder)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            nid,
            note_data.get("title", ""),
            note_data.get("created_at", ""),
            note_data.get("modified_at", ""),
            note_data.get("folder", ""),
        ),
    )
    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
    for tag in note_data.get("tags", []):
        tag = str(tag).strip()
        if tag:
            cursor.execute(
                "INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)",
                (nid, tag, "note"),
            )
    conn.commit()
    conn.close()

    md_file = KB_DIR / "notes" / f"{nid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
    else:
        meta, content = {}, ""
    meta.update(note_data)
    meta["type"] = "note"
    write_frontmatter(md_file, meta, content)


def delete_note(nid):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE id=?", (nid,))
    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
    cursor.execute("DELETE FROM workspace_items WHERE item_id=?", (nid,))
    conn.commit()
    conn.close()


def get_all_workspaces():
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workspaces ORDER BY created_at DESC")
    rows = cursor.fetchall()

    workspaces = []
    for row in rows:
        workspace = dict(row)
        cursor.execute(
            "SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?",
            (workspace["id"],),
        )
        workspace["items"] = [dict(r) for r in cursor.fetchall()]
        workspaces.append(workspace)
    conn.close()
    return workspaces


def add_workspace(workspace_id, name):
    import time

    conn = get_conn()
    cursor = conn.cursor()
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute(
        "INSERT OR REPLACE INTO workspaces (id, name, created_at) VALUES (?, ?, ?)",
        (workspace_id, name, created_at),
    )
    conn.commit()
    conn.close()
    return {"id": workspace_id, "name": name, "created_at": created_at, "items": []}


def delete_workspace(workspace_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM workspace_items WHERE workspace_id=?", (workspace_id,))
    cursor.execute("DELETE FROM workspaces WHERE id=?", (workspace_id,))
    conn.commit()
    conn.close()


def add_item_to_workspace(workspace_id, item_id, item_type):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO workspace_items (workspace_id, item_id, item_type) VALUES (?, ?, ?)",
        (workspace_id, item_id, item_type),
    )
    conn.commit()
    conn.close()


def remove_item_from_workspace(workspace_id, item_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM workspace_items WHERE workspace_id=? AND item_id=?",
        (workspace_id, item_id),
    )
    conn.commit()
    conn.close()


def get_workspace_items(workspace_id):
    if not workspace_id:
        return []
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?",
        (workspace_id,),
    )
    items = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return items
