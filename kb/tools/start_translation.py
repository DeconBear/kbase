#!/usr/bin/env python
"""Start translation for an article.

Usage:
  cd <repo-root> && python kb/tools/start_translation.py --id "article_id"
  cd <repo-root> && python kb/tools/start_translation.py --id "article_id" --mode "update" --language "Japanese"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import post  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Start article translation")
    parser.add_argument("--id", required=True, help="Article ID")
    parser.add_argument("--mode", default="update", choices=["update", "full"],
                        help="Translation mode: update (reuse old) or full (from scratch)")
    parser.add_argument("--language", default="Simplified Chinese", help="Target language")
    parser.add_argument("--extra-prompt", default="", help="Additional translation instructions")
    args = parser.parse_args()

    body: dict = {
        "id": args.id,
        "mode": args.mode,
        "target_language": args.language,
    }
    if args.extra_prompt:
        body["extra_prompt"] = args.extra_prompt

    try:
        result = post(f"/api/translate/{args.id}", body)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output = {
        "status": result.get("status", "unknown"),
        "id": args.id,
        "mode": args.mode,
        "language": args.language,
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
