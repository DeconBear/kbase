import sqlite3
import json
from pathlib import Path
from utils_yaml import parse_frontmatter, write_frontmatter

KB_DIR = Path(__file__).resolve().parent
DB_DIR = KB_DIR / ".kbase"
DB_PATH = DB_DIR / "index.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def get_all_articles():
    if not DB_PATH.exists():
        return {"articles": []}
    
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM articles")
    rows = cursor.fetchall()
    
    articles = []
    categories = set()
    all_tags = set()
    for row in rows:
        a = dict(row)
        try:
            a['authors'] = json.loads(a['authors']) if a['authors'] else []
        except (json.JSONDecodeError, TypeError):
            a['authors'] = []
        a['translated'] = bool(a['translated'])
        a['summarized'] = bool(a['summarized'])
        a['pdf_available'] = bool(a['pdf_available'])
        a['md_available'] = bool(a['md_available'])
        
        cursor.execute("SELECT tag FROM tags WHERE item_id=? AND item_type='paper'", (a['id'],))
        a['tags'] = [t['tag'] for t in cursor.fetchall()]
        
        if a.get('category'):
            categories.add(a['category'])
        all_tags.update(a['tags'])
            
        articles.append(a)
        
    conn.close()
    return {"articles": articles, "categories": sorted(list(categories)), "tags": sorted(list(all_tags))}

