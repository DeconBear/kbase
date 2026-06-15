#!/usr/bin/env python
"""Ask a question against the full KBase library (RAG search).

Usage:
  cd <repo-root> && python kb/tools/ask_library.py --question "What is quantum annealing?"
  cd <repo-root> && python kb/tools/ask_library.py --question "..." --workspace "ws_xxx"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, post  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ask the full KBase library (RAG)")
    parser.add_argument("--question", required=True, help="Question to ask")
    parser.add_argument("--session", default="", help="Chat session ID (optional)")
    parser.add_argument("--workspace", default="", help="Workspace ID to scope search (optional)")
    args = parser.parse_args()

    body: dict = {"question": args.question, "message": args.question}
    if args.session:
        body["session_id"] = args.session
    if args.workspace:
        body["workspace_id"] = args.workspace

    try:
        result = post("/api/library-chat/ask", body)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    output = {
        "answer": result.get("answer", ""),
        "sources": result.get("sources") or [],
    }
    output_json(output)
    print()


if __name__ == "__main__":
    main()
