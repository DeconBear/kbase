"""PDF literature classification — rules, PyMuPDF heuristics, optional LLM."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from document_info import material_kind_from_filename

SUPPLEMENT_RE = re.compile(
    r"(supplement|supplementary|supporting|appendix|esm|si\b|figs?\.?\s*s\d)",
    re.IGNORECASE,
)
NON_LITERATURE_RE = re.compile(
    r"(readme|manual|slides?|poster|handout|syllabus|invoice|receipt|form)",
    re.IGNORECASE,
)
PAPER_HINTS = re.compile(
    r"\b(abstract|introduction|references|acknowledg|doi[:\s]|arxiv)",
    re.IGNORECASE,
)


def _norm_rel(path: str) -> str:
    return path.replace("\\", "/").lower()


def classify_pdf(
    pdf_path: Path,
    *,
    rel_path: str = "",
    literature_dir: str = "articles",
    use_llm: str = "uncertain_only",
    provider_id: str = "",
    model: str = "",
) -> dict[str, Any]:
    """Classify a PDF as literature or not.

    Returns dict with keys: is_literature, is_main, document_kind, confidence, reason.
    """
    rel = _norm_rel(rel_path or pdf_path.name)
    parts = rel.split("/")

    # Rule layer — canonical locations
    if len(parts) >= 3 and parts[-1] in ("original.pdf", "original.PDF".lower()):
        parent = parts[-2]
        lit = literature_dir.lower()
        if parts[-3] in (lit, "articles", "literature", ".literature") and parent:
            return {
                "is_literature": True,
                "is_main": True,
                "document_kind": "paper",
                "confidence": 1.0,
                "reason": "canonical_location",
            }

    if SUPPLEMENT_RE.search(rel) or SUPPLEMENT_RE.search(pdf_path.stem):
        return {
            "is_literature": True,
            "is_main": False,
            "document_kind": "supplement",
            "confidence": 0.9,
            "reason": "filename_supplement",
        }

    if NON_LITERATURE_RE.search(rel) or NON_LITERATURE_RE.search(pdf_path.stem):
        kind = "note" if "slide" in rel or "poster" in rel else "file"
        return {
            "is_literature": False,
            "is_main": False,
            "document_kind": kind,
            "confidence": 0.85,
            "reason": "filename_non_literature",
        }

    # PyMuPDF content layer
    page_count = 0
    sample_text = ""
    meta_title = ""
    meta_author = ""
    try:
        import fitz

        doc = fitz.open(str(pdf_path))
        page_count = doc.page_count
        meta = doc.metadata or {}
        meta_title = (meta.get("title") or "").strip()
        meta_author = (meta.get("author") or "").strip()
        chunks: list[str] = []
        for i in range(min(2, page_count)):
            chunks.append(doc[i].get_text("text") or "")
        doc.close()
        sample_text = "\n".join(chunks)[:3000]
    except Exception:
        sample_text = ""

    if page_count > 0 and page_count < 2 and len(sample_text.strip()) < 120:
        return {
            "is_literature": False,
            "is_main": False,
            "document_kind": "file",
            "confidence": 0.6,
            "reason": "too_short",
        }

    hint_score = len(PAPER_HINTS.findall(sample_text))
    has_meta = bool(meta_title and len(meta_title) > 4)
    if hint_score >= 2 or (hint_score >= 1 and page_count >= 4):
        return {
            "is_literature": True,
            "is_main": True,
            "document_kind": "paper",
            "confidence": min(0.95, 0.55 + hint_score * 0.12 + (0.1 if has_meta else 0)),
            "reason": "content_heuristic",
        }

    if hint_score == 1:
        uncertain = True
        base = {
            "is_literature": True,
            "is_main": True,
            "document_kind": "paper",
            "confidence": 0.5,
            "reason": "weak_heuristic",
        }
    else:
        uncertain = page_count >= 3
        base = {
            "is_literature": page_count >= 5,
            "is_main": page_count >= 5,
            "document_kind": material_kind_from_filename(pdf_path.name),
            "confidence": 0.35 if page_count >= 5 else 0.25,
            "reason": "default_by_pages",
        }

    if use_llm == "never" or not uncertain:
        return base

    if use_llm == "uncertain_only" and base.get("confidence", 0) >= 0.7:
        return base

    llm_result = _classify_with_llm(
        pdf_path,
        rel_path=rel,
        sample_text=sample_text[:1500],
        page_count=page_count,
        provider_id=provider_id,
        model=model,
    )
    if llm_result:
        return llm_result
    return base


def _classify_with_llm(
    pdf_path: Path,
    *,
    rel_path: str,
    sample_text: str,
    page_count: int,
    provider_id: str,
    model: str,
) -> dict[str, Any] | None:
    try:
        from llm_config import call_chat_completion
    except ImportError:
        return None

    prompt = f"""Classify this PDF file for an academic knowledge base.

Path: {rel_path or pdf_path.name}
Filename: {pdf_path.name}
Pages: {page_count}
First text sample:
{sample_text[:1500]}

Return JSON only:
{{
  "is_literature": true,
  "is_main": true,
  "document_kind": "paper|supplement|code|data|note|file"
}}"""
    try:
        data = call_chat_completion(
            [{"role": "user", "content": prompt}],
            provider_id=provider_id,
            model=model,
            temperature=0.1,
            max_tokens=256,
            timeout=45,
        )
        raw = (data.get("choices") or [{}])[0].get("message", {}).get("content") or ""
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return None
        parsed = json.loads(m.group())
        return {
            "is_literature": bool(parsed.get("is_literature")),
            "is_main": bool(parsed.get("is_main", parsed.get("is_literature"))),
            "document_kind": str(parsed.get("document_kind") or "paper"),
            "confidence": 0.75,
            "reason": "llm",
        }
    except Exception:
        return None
