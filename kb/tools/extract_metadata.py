#!/usr/bin/env python
"""Trigger LLM metadata extraction for an article.

Usage:
  cd <repo-root> && python kb/tools/extract_metadata.py --id "article_id"
  cd <repo-root> && python kb/tools/extract_metadata.py --id "..." --provider "openai" --model "gpt-4o"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import post  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract article metadata via LLM")
    parser.add_argument("--id", required=True, help="Article ID")
    parser.add_argument("--provider", default="", help="LLM provider")
    parser.add_argument("--model", default="", help="LLM model")
    parser.add_argument("--background", action="store_true", help="Run in background")
    args = parser.parse_args()

    body: dict = {"id": args.id, "reason": "manual"}
    if args.provider:
        body["provider_id"] = args.provider
    if args.model:
        body["model"] = args.model
    if args.background:
        body["background"] = True

    try:
        result = post(f"/api/extract-info/{args.id}", body)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
