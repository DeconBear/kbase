"""Workspace derivation publishing — adjacent .parsed.md / .zh.md + sidecar links."""
from __future__ import annotations

import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import ARTICLES_DIR, _atomic_write_json
from workspace import Workspace, content_hash, get_active_workspace, new_doc_id

_LANG_CODES = {
    "simplified chinese": "zh",
    "traditional chinese": "zh-tw",
    "chinese": "zh",
    "english": "en",
    "japanese": "ja",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
}


def lang_to_code(target_language: str) -> str:
    key = (target_language or "").strip().lower()
    if key in _LANG_CODES:
        return _LANG_CODES[key]
    if len(key) == 2 and key.isalpha():
        return key
    if "chinese" in key or "中文" in key:
        return "zh"
    return "zh"


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def resolve_legacy_pdf(article_id: str) -> Path | None:
    article_dir = ARTICLES_DIR / article_id
    if not article_dir.is_dir():
        return None
    for path in sorted(article_dir.iterdir()):
        if path.is_file() and path.suffix.lower() == ".pdf":
            return path
    original = article_dir / "original.pdf"
    return original if original.exists() else None


def _ensure_under_workspace(ws: Workspace, abs_path: Path) -> bool:
    try:
        abs_path.resolve().relative_to(ws.root)
        return True
    except ValueError:
        return False


def _ensure_document(ws: Workspace, abs_path: Path, *, kind: str | None = None) -> dict[str, Any]:
    rel = ws.rel_path(abs_path)
    existing = ws.find_document_by_path(rel)
    if existing:
        return existing
    return ws.register_file(rel, kind=kind)


def _register_derivation_file(
    ws: Workspace,
    abs_path: Path,
    *,
    source_doc_id: str,
    derivation_type: str,
) -> dict[str, Any]:
    rel = ws.rel_path(abs_path)
    existing = ws.find_document_by_path(rel)
    now = _now_iso()
    if existing:
        doc = existing
    else:
        doc = {
            "id": new_doc_id(),
            "kind": "markdown",
            "path": rel,
            "contentHash": content_hash(abs_path),
            "title": Path(rel).stem,
            "createdAt": now,
            "updatedAt": now,
            "status": "active",
            "metadata": {
                "sourceDocId": source_doc_id,
                "derivationType": derivation_type,
            },
            "derivations": {},
            "ui": {"icon": "📄", "pinned": False},
        }
    doc["contentHash"] = content_hash(abs_path)
    doc["status"] = "active"
    doc.setdefault("metadata", {})["sourceDocId"] = source_doc_id
    doc["metadata"]["derivationType"] = derivation_type
    ws.save_document(doc)
    return doc


def _write_task(ws: Workspace, task_id: str, payload: dict[str, Any]) -> None:
    path = ws.tasks_dir / f"{task_id}.json"
    _atomic_write_json(path, payload)


def publish_parsed_derivation(
    ws: Workspace,
    source_file: Path,
    parsed_file: Path,
    *,
    engine: str,
) -> dict[str, Any]:
    """Copy parsed markdown adjacent to source and update sidecars + links."""
    source_file = source_file.resolve()
    parsed_file = parsed_file.resolve()
    if not _ensure_under_workspace(ws, source_file):
        raise ValueError("源文件不在当前工作空间内")

    source_doc = _ensure_document(ws, source_file)
    source_rel = str(source_doc.get("path") or ws.rel_path(source_file))
    target_rel = ws.parsed_md_path(source_rel)
    target_abs = ws.resolve(target_rel)

    if parsed_file.resolve() != target_abs.resolve():
        target_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(parsed_file, target_abs)

    deriv_doc = _register_derivation_file(
        ws,
        target_abs,
        source_doc_id=str(source_doc["id"]),
        derivation_type="parsed_md",
    )

    now = _now_iso()
    source_doc.setdefault("derivations", {})
    source_doc["derivations"]["parsed_md"] = {
        "path": target_rel,
        "engine": engine,
        "status": "done",
        "completedAt": now,
        "linkedDocId": deriv_doc["id"],
    }
    ws.save_document(source_doc)
    ws.upsert_link(
        link_type="derivation",
        from_id=str(source_doc["id"]),
        to_id=str(deriv_doc["id"]),
        label="parsed_md",
    )

    task_id = f"parse_{source_doc['id']}_{int(time.time())}"
    _write_task(
        ws,
        task_id,
        {
            "id": task_id,
            "type": "parse",
            "status": "done",
            "engine": engine,
            "sourceDocId": source_doc["id"],
            "outputDocId": deriv_doc["id"],
            "completedAt": now,
        },
    )
    return {"sourceDocId": source_doc["id"], "parsedDocId": deriv_doc["id"], "path": target_rel}


