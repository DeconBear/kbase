#!/usr/bin/env python
"""Trigger PDF parsing for an article using a specified engine.

Usage:
  cd <repo-root> && python kb/tools/trigger_parsing.py --id "article_id" --engine "marker"
  cd <repo-root> && python kb/tools/trigger_parsing.py --id "article_id" --engine "docparser" --docparser-engine "struct"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import post  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Trigger PDF parsing for an article")
    parser.add_argument("--id", required=True, help="Article ID")
    parser.add_argument("--engine", required=True, help="Engine name (marker, docmind, docparser, unisound, vision)")
    parser.add_argument("--docparser-engine", default="", help="DocParser engine variant (struct, etc.)")
    args = parser.parse_args()

    body: dict = {"id": args.id, "engine": args.engine}
    if args.docparser_engine:
        body["docparser_engine"] = args.docparser_engine

    try:
        result = post(f"/api/convert/{args.id}", body)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output = {
        "status": result.get("status", "unknown"),
        "id": args.id,
        "engine": args.engine,
    }
    json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
    print()


if __name__ == "__main__":
    main()
