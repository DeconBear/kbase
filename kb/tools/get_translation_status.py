#!/usr/bin/env python
"""Check translation status for an article.

Usage:
  cd <repo-root> && python kb/tools/get_translation_status.py --id "article_id"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Get translation status")
    parser.add_argument("--id", required=True, help="Article ID")
    args = parser.parse_args()

    try:
        result = get(f"/api/translation-status/{args.id}")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output_json(result)
    print()


if __name__ == "__main__":
    main()
