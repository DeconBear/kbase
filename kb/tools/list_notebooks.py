#!/usr/bin/env python
"""List all notebooks with note counts.

Usage:
  cd <repo-root> && python kb/tools/list_notebooks.py
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, get  # noqa: E402


def main() -> None:
    try:
        nb_data = get("/api/notebooks")
        note_data = get("/api/notes")
    except SystemExit:
        raise
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    notebooks = nb_data.get("notebooks") or []
    notes = note_data.get("notes") or []

    counts: dict[str, int] = {}
    for n in notes:
        nb = n.get("notebook_id") or "nb_default"
        counts[nb] = counts.get(nb, 0) + 1

    result = []
    for nb in notebooks:
        result.append({
            "id": nb["id"],
            "name": nb.get("name", ""),
            "icon": nb.get("icon", ""),
            "note_count": counts.get(nb["id"], 0),
        })

    output_json(result)
    print()


if __name__ == "__main__":
    main()
