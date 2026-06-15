#!/usr/bin/env python
"""Create a new note in KBase.

Usage:
  cd <repo-root> && python kb/tools/create_note.py --title "My Note" --content "Note body"
  cd <repo-root> && python kb/tools/create_note.py --title "R" --content "..." --notebook "nb_xxx"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import post, put  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a new note")
    parser.add_argument("--title", required=True, help="Note title")
    parser.add_argument("--content", required=True, help="Note body in Markdown")
    parser.add_argument("--notebook", default="", help="Notebook ID (default: Inbox)")
    args = parser.parse_args()

    body: dict = {"title": args.title}
    if args.notebook:
        body["notebook_id"] = args.notebook

    try:
        result = post("/api/notes", body)
        note_id = result.get("id")
        if not note_id:
            print(f"Error: create note returned no id: {result}", file=sys.stderr)
            sys.exit(1)

        put(f"/api/notes/{note_id}", {"content": args.content, "title": args.title})

        output = {"status": "ok", "id": note_id, "title": args.title}
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        print()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
