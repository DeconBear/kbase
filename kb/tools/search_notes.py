#!/usr/bin/env python
"""Search notes by query string.

Usage:
  cd <repo-root> && python kb/tools/search_notes.py --query "keyword"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Search notes")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--limit", type=int, default=20, help="Max results")
    args = parser.parse_args()

    try:
        data = get("/api/notes")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    notes = data.get("notes") or []
    query_lower = args.query.lower()
    results = []
    for n in notes:
        title = str(n.get("title") or "").lower()
        if query_lower in title:
            results.append({
                "id": n["id"],
                "title": n.get("title", ""),
                "notebook_id": n.get("notebook_id", ""),
                "modified_at": n.get("modified_at", ""),
            })
            if len(results) >= args.limit:
                break

    output_json(results)
    print()


if __name__ == "__main__":
    main()
