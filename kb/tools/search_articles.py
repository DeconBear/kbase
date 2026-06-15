#!/usr/bin/env python
"""Search the KBase article library.

Usage:
  cd <repo-root> && python kb/tools/search_articles.py --query "quantum computing"
  cd <repo-root> && python kb/tools/search_articles.py --query "ML" --limit 5 --tag "AI"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Search KBase article library")
    parser.add_argument("--query", required=True, help="Search query string")
    parser.add_argument("--limit", type=int, default=20, help="Max results (default: 20)")
    parser.add_argument("--tag", default="", help="Filter by tag")
    args = parser.parse_args()

    try:
        data = get("/api/articles")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    articles = data.get("articles") or []
    results = []
    query_lower = args.query.lower()
    for a in articles:
        title = str(a.get("title") or "").lower()
        author = str(a.get("author") or "").lower()
        abstract = str(a.get("abstract") or "").lower()
        tags = [t.lower() for t in (a.get("tags") or [])]
        if query_lower in title or query_lower in author or query_lower in abstract or any(query_lower in t for t in tags):
            if args.tag and args.tag.lower() not in tags:
                continue
            results.append({
                "id": a["id"],
                "title": a.get("title", ""),
                "author": a.get("author", ""),
                "year": a.get("year", ""),
                "tags": a.get("tags", []),
                "kind": a.get("kind", ""),
                "md_available": a.get("md_available", False),
                "pdf_available": a.get("pdf_available", False),
            })
            if len(results) >= args.limit:
                break

    output_json(results)
    print()


if __name__ == "__main__":
    main()
