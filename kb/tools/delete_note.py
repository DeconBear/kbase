#!/usr/bin/env python
"""Delete a note.

Usage:
  cd <repo-root> && python kb/tools/delete_note.py --id "note_xxx"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, delete  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Delete a note")
    parser.add_argument("--id", required=True, help="Note ID")
    args = parser.parse_args()

    try:
        delete(f"/api/notes/{args.id}")
        output = {"status": "ok", "id": args.id}
        output_json(output)
        print()
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
