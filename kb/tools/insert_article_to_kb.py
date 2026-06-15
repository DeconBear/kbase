#!/usr/bin/env python
"""Insert Markdown text directly as a knowledge base article entry.

Usage:
  cd <repo-root> && python kb/tools/insert_article_to_kb.py --title "Topic" --content "# Markdown..."
  cd <repo-root> && python kb/tools/insert_article_to_kb.py --title "T" --content "..." --tags "AI,ML"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, BASE, put, get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Insert text as an article entry")
    parser.add_argument("--title", required=True, help="Article title")
    parser.add_argument("--content", required=True, help="Article body in Markdown")
    parser.add_argument("--tags", default="", help="Comma-separated tags")
    parser.add_argument("--kind", default="note", help="Article kind (default: note)")
    args = parser.parse_args()

    ts = time.strftime("%Y%m%d_%H%M%S")
    uid = os.urandom(4).hex()
    article_id = f"art_{ts}_{uid}"
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]

    # Write markdown directly via /save endpoint
    md_path = f"articles/{article_id}/{article_id}.md"
    article_dir = os.path.dirname(md_path)

    # Create directory via server
    url = f"{BASE}/{md_path}"
    body_bytes = args.content.encode("utf-8")

    # Use raw PUT to save
    req = urllib.request.Request(
        f"{BASE}/save",
        data=json.dumps({"path": md_path, "content": args.content}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Save failed: HTTP {exc.code}: {detail[:300]}", file=sys.stderr)
        sys.exit(1)

    # Update metadata
    try:
        put("/api/articles/update", {
            "id": article_id,
            "updates": {
                "title": args.title,
                "kind": args.kind,
                "md_available": True,
                "file_available": False,
                "pdf_available": False,
                "date_added": time.strftime("%Y-%m-%d %H:%M"),
                "tags": tags,
                "source_filename": f"{article_id}.md",
            },
        })
    except Exception:
        pass

    output = {
        "status": "ok",
        "id": article_id,
        "title": args.title,
        "tags": tags,
    }
    output_json(output)
    print()


if __name__ == "__main__":
    main()