def update_article(aid, updates):
    # 1. Update SQLite
    conn = get_conn()
    cursor = conn.cursor()
    
    # Get current
    cursor.execute("SELECT * FROM articles WHERE id=?", (aid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    a = dict(row)
    a.update(updates)
    
    cursor.execute('''
    UPDATE articles SET 
        title=?, author=?, authors=?, date_added=?, category=?, 
        doi=?, year=?, venue=?, abstract=?, 
        translated=?, summarized=?, pdf_available=?, md_available=?
    WHERE id=?
    ''', (
        a.get('title'), a.get('author'), 
        json.dumps(a.get('authors', [])) if 'authors' in a else a.get('authors'),
        a.get('date_added'), a.get('category'), a.get('doi'), a.get('year'),
        a.get('venue'), a.get('abstract'),
        1 if a.get('translated') else 0,
        1 if a.get('summarized') else 0,
        1 if a.get('pdf_available') else 0,
        1 if a.get('md_available') else 0,
        aid
    ))
    
    if 'tags' in updates:
        cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
        for tag in updates['tags']:
            cursor.execute("INSERT INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)", (aid, tag, 'paper'))
            
    conn.commit()
    conn.close()
    
    # 2. Update Frontmatter
    md_file = KB_DIR / "articles" / aid / f"{aid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
    else:
        meta, content = {"id": aid, "type": "paper"}, ""
        
    meta.update(updates)
    write_frontmatter(md_file, meta, content)

def delete_article(aid):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM articles WHERE id=?", (aid,))
    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='paper'", (aid,))
    conn.commit()
    conn.close()

def add_article(article_data):
    aid = article_data['id']
    conn = get_conn()
    cursor = conn.cursor()

    cursor.execute("SELECT id FROM articles WHERE id=?", (aid,))
    exists = cursor.fetchone() is not None

    if exists:
        defaults = dict(cursor.execute("SELECT * FROM articles WHERE id=?", (aid,)).fetchone())
        defaults.update(article_data)
        article_data = defaults
        cursor.execute('''
        UPDATE articles SET
            title=?, author=?, authors=?, date_added=?, category=?,
            doi=?, year=?, venue=?, abstract=?,
            translated=?, summarized=?, pdf_available=?, md_available=?
        WHERE id=?
        ''', (
            article_data.get("title", ""),
            article_data.get("author", ""),
            json.dumps(article_data.get("authors", [])),
            article_data.get("date_added", ""),
            article_data.get("category", ""),
            article_data.get("doi", ""),
            article_data.get("year", ""),
            article_data.get("venue", ""),
            article_data.get("abstract", ""),
            1 if article_data.get("translated") else 0,
            1 if article_data.get("summarized") else 0,
            1 if article_data.get("pdf_available") else 0,
            1 if article_data.get("md_available") else 0,
            aid
        ))
    else:
        cursor.execute('''
        INSERT INTO articles (id, title, author, authors, date_added, category, doi, year, venue, abstract, translated, summarized, pdf_available, md_available)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            aid,
            article_data.get("title", ""),
            article_data.get("author", ""),
            json.dumps(article_data.get("authors", [])),
            article_data.get("date_added", ""),
            article_data.get("category", ""),
            article_data.get("doi", ""),
            article_data.get("year", ""),
            article_data.get("venue", ""),
            article_data.get("abstract", ""),
            1 if article_data.get("translated") else 0,
            1 if article_data.get("summarized") else 0,
            1 if article_data.get("pdf_available") else 0,
            1 if article_data.get("md_available") else 0
        ))
    for tag in article_data.get("tags", []):
        cursor.execute('INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)', (aid, tag, "paper"))
    conn.commit()
    conn.close()
    
    # Update frontmatter
    md_file = KB_DIR / "articles" / aid / f"{aid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
    else:
        meta, content = {}, ""
    meta.update(article_data)
    meta['type'] = 'paper'
    write_frontmatter(md_file, meta, content)


def get_all_notes():
    if not DB_PATH.exists():
        return {"notes": []}
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM notes")
    rows = cursor.fetchall()
    
    notes = []
    for row in rows:
        n = dict(row)
        cursor.execute("SELECT tag FROM tags WHERE item_id=? AND item_type='note'", (n['id'],))
        n['tags'] = [t['tag'] for t in cursor.fetchall()]
        notes.append(n)
        
    conn.close()
    return {"notes": notes}

def update_note(nid, updates):
    conn = get_conn()
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM notes WHERE id=?", (nid,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return
        
    n = dict(row)
    n.update(updates)
    
    cursor.execute('''
    UPDATE notes SET title=?, created_at=?, modified_at=?, folder=? WHERE id=?
    ''', (n.get('title'), n.get('created_at'), n.get('modified_at'), n.get('folder'), nid))
    
    if 'tags' in updates:
        cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
        for tag in updates['tags']:
            cursor.execute("INSERT INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)", (nid, tag, 'note'))
            
    conn.commit()
    conn.close()
    
    # Frontmatter
    md_file = KB_DIR / "notes" / f"{nid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
        meta.update(updates)
        write_frontmatter(md_file, meta, content)

def add_note(note_data):
    nid = note_data['id']
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute('''
    INSERT OR REPLACE INTO notes (id, title, created_at, modified_at, folder)
    VALUES (?, ?, ?, ?, ?)
    ''', (
        nid,
        note_data.get("title", ""),
        note_data.get("created_at", ""),
        note_data.get("modified_at", ""),
        note_data.get("folder", "")
    ))
    for tag in note_data.get("tags", []):
        cursor.execute('INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)', (nid, tag, "note"))
    conn.commit()
    conn.close()
    
    # Frontmatter
    md_file = KB_DIR / "notes" / f"{nid}.md"
    if md_file.exists():
        meta, content = parse_frontmatter(md_file)
    else:
        meta, content = {}, ""
    meta.update(note_data)
    meta['type'] = 'note'
    write_frontmatter(md_file, meta, content)

def delete_note(nid):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM notes WHERE id=?", (nid,))
    cursor.execute("DELETE FROM tags WHERE item_id=? AND item_type='note'", (nid,))
    conn.commit()
    conn.close()

def get_all_workspaces():
    if not DB_PATH.exists():
        return []
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM workspaces ORDER BY created_at DESC")
    rows = cursor.fetchall()
    
    workspaces = []
    for row in rows:
        ws = dict(row)
        cursor.execute("SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?", (ws['id'],))
        items = [dict(r) for r in cursor.fetchall()]
        ws['items'] = items
        workspaces.append(ws)
    conn.close()
    return workspaces

def add_workspace(workspace_id, name):
    import time
    conn = get_conn()
    cursor = conn.cursor()
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("INSERT OR REPLACE INTO workspaces (id, name, created_at) VALUES (?, ?, ?)", 
                   (workspace_id, name, created_at))
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
    cursor.execute("INSERT OR IGNORE INTO workspace_items (workspace_id, item_id, item_type) VALUES (?, ?, ?)",
                   (workspace_id, item_id, item_type))
    conn.commit()
    conn.close()

def remove_item_from_workspace(workspace_id, item_id):
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM workspace_items WHERE workspace_id=? AND item_id=?", (workspace_id, item_id))
    conn.commit()
    conn.close()

def get_workspace_items(workspace_id):
    if not workspace_id:
        return []
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute("SELECT item_id, item_type FROM workspace_items WHERE workspace_id=?", (workspace_id,))
    items = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return items
