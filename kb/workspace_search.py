"""Full-text search over workspace documents (markdown + parsed derivations)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from library_chat import _chunk_markdown, _plain_snippet, _query_terms
from workspace import Workspace, is_derivation_path


def _readable_paths(ws: Workspace, doc: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    kind = doc.get("kind")
    rel = str(doc.get("path") or "").replace("\\", "/")
    if kind == "markdown" and rel and not is_derivation_path(rel):
        paths.append(rel)
    derivations = doc.get("derivations") or {}
    for key in ("parsed_md", "translated_md", "summary_md"):
        meta = derivations.get(key) or {}
        p = str(meta.get("path") or "").replace("\\", "/")
        if p and p not in paths:
            paths.append(p)
    if kind == "word" and rel:
        parsed = ws.parsed_md_path(rel)
        if parsed not in paths:
            paths.append(parsed)
    return paths


def _score_chunk(
    hay: str,
    meta_text: str,
    heading: str,
    query: str,
    terms: list[str],
) -> int:
    score = 0
    if query and query.lower() in hay:
        score += 12
    for term in terms:
        count = hay.count(term.lower())
        if count:
            score += min(count, 8)
            if term in meta_text:
                score += 4
            if term in heading.lower():
                score += 3
    if not terms:
        score = 1
    return score


def search_workspace_documents(
    ws: Workspace,
    query: str,
    *,
    limit: int = 20,
    context_chars: int = 8000,
    kind: str | None = None,
) -> list[dict[str, Any]]:
    try:
        from workspace_index import search_fts

        fts_hits = search_fts(ws, query, limit=limit)
        if fts_hits:
            return [
                {
                    "source_id": f"F{i + 1}",
                    "doc_id": h["doc_id"],
                    "article_id": h["doc_id"],
                    "title": h.get("title") or h.get("path"),
                    "author": "",
                    "heading": "",
                    "variant": "fts",
                    "path": h.get("path"),
                    "kind": None,
                    "score": limit - i,
                    "text": "",
                    "snippet": h.get("snippet") or "",
                }
                for i, h in enumerate(fts_hits)
            ]
    except Exception:
        pass

    terms = _query_terms(query)
    results: list[dict[str, Any]] = []
    source_no = 1

    for doc in ws.list_documents():
        if doc.get("status") == "missing":
            continue
        if kind and doc.get("kind") != kind:
            continue
        doc_id = str(doc.get("id") or "")
        meta_text = " ".join(
            str(doc.get(k) or "")
            for k in ("title", "path", "kind")
        ).lower()
        tags = (doc.get("metadata") or {}).get("tags") or []
        if tags:
            meta_text += " " + " ".join(str(t) for t in tags).lower()

        for rel in _readable_paths(ws, doc):
            try:
                abs_path = ws.resolve(rel)
            except ValueError:
                continue
            if not abs_path.is_file():
                continue
            try:
                md = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            variant = "parsed_md" if rel.endswith(".parsed.md") else "markdown"
            if rel.endswith(".zh.md") or re.search(r"\.[a-z]{2}\.md$", rel):
                variant = "translated_md"
            for chunk in _chunk_markdown(md):
                hay = (meta_text + "\n" + chunk["heading"] + "\n" + chunk["text"]).lower()
                score = _score_chunk(hay, meta_text, chunk["heading"], query, terms)
                if score <= 0:
                    continue
                results.append({
                    "source_id": f"W{source_no}",
                    "doc_id": doc_id,
                    "article_id": doc_id,
                    "title": doc.get("title") or rel,
                    "author": "",
                    "heading": chunk["heading"],
                    "variant": variant,
                    "path": rel,
                    "kind": doc.get("kind"),
                    "score": score,
                    "text": chunk["text"],
                    "snippet": _plain_snippet(chunk["text"]),
                })
                source_no += 1

    results.sort(key=lambda item: item["score"], reverse=True)
    selected: list[dict[str, Any]] = []
    used = 0
    seen: set[tuple[str, str, str]] = set()
    for item in results:
        key = (item["doc_id"], item["heading"], item["variant"])
        if key in seen:
            continue
        seen.add(key)
        chunk_len = len(item.get("text") or "")
        if used + chunk_len > context_chars and selected:
            continue
        selected.append(item)
        used += chunk_len
        if len(selected) >= limit:
            break
    return selected
