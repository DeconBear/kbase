#!/usr/bin/env python
"""Upload a file to the KBase library.

Usage:
  cd <repo-root> && python kb/tools/upload.py --file "/path/to/paper.pdf"
  cd <repo-root> && python kb/tools/upload.py --file "/path/to/report.docx"
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools._client import output_json, BASE  # noqa: E402
import urllib.request  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload a file to KBase")
    parser.add_argument("--file", required=True, help="Path to the file")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.isfile(file_path):
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    filename = os.path.basename(file_path)
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----KBaseTool{uuid.uuid4().hex}"

    parts: list[bytes] = []
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode())
    parts.append(f"Content-Type: {mime}\r\n\r\n".encode())
    with open(file_path, "rb") as f:
        parts.append(f.read())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    body_bytes = b"".join(parts)

    url = f"{BASE}/api/upload"
    req = urllib.request.Request(
        url,
        data=body_bytes,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        print(f"Upload failed: HTTP {exc.code}: {detail[:500]}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"Connection failed: {exc.reason}", file=sys.stderr)
        sys.exit(1)

    article = result.get("article") or {}
    output = {
        "status": result.get("status", "error"),
        "id": article.get("id", ""),
        "title": article.get("title", ""),
        "pages": article.get("pages", 0),
    }
    output_json(output)
    print()


if __name__ == "__main__":
    main()
