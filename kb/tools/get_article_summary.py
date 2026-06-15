#!/usr/bin/env python
"""Get an article's AI-generated summary.

Usage:
  cd <repo-root> && python kb/tools/get_article_summary.py --id "article_id"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get_raw, get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Get article summary")
    parser.add_argument("--id", required=True, help="Article ID")
    args = parser.parse_args()

    try:
        summary = get_raw(f"/articles/{args.id}/{args.id}_summary.md")
    except Exception:
        summary = ""

    try:
        data = get("/api/articles")
        articles = data.get("articles") or []
        article = next((a for a in articles if a["id"] == args.id), {"id": args.id, "title": ""})
    except Exception:
        article = {"id": args.id, "title": ""}

    output = {
        "id": args.id,
        "title": article.get("title", ""),
        "summary": summary.strip() if summary else "(no summary available)",
    }
    output_json(output)
    print()


if __name__ == "__main__":
    main()
