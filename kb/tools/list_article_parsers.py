#!/usr/bin/env python
"""List available PDF parsing engines and their status.

Usage:
  cd <repo-root> && python kb/tools/list_article_parsers.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> None:
    engines = {
        "pymupdf": {"name": "PyMuPDF", "type": "local", "status": "available (built-in)"},
        "marker": {"name": "Marker (Surya)", "type": "local", "status": "checking..."},
        "docparser": {"name": "DocParser (DeconBear)", "type": "cloud", "status": "needs API key"},
        "docmind": {"name": "DocMind (Alibaba)", "type": "cloud", "status": "needs API key"},
        "vision_ocr": {"name": "Vision OCR", "type": "cloud", "status": "needs API key + provider"},
        "unisound": {"name": "Unisound U1", "type": "cloud", "status": "needs API key"},
    }

    # Check marker availability
    try:
        result = subprocess.run(
            [sys.executable, "-c", "import shutil; print(shutil.which('marker_single') or 'not found')"],
            capture_output=True, text=True, timeout=10,
        )
        if "not found" in result.stdout:
            engines["marker"]["status"] = "not installed"
        else:
            engines["marker"]["status"] = "available"
    except Exception:
        engines["marker"]["status"] = "check failed"

    # Check cloud engine keys from env
    if os.environ.get("DOCPARSER_API_KEY"):
        engines["docparser"]["status"] = "configured"
    if os.environ.get("DOCMIND_ACCESS_KEY_ID"):
        engines["docmind"]["status"] = "configured"
    if os.environ.get("UNISOUND_API_KEY"):
        engines["unisound"]["status"] = "configured"

    output_json(list(engines.values()))
    print()


if __name__ == "__main__":
    main()
