#!/usr/bin/env python
"""Get the note tree structure (nested JSON).

Usage:
  cd <repo-root> && python kb/tools/get_note_tree.py
  cd <repo-root> && python kb/tools/get_note_tree.py --notebook "nb_xxx"
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Get note tree structure")
    parser.add_argument("--notebook", default="", help="Filter by notebook ID")
    args = parser.parse_args()

    try:
        data = get("/api/notes")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    notes = data.get("notes") or []
    if args.notebook:
        notes = [n for n in notes if (n.get("notebook_id") or "nb_default") == args.notebook]

    # Build tree: root notes (no parent_id) with children nested
    by_id = {n["id"]: n for n in notes}
    children: dict[str, list] = {}
    for n in notes:
        pid = n.get("parent_id")
        if pid:
            children.setdefault(pid, []).append(n)

    def build_tree(n):
        node = {
            "id": n["id"],
            "title": n.get("title", ""),
            "modified_at": n.get("modified_at", ""),
        }
        kids = children.get(n["id"], [])
        if kids:
            node["children"] = [build_tree(c) for c in kids]
        return node

    roots = [n for n in notes if not n.get("parent_id")]
    tree = [build_tree(r) for r in roots]

    json.dump(tree, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    import argparse
    main()
