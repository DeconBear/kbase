#!/usr/bin/env python
"""Export articles as BibTeX.

Usage:
  cd <repo-root> && python kb/tools/export_bibtex.py --ids "id1,id2"
  cd <repo-root> && python kb/tools/export_bibtex.py --ids "id1,id2" --output refs.bib
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import BASE  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Export articles as BibTeX")
    parser.add_argument("--ids", required=True, help="Comma-separated article IDs")
    parser.add_argument("--output", default="", help="Output file path (default: stdout)")
    args = parser.parse_args()

    ids = [i.strip() for i in args.ids.split(",") if i.strip()]
    if not ids:
        print("Error: no valid IDs", file=sys.stderr)
        sys.exit(1)

    url = f"{BASE}/api/export?ids={','.join(ids)}&format=bibtex"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            bib = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Export failed: HTTP {exc.code}: {detail[:300]}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(bib)
        output = {"status": "ok", "count": len(ids), "file": args.output}
        json.dump(output, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(bib)


if __name__ == "__main__":
    main()
