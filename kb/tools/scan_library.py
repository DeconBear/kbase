#!/usr/bin/env python
"""Trigger a full library rescan (reconcile filesystem with SQLite).

Usage:
  cd <repo-root> && python kb/tools/scan_library.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import get  # noqa: E402


def main() -> None:
    try:
        data = get("/api/articles")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    articles = data.get("articles") or []
    output = {
        "status": "ok",
        "articles": len(articles),
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
