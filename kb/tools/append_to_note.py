#!/usr/bin/env python
"""Append content to an existing note.

Usage:
  cd <repo-root> && python kb/tools/append_to_note.py --id "note_xxx" --content "Additional..."
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get, put  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Append content to a note")
    parser.add_argument("--id", required=True, help="Note ID")
    parser.add_argument("--content", required=True, help="Content to append (Markdown)")
    args = parser.parse_args()

    try:
        data = get(f"/api/notes/{args.id}")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Note not found: {args.id} ({exc})", file=sys.stderr)
        sys.exit(1)

    current_content = data.get("content") or ""
    new_content = current_content.rstrip() + "\n\n" + args.content

    try:
        put(f"/api/notes/{args.id}", {"content": new_content})
        output = {"status": "ok", "id": args.id}
        output_json(output)
        print()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
