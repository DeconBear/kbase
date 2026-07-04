"""Migrate legacy ``data/`` layout to workspace sidecar model (in-place or copy)."""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

_KB_DIR = Path(__file__).resolve().parent
if str(_KB_DIR) not in sys.path:
    sys.path.insert(0, str(_KB_DIR))

from storage import (  # noqa: E402
    ARTICLES_DIR,
    DATA_ROOT,
    KBASE_DIR,
    NOTES_DIR,
    _atomic_write_json,
    ensure_directories,
    get_all_articles,
    get_all_notes,
    init_db,
)
from workspace import Workspace, open_workspace  # noqa: E402


def _copy_tree(src: Path, dst: Path, *, dry_run: bool) -> int:
    if not src.exists():
        return 0
    if dry_run:
        print(f"[dry-run] would copy {src} -> {dst}")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        if dst.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        else:
            shutil.copytree(src, dst)
    else:
        shutil.copy2(src, dst)
    return 1


def _migrate_files(from_root: Path, to_root: Path, *, dry_run: bool) -> dict[str, int]:
    counts = {"articles": 0, "notes": 0, "databases": 0}
    src_articles = from_root / "articles"
    dst_articles = to_root / "articles"
    if src_articles.exists() and src_articles != dst_articles:
        for item in src_articles.iterdir():
            if item.is_dir():
                dest = dst_articles / item.name
                if not dest.exists():
                    counts["articles"] += _copy_tree(item, dest, dry_run=dry_run)
    src_notes = from_root / "notes"
    dst_notes = to_root / "notes"
    if src_notes.exists() and src_notes != dst_notes:
        dst_notes.mkdir(parents=True, exist_ok=True)
        for item in src_notes.glob("*.md"):
            dest = dst_notes / item.name
            if not dest.exists():
                counts["notes"] += _copy_tree(item, dest, dry_run=dry_run)
    src_db = from_root / ".kbase" / "databases"
    dst_db = to_root / ".kbase" / "databases"
    if src_db.exists():
        dst_db.mkdir(parents=True, exist_ok=True)
        for item in src_db.glob("*.json"):
            dest = dst_db / item.name
            if not dest.exists():
                counts["databases"] += _copy_tree(item, dest, dry_run=dry_run)
    return counts


def _build_id_map(ws: Workspace) -> dict[str, Any]:
    articles_map: dict[str, str] = {}
    notes_map: dict[str, str] = {}

    for article in get_all_articles():
        aid = str(article.get("id") or "")
        if not aid:
            continue
        prefix = f"articles/{aid}/"
        for doc in ws.list_documents(kind="pdf"):
            path = str(doc.get("path") or "")
            if path.startswith(prefix) or path == f"articles/{aid}/original.pdf":
                articles_map[aid] = str(doc["id"])
                break
        if aid not in articles_map:
            for doc in ws.list_documents():
                path = str(doc.get("path") or "")
                if path.startswith(prefix) and doc.get("kind") in ("pdf", "markdown"):
                    articles_map[aid] = str(doc["id"])
                    break

    for note in get_all_notes():
        nid = str(note.get("id") or "")
        if not nid:
            continue
        candidates = [
            f"notes/{nid}.md",
            f"notes/{note.get('title', '')}.md",
        ]
        for doc in ws.list_documents(kind="markdown"):
            path = str(doc.get("path") or "")
            if path in candidates or path.endswith(f"/{nid}.md"):
                notes_map[nid] = str(doc["id"])
                break

    return {"articles": articles_map, "notes": notes_map}


def run_migration(
    from_root: Path,
    to_root: Path,
    *,
    dry_run: bool = False,
    reindex_only: bool = False,
) -> dict[str, Any]:
    ensure_directories()
    init_db()
    from_root = from_root.resolve()
    to_root = to_root.resolve()

    file_counts: dict[str, int] = {}
    if not reindex_only and from_root != to_root:
        file_counts = _migrate_files(from_root, to_root, dry_run=dry_run)
    elif not reindex_only:
        print(f"In-place migration on {to_root}")

    if dry_run:
        return {"ok": True, "dryRun": True, "fileCounts": file_counts}

    ws = open_workspace(to_root, scan=True)
    stats = ws.scan(full=True)
    id_map = _build_id_map(ws)
    migration_dir = ws.kbase / "migration"
    migration_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(migration_dir / "id_map.json", id_map)
    manifest = ws.load_manifest()
    manifest["migratedFrom"] = str(from_root)
    manifest["migratedAt"] = stats.get("lastScanAt") or manifest.get("openedAt")
    manifest["migrationComplete"] = True
    ws.save_manifest(manifest)

    return {
        "ok": True,
        "workspace": ws.info(),
        "scan": stats,
        "idMap": {
            "articles": len(id_map.get("articles") or {}),
            "notes": len(id_map.get("notes") or {}),
        },
        "fileCounts": file_counts,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy data/ to workspace model")
    parser.add_argument("--from", dest="from_root", default=str(DATA_ROOT), help="Source data root")
    parser.add_argument("--to", dest="to_root", default="", help="Target workspace root (default: same as --from)")
    parser.add_argument("--dry-run", action="store_true", help="Print plan only")
    parser.add_argument(
        "--reindex-only",
        action="store_true",
        help="Skip file copy; only scan and build id_map",
    )
    args = parser.parse_args(argv)

    from_root = Path(args.from_root)
    to_root = Path(args.to_root) if args.to_root else from_root

    if not from_root.is_dir():
        print(f"Source not found: {from_root}", file=sys.stderr)
        return 1

    report = run_migration(
        from_root,
        to_root,
        dry_run=args.dry_run,
        reindex_only=args.reindex_only,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
