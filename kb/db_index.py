import sqlite3
import json
import re
from pathlib import Path
from utils_yaml import parse_frontmatter

KB_DIR = Path(__file__).resolve().parent
DB_DIR = KB_DIR / ".kbase"
DB_PATH = DB_DIR / "index.db"

def init_db():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    cursor = conn.cursor()
    
    # Create tables
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS articles (
        id TEXT PRIMARY KEY,
        title TEXT,
        author TEXT,
        authors TEXT,
        date_added TEXT,
        category TEXT,
        doi TEXT,
        year TEXT,
        venue TEXT,
        abstract TEXT,
        translated INTEGER,
        summarized INTEGER,
        pdf_available INTEGER,
        md_available INTEGER
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS notes (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT,
        modified_at TEXT,
        folder TEXT
    )
    ''')
    
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS tags (
        item_id TEXT,
        tag TEXT,
        item_type TEXT,
        UNIQUE(item_id, tag, item_type)
    )
    ''')
    
    cursor.executescript('''
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
    ''')
    conn.commit()
    return conn

def scan_and_rebuild():
    conn = init_db()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM articles')
    cursor.execute('DELETE FROM notes')
    cursor.execute('DELETE FROM tags')
    cursor.execute('DELETE FROM links')
    
    # Scan articles
    articles_dir = KB_DIR / "articles"
    if articles_dir.exists():
        for adir in articles_dir.iterdir():
            if adir.is_dir():
                md_file = adir / f"{adir.name}.md"
                if md_file.exists():
                    meta, _ = parse_frontmatter(md_file)
                    if meta.get("type") == "paper":
                        cursor.execute('''
                        INSERT INTO articles (id, title, author, authors, date_added, category, doi, year, venue, abstract, translated, summarized, pdf_available, md_available)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ''', (
                            meta.get("id", adir.name),
                            meta.get("title", ""),
                            meta.get("author", ""),
                            json.dumps(meta.get("authors", [])),
                            meta.get("date_added", ""),
                            meta.get("category", ""),
                            meta.get("doi", ""),
                            meta.get("year", ""),
                            meta.get("venue", ""),
                            meta.get("abstract", ""),
                            1 if meta.get("translated") else 0,
                            1 if meta.get("summarized") else 0,
                            1 if meta.get("pdf_available") else 0,
                            1 if meta.get("md_available") else 0
                        ))
                        for tag in meta.get("tags", []):
                            cursor.execute('INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)', (adir.name, tag, "paper"))

    # Scan notes
    notes_dir = KB_DIR / "notes"
    if notes_dir.exists():
        for md_file in notes_dir.rglob("*.md"):
            meta, content = parse_frontmatter(md_file)
            if meta.get("type") == "note":
                nid = meta.get("id", md_file.stem)
                cursor.execute('''
                INSERT INTO notes (id, title, created_at, modified_at, folder)
                VALUES (?, ?, ?, ?, ?)
                ''', (
                    nid,
                    meta.get("title", md_file.stem),
                    meta.get("created_at", ""),
                    meta.get("modified_at", ""),
                    meta.get("folder", "")
                ))
                for tag in meta.get("tags", []):
                    cursor.execute('INSERT OR IGNORE INTO tags (item_id, tag, item_type) VALUES (?, ?, ?)', (nid, tag, "note"))
                
                # Simple link extraction for Phase 1
                links = re.findall(r'\[\[(.*?)\]\]', content)
                for link in links:
                    # Remove alias part e.g. [[Note|Alias]] -> Note
                    link = link.split('|')[0]
                    cursor.execute('INSERT OR IGNORE INTO links (source_id, target_id, source_type) VALUES (?, ?, ?)', (nid, link, "note"))
                for link in meta.get("links", []):
                    cursor.execute('INSERT OR IGNORE INTO links (source_id, target_id, source_type) VALUES (?, ?, ?)', (nid, link, "note"))
                    
    conn.commit()
    conn.close()
    print("Database rebuilt successfully.")

if __name__ == "__main__":
    scan_and_rebuild()
