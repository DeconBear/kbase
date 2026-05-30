"""LLM + PyMuPDF calibration — chunk-based, no fragile protocol."""
import re
from pathlib import Path

from llm_config import call_chat_completion

CALIBRATION_TAG_RE = re.compile(
    r"\[\s*/?\s*(?:FIX|FIXED|CORRECTED|CORRECTION|REWRITE|REWRITTEN|UNCHANGED|NO\s*CHANGE|KEEP|KEEP\s*ORIGINAL)\b[^\]\n]*\]",
    flags=re.IGNORECASE,
)
CALIBRATION_LINE_LABEL_RE = re.compile(
    r"(?im)^\s*(?:FIX|FIXED|CORRECTED|CORRECTION|REWRITE|REWRITTEN|UNCHANGED|NO\s*CHANGE|KEEP|KEEP\s*ORIGINAL)\b[^\n:：-]{0,60}[-:：]\s*"
)


def _llm_chat(messages, temperature=0.3, max_tokens=8192):
    data = call_chat_completion(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=300,
    )
    return data["choices"][0]["message"]["content"]


def _extract_pymupdf(pdf_path):
    import fitz
    doc = fitz.open(pdf_path)
    pages = []
    for i in range(doc.page_count):
        text = doc[i].get_text()
        pages.append(f"[Page {i + 1}]\n{text}")
    doc.close()
    return pages


def _chunk_md(md_text, max_chars=10000):
    """Split markdown into chunks at header boundaries, each <= max_chars."""
    sections = re.split(r"(?=^#{1,3}\s)", md_text, flags=re.MULTILINE)
    chunks = []
    cur = ""
    for s in sections:
        if len(cur) + len(s) > max_chars and cur:
            chunks.append(cur.strip())
            cur = s
        else:
            cur += s
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def _clean_llm_markdown(text):
    """Remove wrapper fences and edit markers without touching citations."""
    text = text.strip()
    fence = re.fullmatch(
        r"```(?:markdown|md)?\s*\n([\s\S]*?)\n?```",
        text,
        flags=re.IGNORECASE,
    )
    if fence:
        text = fence.group(1).strip()

    text = CALIBRATION_TAG_RE.sub("", text)
    text = CALIBRATION_LINE_LABEL_RE.sub("", text)
    text = re.sub(
        r"</?\s*(?:fix|fixed|corrected|correction|rewrite|rewritten|unchanged|keep)(?:\s+[^>]*)?>",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def calibrate(article_id, log_callback=None):
    def log(msg):
        if log_callback:
            log_callback(msg)

    art_dir = Path(__file__).parent / "articles" / article_id
    md_path = art_dir / f"{article_id}.md"
    pdf_path = art_dir / "original.pdf"

    if not md_path.exists():
        log("ERROR: No markdown to calibrate")
        return False
    if not pdf_path.exists():
        log("ERROR: No PDF to cross-reference")
        return False

    md_text = md_path.read_text(encoding="utf-8")
    log(f"Calibrating {article_id} ({len(md_text)} chars)")

    # Phase 1: Extract PyMuPDF once
    log("Extracting PDF reference text...")
    try:
        pymupdf_pages = _extract_pymupdf(str(pdf_path))
        pymupdf_full = "\n\n".join(pymupdf_pages)
        log(f"  {len(pymupdf_pages)} pages, {len(pymupdf_full)} chars")
    except Exception as e:
        log(f"PyMuPDF error: {e}")
        return False

    # Phase 2: Chunk markdown
    chunks = _chunk_md(md_text, 8000)
    md_total = len(md_text)
    log(f"Split into {len(chunks)} chunks for correction")

    # Phase 3: Correct each chunk
    corrected = []
    char_offset = 0
    for ci, chunk in enumerate(chunks):
        log(f"  Chunk {ci + 1}/{len(chunks)} ({len(chunk)} chars)...")
        chunk_ratio_start = char_offset / max(md_total, 1)
        chunk_ratio_end = (char_offset + len(chunk)) / max(md_total, 1)
        page_start = max(1, int(len(pymupdf_pages) * chunk_ratio_start))
        page_end = min(len(pymupdf_pages), int(len(pymupdf_pages) * chunk_ratio_end) + 1)
        ref_pages = pymupdf_pages[page_start - 1:page_end]
        ref_text = "\n\n".join(ref_pages)[:6000]

        prompt = f"""Review this parsed markdown section. Fix ONLY clear errors (garbled text, broken sentences, recognition artifacts).

**PARSED MARKDOWN** (primary — trust this structure):
{chunk}

**PyMuPDF REFERENCE** (secondary — unstructured, use only to verify suspected errors):
{ref_text}

Rules:
- Primary trust: the parsed markdown (from a specialized document parser)
- Secondary reference: PyMuPDF raw text (may have wrong order)
- ONLY fix what is clearly wrong — when unsure, keep the original
- Preserve ALL formatting, headers, LaTeX, images, URLs, citations
- Do NOT add edit markers or labels such as [FIX], [/FIX], [CORRECTED], [UNCHANGED], or similar

Output ONLY the corrected markdown. No explanations."""

        try:
            result = _llm_chat([{"role": "user", "content": prompt}])
        except Exception as e:
            log(f"    LLM error: {e}, keeping original")
            corrected.append(chunk)
            char_offset += len(chunk)
            continue

        result = _clean_llm_markdown(result)
        if len(result) < len(chunk) * 0.3:
            # LLM output suspiciously short — keep original
            log(f"    Output too short ({len(result)} vs {len(chunk)}), keeping original")
            corrected.append(chunk)
        else:
            corrected.append(result)
            log(f"    ✓ Done ({len(result)} chars)")

        char_offset += len(chunk)

    # Save
    calibrated_md = "\n\n".join(corrected)
    out_path = art_dir / f"{article_id}_calibrated.md"
    out_path.write_text(calibrated_md, encoding="utf-8")
    log(f"Saved: {out_path.name} ({len(calibrated_md)} chars)")
    return True
