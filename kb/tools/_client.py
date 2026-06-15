"""Shared HTTP client for KBase CLI tools.

All tools use this module to call the KBase server API.
Set KBASE_URL env var to override the default (localhost:8765).
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


def output_json(data) -> None:
    """Write JSON to stdout safely (handles Windows GBK encoding)."""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    sys.stdout.buffer.write(text.encode("utf-8") + b"\n")

BASE = os.environ.get("KBASE_URL", "http://localhost:8765").rstrip("/")


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Send an HTTP request to the KBase server and return parsed JSON."""
    url = f"{BASE}{path}"
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed: {exc.reason}")


def get(path: str) -> dict:
    """GET request → parsed JSON."""
    return _request("GET", path)


def post(path: str, body: dict | None = None) -> dict:
    """POST request → parsed JSON."""
    return _request("POST", path, body)


def put(path: str, body: dict | None = None) -> dict:
    """PUT request → parsed JSON."""
    return _request("PUT", path, body)


def delete(path: str) -> dict:
    """DELETE request → parsed JSON."""
    return _request("DELETE", path)


def get_raw(path: str) -> str:
    """GET request → raw text response."""
    url = f"{BASE}{path}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {detail[:500]}")
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Connection failed: {exc.reason}")
