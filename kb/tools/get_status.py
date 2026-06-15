#!/usr/bin/env python
"""Get KBase server status.

Usage:
  cd <repo-root> && python kb/tools/get_status.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import get  # noqa: E402


def main() -> None:
    try:
        articles = get("/api/articles")
        notes = get("/api/notes")
        llm = get("/api/llm-config")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: server may not be running ({exc})", file=sys.stderr)
        sys.exit(1)

    output = {
        "server": "running",
        "articles": len(articles.get("articles") or []),
        "notes": len(notes.get("notes") or []),
        "providers": len(llm.get("providers") or []),
        "active_provider": llm.get("active_provider", ""),
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
