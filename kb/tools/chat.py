#!/usr/bin/env python
"""Send a message to the KBase LLM chat.

Usage:
  cd <repo-root> && python kb/tools/chat.py --message "Explain quantum computing"
  cd <repo-root> && python kb/tools/chat.py --message "..." --provider "openai" --model "gpt-4o"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, post  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Chat with KBase LLM")
    parser.add_argument("--message", required=True, help="Message to send")
    parser.add_argument("--provider", default="", help="LLM provider ID (default: active provider)")
    parser.add_argument("--model", default="", help="Model name (default: provider default)")
    args = parser.parse_args()

    body: dict = {"messages": [{"role": "user", "content": args.message}]}
    if args.provider:
        body["provider_id"] = args.provider
    if args.model:
        body["model"] = args.model

    try:
        result = post("/api/chat", body)
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    choices = result.get("choices") or [{}]
    reply = choices[0].get("message", {}).get("content") or ""

    output = {"reply": reply, "model": result.get("model", "")}
    output_json(output)
    print()


if __name__ == "__main__":
    main()
