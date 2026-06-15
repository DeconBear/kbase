#!/usr/bin/env python
"""Get a single article's metadata and full Markdown content.

Usage:
  cd <repo-root> && python kb/tools/get_article.py --id "article_id"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get, get_raw  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Get article details")
    parser.add_argument("--id", required=True, help="Article ID")
    args = parser.parse_args()

    try:
        data = get("/api/articles")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    articles = data.get("articles") or []
    article = next((a for a in articles if a["id"] == args.id), None)
    if not article:
        print(f"Article not found: {args.id}", file=sys.stderr)
        sys.exit(1)

    content = ""
    for suffix in ("_calibrated.md", ".md", "_translated.md"):
        try:
            content = get_raw(f"/articles/{args.id}/{args.id}{suffix}")
            if content.strip():
                break
        except Exception:
            continue

    result = {
        "id": article["id"],
        "title": article.get("title", ""),
        "author": article.get("author", ""),
        "authors": article.get("authors", []),
        "doi": article.get("doi", ""),
        "year": article.get("year", ""),
        "venue": article.get("venue", ""),
        "abstract": article.get("abstract", ""),
        "tags": article.get("tags", []),
        "kind": article.get("kind", ""),
        "pages": article.get("pages", 0),
        "content": content,
    }
    output_json(result)
    print()


if __name__ == "__main__":
    main()
