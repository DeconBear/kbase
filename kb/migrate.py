"""One-shot data migration: legacy kb/ layout → unified data/ layout.

Imports from:
  - kb/kb-index.json                 → data/.kbase/index.db (articles + tags)
  - kb/notes_index.json              → data/.kbase/index.db (notes + tags)
  - kb/library_chat_sessions.json    → data/.kbase/chat_sessions/ + index
  - kb/articles/                     → data/articles/  (copied, then removed)
  - kb/notes/                        → data/notes/     (copied, then removed)

The legacy JSON files are renamed to ``*.legacy.json`` so the user keeps a
backup. This script is idempotent: re-running it on an already-migrated
data root is a no-op.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import storage
from storage import (
    CHAT_SESSIONS_DIR,
    DATA_ROOT,
    DB_PATH,
    KBASE_DIR,
    delete_article,
    list_chat_sessions,
    save_chat_index,
    save_chat_session_file,
    upsert_article,
    upsert_note,
)

LEGACY_KB_DIR = Path(__file__).resolve().parent
LEGACY_KB_INDEX = LEGACY_KB_DIR / "kb-index.json"
LEGACY_NOTES_INDEX = LEGACY_KB_DIR / "notes_index.json"
LEGACY_SESSIONS = LEGACY_KB_DIR / "library_chat_sessions.json"
LEGACY_ARTICLES_DIR = LEGACY_KB_DIR / "articles"
LEGACY_NOTES_DIR = LEGACY_KB_DIR / "notes"
BACKUP_SUFFIX = ".legacy.json"


def _migrate_article_files() -> int:
    """Copy kb/articles/ → data/articles/ (deep), then remove the source dir."""
    if not LEGACY_ARTICLES_DIR.exists():
        return 0
    target = DATA_ROOT / "articles"
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in LEGACY_ARTICLES_DIR.iterdir():
        if not src.is_dir():
            continue
        dest = target / src.name
        if dest.exists():
            continue
        shutil.copytree(src, dest)
        count += 1
    # Remove legacy tree after successful copy
    try:
        shutil.rmtree(LEGACY_ARTICLES_DIR)
        print(f"  Removed legacy {LEGACY_ARTICLES_DIR.name}/")
    except OSError as exc:
        print(f"  ! could not remove {LEGACY_ARTICLES_DIR}: {exc}")
    return count


def _migrate_note_files() -> int:
    if not LEGACY_NOTES_DIR.exists():
        return 0
    target = DATA_ROOT / "notes"
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for src in LEGACY_NOTES_DIR.iterdir():
        if not src.is_file():
            continue
        dest = target / src.name
        if dest.exists():
            continue
        shutil.copy2(src, dest)
        count += 1
    try:
        shutil.rmtree(LEGACY_NOTES_DIR)
        print(f"  Removed legacy {LEGACY_NOTES_DIR.name}/")
    except OSError as exc:
        print(f"  ! could not remove {LEGACY_NOTES_DIR}: {exc}")
    return count


def _migrate_index() -> int:
    if not LEGACY_KB_INDEX.exists():
        return 0
    try:
        data = json.loads(LEGACY_KB_INDEX.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"⚠️  Could not parse {LEGACY_KB_INDEX}: {exc}")
        return 0
    articles = data.get("articles") or []
    n = 0
    for art in articles:
        if not isinstance(art, dict) or not art.get("id"):
            continue
        # Split into clean payload; tags handled separately.
        tags = art.get("tags") or []
        art_payload = {k: v for k, v in art.items() if k != "tags"}
        # Defaults: a few fields may be missing in old records.
        art_payload.setdefault("title", art_payload.get("id"))
        art_payload.setdefault("date_added", time.strftime("%Y-%m-%d %H:%M"))
        art_payload.setdefault("kind", "paper" if art_payload.get("pdf_available") else "file")
        try:
            upsert_article(art_payload)
        except Exception as exc:
            print(f"  ! article {art.get('id')}: {exc}")
            continue
        from storage import replace_article_tags
        if tags:
            try:
                replace_article_tags(art["id"], tags)
            except Exception:
                pass
        n += 1
    _rename_legacy(LEGACY_KB_INDEX)
    return n


def _migrate_notes() -> int:
    if not LEGACY_NOTES_INDEX.exists():
        return 0
    try:
        data = json.loads(LEGACY_NOTES_INDEX.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"⚠️  Could not parse {LEGACY_NOTES_INDEX}: {exc}")
        return 0
    notes = data.get("notes") or []
    n = 0
    for note in notes:
        if not isinstance(note, dict) or not note.get("id"):
            continue
        try:
            upsert_note({
                "id": note["id"],
                "title": note.get("title", ""),
                "created_at": note.get("created_at", ""),
                "modified_at": note.get("modified_at", ""),
                "folder": note.get("folder", ""),
                "tags": note.get("tags") or [],
            })
            n += 1
        except Exception as exc:
            print(f"  ! note {note.get('id')}: {exc}")
    _rename_legacy(LEGACY_NOTES_INDEX)
    return n


def _migrate_sessions() -> int:
    if not LEGACY_SESSIONS.exists():
        return 0
    try:
        data = json.loads(LEGACY_SESSIONS.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"⚠️  Could not parse {LEGACY_SESSIONS}: {exc}")
        return 0
    sessions = data.get("sessions") or []
    n = 0
    CHAT_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    for session in sessions:
        if not isinstance(session, dict) or not session.get("id"):
            continue
        sid = session["id"]
        save_chat_session_file(sid, {
            "id": sid,
            "title": session.get("title", "新会话"),
            "created_at": session.get("created_at", ""),
            "updated_at": session.get("updated_at", ""),
            "messages": session.get("messages") or [],
            "memory_summary": session.get("memory_summary", ""),
            "compacted_count": int(session.get("compacted_count") or 0),
        })
        n += 1
    if sessions:
        meta_list = [
            {
                "id": s.get("id"),
                "title": s.get("title", ""),
                "created_at": s.get("created_at", ""),
                "updated_at": s.get("updated_at", ""),
                "compacted_count": int(s.get("compacted_count") or 0),
            }
            for s in sessions if s.get("id")
        ]
        meta_list.sort(key=lambda m: m.get("updated_at", ""), reverse=True)
        save_chat_index({
            "active_session_id": data.get("active_session_id", meta_list[0]["id"] if meta_list else ""),
            "sessions": meta_list,
        })
    _rename_legacy(LEGACY_SESSIONS)
    return n


def _rename_legacy(path: Path) -> None:
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        backup.unlink()
    try:
        path.rename(backup)
        print(f"  Renamed {path.name} → {backup.name}")
    except OSError as exc:
        print(f"  ! could not rename {path}: {exc}")


def main() -> int:
    print(f"Data root:   {DATA_ROOT}")
    print(f"Database:    {DB_PATH}")
    print(f"Legacy dir:  {LEGACY_KB_DIR}")
    if not LEGACY_KB_INDEX.exists() and not LEGACY_NOTES_INDEX.exists() and not LEGACY_SESSIONS.exists():
        print("No legacy JSON files detected. Nothing to migrate.")
        return 0
    af = _migrate_article_files()
    nf = _migrate_note_files()
    a = _migrate_index()
    n = _migrate_notes()
    s = _migrate_sessions()
    print(f"Imported: {af} article folders, {a} article records; {nf} notes; {s} chat sessions")
    print("Migration complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
