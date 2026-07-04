"""Bridge legacy article/note IDs to workspace ``doc_id`` values."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from workspace import Workspace, get_active_workspace


def load_id_map(ws: Workspace | None = None) -> dict[str, dict[str, str]]:
    ws = ws or get_active_workspace()
    if ws is None:
        return {"articles": {}, "notes": {}}
    path = ws.kbase / "migration" / "id_map.json"
    if not path.exists():
        return {"articles": {}, "notes": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"articles": {}, "notes": {}}
    if not isinstance(data, dict):
        return {"articles": {}, "notes": {}}
    return {
        "articles": dict(data.get("articles") or {}),
        "notes": dict(data.get("notes") or {}),
    }


def enrich_articles(articles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    id_map = load_id_map().get("articles") or {}
    if not id_map:
        return articles
    out: list[dict[str, Any]] = []
    for article in articles:
        row = dict(article)
        aid = str(row.get("id") or "")
        if aid in id_map:
            row["workspaceDocId"] = id_map[aid]
        out.append(row)
    return out


def enrich_notes(notes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    id_map = load_id_map().get("notes") or {}
    if not id_map:
        return notes
    out: list[dict[str, Any]] = []
    for note in notes:
        row = dict(note)
        nid = str(row.get("id") or "")
        if nid in id_map:
            row["workspaceDocId"] = id_map[nid]
        out.append(row)
    return out
