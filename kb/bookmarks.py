"""URL bookmarks stored under ``.kbase/bookmarks/``."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from storage import _atomic_write_json
from workspace import Workspace, new_doc_id

_URL_RE = re.compile(r"^https?://", re.I)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_url(url: str) -> str:
    raw = (url or "").strip()
    if not _URL_RE.match(raw):
        raise ValueError("URL 必须以 http:// 或 https:// 开头")
    parsed = urlparse(raw)
    if not parsed.netloc:
        raise ValueError("无效的 URL")
    return raw


def _bookmark_json_path(ws: Workspace, doc_id: str) -> Path:
    return ws.bookmarks_dir / f"{doc_id}.json"


def _bookmark_sidecar_path(ws: Workspace, doc_id: str) -> Path:
    return ws.documents_dir / f"{doc_id}.json"


def list_bookmarks(ws: Workspace) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    if not ws.bookmarks_dir.exists():
        return items
    for path in sorted(ws.bookmarks_dir.glob("doc_*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict):
            items.append(data)
    items.sort(key=lambda x: str(x.get("fetchedAt") or x.get("createdAt") or ""), reverse=True)
    return items


def create_bookmark(ws: Workspace, url: str, *, title: str = "") -> dict[str, Any]:
    canonical = _normalize_url(url)
    doc_id = new_doc_id()
    now = _now_iso()
    display = (title or "").strip() or urlparse(canonical).netloc or canonical
    snap_dir = ws.bookmarks_dir / doc_id
    snap_dir.mkdir(parents=True, exist_ok=True)

    snapshot_html = ""
    try:
        req = urllib.request.Request(
            canonical,
            headers={"User-Agent": "KBase/1.0 (+local bookmark fetch)"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read(512_000)
            snapshot_html = body.decode("utf-8", errors="replace")
        html_path = snap_dir / "page.html"
        html_path.write_text(snapshot_html, encoding="utf-8")
    except Exception:
        html_path = None

    record: dict[str, Any] = {
        "id": doc_id,
        "kind": "url",
        "url": canonical,
        "canonicalUrl": canonical,
        "title": display,
        "createdAt": now,
        "fetchedAt": now,
        "snapshot": {
            "html": f"bookmarks/{doc_id}/page.html" if snapshot_html else None,
        },
        "metadata": {"tags": []},
        "derivations": {},
    }
    _atomic_write_json(_bookmark_json_path(ws, doc_id), record)

    sidecar = {
        "id": doc_id,
        "kind": "url",
        "path": None,
        "title": display,
        "createdAt": now,
        "updatedAt": now,
        "status": "active",
        "metadata": {"url": canonical, "tags": []},
        "derivations": {},
        "ui": {"icon": "🔗", "pinned": False},
    }
    _atomic_write_json(_bookmark_sidecar_path(ws, doc_id), sidecar)
    return record


def delete_bookmark(ws: Workspace, doc_id: str) -> bool:
    json_path = _bookmark_json_path(ws, doc_id)
    if not json_path.exists():
        return False
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
        snap = ((data.get("snapshot") or {}).get("html") or "").replace("\\", "/")
        if snap.startswith("bookmarks/"):
            rel = snap[len("bookmarks/"):]
            target = ws.bookmarks_dir / rel
            if target.is_file():
                target.unlink()
            parent = target.parent
            if parent.exists() and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    except Exception:
        pass
    json_path.unlink(missing_ok=True)
    _bookmark_sidecar_path(ws, doc_id).unlink(missing_ok=True)
    return True
