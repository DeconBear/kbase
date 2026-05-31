import json
from pathlib import Path
from utils_yaml import parse_frontmatter, write_frontmatter

KB_DIR = Path(__file__).resolve().parent

def migrate_articles():
    idx_path = KB_DIR / "kb-index.json"
    if not idx_path.exists():
        print("No kb-index.json found")
        return
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    
    for a in idx.get("articles", []):
        aid = a["id"]
        md_file = KB_DIR / "articles" / aid / f"{aid}.md"
        
        # We also have _meta.json in some articles, we can merge
        meta_file = KB_DIR / "articles" / aid / f"{aid}_meta.json"
        if meta_file.exists():
            try:
                local_meta = json.loads(meta_file.read_text(encoding="utf-8"))
                for k, v in local_meta.items():
                    if k not in a or not a[k]:
                        a[k] = v
            except:
                pass
                
        # Build YAML dict
        meta = {
            "type": "paper",
            "id": aid,
            "title": a.get("title", ""),
            "author": a.get("author", ""),
            "authors": a.get("authors", []),
            "date_added": a.get("date_added", ""),
            "category": a.get("category", ""),
            "tags": a.get("tags", []),
            "doi": a.get("doi", ""),
            "year": a.get("year", ""),
            "venue": a.get("venue", ""),
            "abstract": a.get("abstract", "")
        }
        
        # Preserve boolean flags
        for flag in ["translated", "summarized", "pdf_available", "md_available"]:
            if a.get(flag):
                meta[flag] = True

        md_content = ""
        if md_file.exists():
            _, md_content = parse_frontmatter(md_file)
        else:
            # Create an empty file with frontmatter if it has PDF but no Markdown yet
            # It's better to ensure every article has an entry point
            pass

        write_frontmatter(md_file, meta, md_content)
        print(f"Migrated article: {aid}")

def migrate_notes():
    idx_path = KB_DIR / "notes_index.json"
    if not idx_path.exists():
        print("No notes_index.json found")
        return
    idx = json.loads(idx_path.read_text(encoding="utf-8"))
    
    for n in idx.get("notes", []):
        nid = n["id"]
        # n has: id, title, created_at, updated_at
        md_file = KB_DIR / "notes" / f"{nid}.md"
        
        meta = {
            "type": "note",
            "id": nid,
            "title": n.get("title", ""),
            "created_at": n.get("created_at", ""),
            "modified_at": n.get("modified_at", ""),
            "tags": n.get("tags", []),
            "folder": n.get("folder", ""),
            "links": n.get("links", [])
        }
        
        md_content = ""
        if md_file.exists():
            _, md_content = parse_frontmatter(md_file)
            write_frontmatter(md_file, meta, md_content)
            print(f"Migrated note: {nid}")
        else:
            print(f"Note file not found: {nid}")

if __name__ == "__main__":
    print("Migrating articles...")
    migrate_articles()
    print("Migrating notes...")
    migrate_notes()
    print("Migration complete.")