def publish_translation_derivation(
    ws: Workspace,
    source_file: Path,
    translated_file: Path,
    *,
    lang: str,
    source_derivation: str = "parsed_md",
) -> dict[str, Any]:
    """Register adjacent ``{basename}.{lang}.md`` translation."""
    source_file = source_file.resolve()
    translated_file = translated_file.resolve()
    if not _ensure_under_workspace(ws, source_file):
        raise ValueError("源文件不在当前工作空间内")

    source_doc = _ensure_document(ws, source_file)
    source_rel = str(source_doc.get("path") or ws.rel_path(source_file))
    target_rel = ws.translated_md_path(source_rel, lang)
    target_abs = ws.resolve(target_rel)

    if translated_file.resolve() != target_abs.resolve():
        target_abs.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(translated_file, target_abs)

    deriv_doc = _register_derivation_file(
        ws,
        target_abs,
        source_doc_id=str(source_doc["id"]),
        derivation_type="translated_md",
    )

    now = _now_iso()
    source_doc.setdefault("derivations", {})
    source_doc["derivations"]["translated_md"] = {
        "path": target_rel,
        "sourceDerivation": source_derivation,
        "lang": lang,
        "status": "done",
        "completedAt": now,
        "linkedDocId": deriv_doc["id"],
    }
    ws.save_document(source_doc)
    ws.upsert_link(
        link_type="translation",
        from_id=str(source_doc["id"]),
        to_id=str(deriv_doc["id"]),
        label=lang,
    )

    task_id = f"translate_{source_doc['id']}_{lang}_{int(time.time())}"
    _write_task(
        ws,
        task_id,
        {
            "id": task_id,
            "type": "translate",
            "status": "done",
            "lang": lang,
            "sourceDocId": source_doc["id"],
            "outputDocId": deriv_doc["id"],
            "completedAt": now,
        },
    )
    return {"sourceDocId": source_doc["id"], "translatedDocId": deriv_doc["id"], "path": target_rel}


def lookup_article_derivations(ws: Workspace, article_id: str) -> dict[str, Any] | None:
    prefix = f"articles/{article_id}/"
    pdf_doc: dict[str, Any] | None = None
    for doc in ws.list_documents(kind="pdf"):
        path = str(doc.get("path") or "").replace("\\", "/")
        if path.startswith(prefix):
            pdf_doc = doc
            break
    if pdf_doc is None:
        return None
    derivations = pdf_doc.get("derivations") or {}
    out: dict[str, Any] = {}
    for key, meta in derivations.items():
        if not isinstance(meta, dict):
            continue
        rel = str(meta.get("path") or "").replace("\\", "/")
        out[key] = {
            **meta,
            "url": f"/{rel}" if rel else None,
        }
    return {
        "articleId": article_id,
        "docId": pdf_doc.get("id"),
        "sourcePath": pdf_doc.get("path"),
        "derivations": out,
    }


def sync_legacy_parse(article_id: str, parsed_md: Path, engine: str) -> dict[str, Any] | None:
    ws = get_active_workspace()
    if ws is None:
        return None
    pdf = resolve_legacy_pdf(article_id)
    if pdf is None or not _ensure_under_workspace(ws, pdf):
        return None
    try:
        return publish_parsed_derivation(ws, pdf, parsed_md, engine=engine)
    except Exception:
        return None


def preparse_word_document(ws: Workspace, doc_id: str) -> dict[str, Any] | None:
    doc = ws.load_document(doc_id)
    if not doc or doc.get("kind") != "word":
        return None
    rel = str(doc.get("path") or "")
    if not rel:
        return None
    if ws.is_readonly_path(rel):
        raise ValueError("外部资料源为只读；请先复制到托管区再解析")
    source_abs = ws.resolve(rel)
    if not source_abs.is_file():
        return None
    parsed_rel = ws.parsed_md_path(rel)
    parsed_abs = ws.resolve(parsed_rel)
    if not parsed_abs.exists():
        from word_extract import docx_to_markdown

        parsed_abs.parent.mkdir(parents=True, exist_ok=True)
        parsed_abs.write_text(
            docx_to_markdown(source_abs, title=str(doc.get("title") or source_abs.stem)),
            encoding="utf-8",
        )
    try:
        return publish_parsed_derivation(ws, source_abs, parsed_abs, engine="docx")
    except Exception:
        return None


def sync_legacy_translation(
    article_id: str,
    translated_md: Path,
    target_language: str,
) -> dict[str, Any] | None:
    ws = get_active_workspace()
    if ws is None:
        return None
    pdf = resolve_legacy_pdf(article_id)
    if pdf is None or not _ensure_under_workspace(ws, pdf):
        return None
    lang = lang_to_code(target_language)
    try:
        return publish_translation_derivation(
            ws,
            pdf,
            translated_md,
            lang=lang,
            source_derivation="parsed_md",
        )
    except Exception:
        return None
