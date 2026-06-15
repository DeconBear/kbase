#!/usr/bin/env python
"""Update a note's title and/or content.

Usage:
  cd <repo-root> && python kb/tools/update_note.py --id "note_xxx" --title "New Title"
  cd <repo-root> && python kb/tools/update_note.py --id "note_xxx" --content "New body"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import put  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Update a note")
    parser.add_argument("--id", required=True, help="Note ID")
    parser.add_argument("--title", default=None, help="New title")
    parser.add_argument("--content", default=None, help="New content (Markdown, replaces all)")
    args = parser.parse_args()

    if not args.title and not args.content:
        print("Error: specify --title or --content", file=sys.stderr)
        sys.exit(1)

    body: dict = {}
    if args.title is not None:
        body["title"] = args.title
    if args.content is not None:
        body["content"] = args.content

    try:
        put(f"/api/notes/{args.id}", body)
        output = {"status": "ok", "id": args.id}
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        print()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
