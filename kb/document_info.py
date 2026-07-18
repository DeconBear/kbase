"""Fast document ingestion and LLM metadata extraction."""
from __future__ import annotations

import json
import mimetypes
import re
import time
from pathlib import Path

from llm_config import call_chat_completion
import storage

TEXT_EXTS = {
    ".bib", ".c", ".cc", ".cfg", ".cpp", ".csv", ".h", ".hpp", ".ipynb",
    ".java", ".js", ".json", ".log", ".m", ".md", ".py", ".r", ".rst",
    ".sh", ".tex", ".toml", ".ts", ".txt", ".yaml", ".yml",
}


def _article_dir(article_id: str) -> Path:
    return storage.ARTICLES_DIR / article_id


def _preferred_markdown(article_id: str) -> Path | None:
    from workspace_paths import adjacent_parsed_md_path

    art_dir = _article_dir(article_id)
    calibrated = art_dir / f"{article_id}_calibrated.md"
    if calibrated.exists():
        return calibrated
    pdf_candidates = [p for p in art_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"]
    pdf_path = pdf_candidates[0] if pdf_candidates else art_dir / "original.pdf"
    adjacent = adjacent_parsed_md_path(art_dir, pdf_path, article_id)
    if adjacent.exists():
        return adjacent
    legacy = art_dir / f"{article_id}.md"
    if legacy.exists():
        return legacy
    translated = art_dir / f"{article_id}_translated.md"
    if translated.exists():
        return translated
    return None


def _clean_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", (text or "").replace("\x00", "")).strip()


def assess_preparse_quality(page_stats: list[dict] | None) -> dict:
    """Score text-layer coverage for UI hints (skip cloud OCR when rich)."""
    stats = page_stats or []
    n = len(stats)
    if n <= 0:
        return {
            "pages": 0,
            "rich_pages": 0,
            "empty_pages": 0,
            "avg_chars": 0,
            "coverage": 0.0,
            "recommend_text_layer": False,
        }
    rich = sum(1 for p in stats if int(p.get("chars") or 0) >= 80)
    empty = sum(1 for p in stats if int(p.get("chars") or 0) < 20)
    total_chars = sum(int(p.get("chars") or 0) for p in stats)
    coverage = rich / n
    return {
        "pages": n,
        "rich_pages": rich,
        "empty_pages": empty,
        "avg_chars": int(total_chars / n),
        "coverage": round(coverage, 3),
        "recommend_text_layer": coverage >= 0.7 and n >= 5,
    }


def quick_parse_pdf(article_id: str, pdf_path: Path, source_filename: str = "") -> dict:
    """Create a first-pass Markdown file from PyMuPDF immediately after upload."""
    import fitz
    from workspace_paths import publish_engine_markdown

    art_dir = _article_dir(article_id)
    doc = fitz.open(str(pdf_path))
    meta = doc.metadata or {}
    toc = []
    try:
        toc = doc.get_toc(simple=True) or []
    except Exception:
        toc = []
    pages: list[dict] = []
    for i in range(doc.page_count):
        text = _clean_text(doc[i].get_text("text"))
        pages.append({"page": i + 1, "chars": len(text), "text": text})
    doc.close()

    title = (meta.get("title") or "").strip()
    if not title:
        title = Path(source_filename or pdf_path).stem.replace("_", " ")

    quality = assess_preparse_quality(
        [{"page": p["page"], "chars": p["chars"]} for p in pages]
    )
    markdown_parts = [
        f"# {title}",
        "",
        f"<!-- kbase-preparse: pymupdf · {len(pages)} pages"
        f" · coverage {quality['coverage']:.0%} -->",
        "",
        "> PyMuPDF 文本层全文。数字版 PDF 通常已可直接阅读；扫描件请改用云 OCR。",
        "",
    ]
    if toc:
        markdown_parts.append("## 目录")
        markdown_parts.append("")
        for level, heading, page in toc[:80]:
            try:
                lvl = max(1, min(int(level), 3))
            except (TypeError, ValueError):
                lvl = 1
            pad = "  " * (lvl - 1)
            markdown_parts.append(f"{pad}- {heading} · p.{page}")
        markdown_parts.append("")
        markdown_parts.append("---")
        markdown_parts.append("")

    for page in pages:
        markdown_parts.extend([
            f"<!-- kbase-page: {page['page']} -->",
            "",
            page["text"] or "_（本页无文本层）_",
            "",
            "---",
            "",
        ])

    md_text = "\n".join(markdown_parts).strip() + "\n"
    md_path = publish_engine_markdown(art_dir, article_id, pdf_path, md_text=md_text)
    versioned = art_dir / f"{article_id}_pymupdf.md"
    versioned.write_text(md_text, encoding="utf-8")
    storage.record_article_history(article_id, "pymupdf", versioned)

    toc_public = [{"level": lv, "title": h, "page": pg} for lv, h, pg in toc[:200]]
    meta_payload = {
        "source": "pymupdf",
        "source_filename": source_filename,
        "title": title,
        "metadata": meta,
        "page_stats": [{"page": p["page"], "chars": p["chars"]} for p in pages],
        "preparse_quality": quality,
        "table_of_contents": toc_public or [{"title": title}],
    }
    meta_path = art_dir / f"{article_id}_meta.json"
    meta_path.write_text(json.dumps(meta_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "title": title,
        "pages": len(pages),
        "meta": meta_payload,
        "md_path": md_path,
        "quality": quality,
    }


def material_kind_from_filename(filename: str) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        return "paper"
    if ext in {".zip", ".tar", ".gz", ".7z", ".rar"}:
        return "archive"
    if ext in {".csv", ".json", ".tsv", ".xlsx", ".xls"}:
        return "data"
    if ext in {".py", ".js", ".ts", ".r", ".m", ".sh", ".ipynb", ".java", ".cpp", ".c"}:
        return "code"
    if ext in {".bib", ".tex", ".md", ".txt", ".rst"}:
        return "supplement"
    return "file"


def ingest_non_pdf_file(article_id: str, file_path: Path, source_filename: str = "") -> dict:
    """Create a readable Markdown representation for supplementary/code uploads."""
    art_dir = _article_dir(article_id)
    ext = Path(source_filename or file_path).suffix.lower()
    title = Path(source_filename or file_path).stem.replace("_", " ") or article_id
    kind = material_kind_from_filename(source_filename or file_path)
    mime = mimetypes.guess_type(source_filename or str(file_path))[0] or ""

    body = ""
    if ext in TEXT_EXTS:
        try:
            body = Path(file_path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            body = Path(file_path).read_text(encoding="utf-8", errors="replace")

    if ext == ".md" and body:
        markdown = body
    elif body:
        lang = ext.lstrip(".") or "text"
        markdown = f"# {title}\n\n> 上传的补充材料 / 代码块。\n\n```{lang}\n{body}\n```\n"
    else:
        markdown = (
            f"# {title}\n\n"
            f"> 已上传文件 `{source_filename or Path(file_path).name}`。\n\n"
            f"- 类型: {mime or ext or 'unknown'}\n"
            f"- 该文件不是可直接文本化的格式，可右键打开所在文件夹查看原文件。\n"
        )

    md_path = art_dir / f"{article_id}.md"
    md_path.write_text(markdown.strip() + "\n", encoding="utf-8")
    meta = {
        "source": "upload",
        "source_filename": source_filename,
        "file_type": ext.lstrip("."),
        "mime_type": mime,
        "document_kind": kind,
        "title": title,
        "table_of_contents": [{"title": title}],
        "page_stats": [],
    }
    (art_dir / f"{article_id}_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {"title": title, "kind": kind, "meta": meta, "md_path": md_path}


def _json_from_llm(text: str) -> dict:
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, flags=re.I)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def extract_document_info(
    article_id: str,
    log_callback=None,
    provider_id: str = "",
    model: str = "",
    reason: str = "manual",
) -> dict:
    """Extract bibliographic/material metadata with LLM and persist it."""
    def log(msg: str) -> None:
        if log_callback:
            log_callback(msg)

    md_path = _preferred_markdown(article_id)
    if not md_path:
        raise FileNotFoundError("No markdown available for metadata extraction")

    art_dir = _article_dir(article_id)
    meta = {}
    meta_path = art_dir / f"{article_id}_meta.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    existing = storage.get_article(article_id) or {"id": article_id}
    text = md_path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = text[:32000]

    summary_context = ""
    summary_path = art_dir / f"{article_id}_summary.md"
    if summary_path.exists():
        try:
            summary_context = summary_path.read_text(encoding="utf-8", errors="replace")[:8000]
        except Exception:
            summary_context = ""

    prompt = f"""Extract and calibrate document metadata from the content below.

Trigger reason: {reason}

Return JSON only with these fields:
{{
  "title": "",
  "authors": [],
  "author": "",
  "doi": "",
  "year": "",
  "venue": "",
  "abstract": "",
  "keywords": [],
  "category": "",
  "tags": [],
  "document_kind": "paper|supplement|code|data|note|file",
  "is_paper": true
}}

Rules:
- Prefer exact title, author names, DOI, venue, and year from the document.
- Treat parser metadata, the first pages, references, and summary context as evidence; use the most specific consistent value.
- If this is supplementary material, code, data, or notes rather than a paper, set document_kind accordingly.
- Use short tags, maximum 8.
- Do not invent missing DOI or authors; leave unknown fields empty.

Existing record:
{json.dumps(existing, ensure_ascii=False)}

Parser metadata:
{json.dumps(meta, ensure_ascii=False)[:6000]}

Summary context:
{summary_context}

Content:
{text}"""
    log(f"Extracting document metadata with LLM ({reason})...")
    data = call_chat_completion(
        [{"role": "user", "content": prompt}],
        provider_id=provider_id,
        model=model,
        temperature=0.1,
        max_tokens=2048,
        timeout=240,
    )
    choices = data.get("choices") or []
    if not choices:
        raise ValueError("LLM returned no choices")
    raw = choices[0].get("message", {}).get("content") or ""
    info = _json_from_llm(raw)
    if not isinstance(info, dict):
        raise ValueError("LLM did not return a JSON object")

    if isinstance(info.get("authors"), str):
        info["authors"] = [a.strip() for a in re.split(r",|;| and ", info["authors"]) if a.strip()]
    elif isinstance(info.get("authors"), list):
        authors: list[str] = []
        for author in info["authors"]:
            if isinstance(author, dict):
                name = author.get("name") or author.get("full_name") or author.get("author") or ""
            else:
                name = str(author)
            name = name.strip()
            if name:
                authors.append(name)
        info["authors"] = authors
    else:
        info["authors"] = []
    if not info.get("author") and info.get("authors"):
        info["author"] = ", ".join(info["authors"][:4])
    if isinstance(info.get("keywords"), str):
        info["keywords"] = [t.strip() for t in re.split(r",|;|\n", info["keywords"]) if t.strip()]
    if isinstance(info.get("tags"), str):
        info["tags"] = [t.strip() for t in re.split(r",|;|\n", info["tags"]) if t.strip()]

    info["extracted_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    info["extraction_reason"] = reason
    (art_dir / f"{article_id}_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    update = {
        "metadata_extracted": True,
        "metadata_extracted_at": info["extracted_at"],
        "metadata_source": reason,
    }
    for key in ("title", "author", "doi", "year", "venue", "abstract", "category"):
        if info.get(key):
            update[key] = info[key]
    if info.get("authors"):
        update["authors"] = info["authors"]
    if info.get("document_kind"):
        update["kind"] = info["document_kind"]
    tags = info.get("tags") or info.get("keywords") or []
    if tags:
        update["tags"] = tags[:8]
    storage.update_article_fields(article_id, update)
    if tags:
        storage.replace_article_tags(article_id, tags[:8])
    log("Metadata extraction complete")
    return {"info": info, "updates": update}
