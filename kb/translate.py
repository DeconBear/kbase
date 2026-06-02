"""Background markdown translation for articles."""
import json
import re
import shutil
import time
from pathlib import Path

from llm_config import call_chat_completion

DIR = Path(__file__).parent.absolute()
ARTICLES_DIR = DIR / "articles"


def _clean_llm_markdown(text):
    text = (text or "").strip()
    fence = re.search(r"```(?:markdown|md)?\s*\n([\s\S]*?)\n?```", text, flags=re.I)
    if fence:
        text = fence.group(1).strip()
    text = re.sub(r"^\s*(?:译文|翻译|Translation)\s*[:：]\s*", "", text, flags=re.I)
    return text.strip()


def _split_large_text(text, max_chars):
    parts = []
    cur = ""
    for piece in re.split(r"(\n{2,})", text):
        if len(cur) + len(piece) > max_chars and cur:
            parts.append(cur)
            cur = piece
        else:
            cur += piece
    if cur:
        parts.append(cur)

    result = []
    for part in parts:
        if len(part) <= max_chars:
            result.append(part)
            continue
        for start in range(0, len(part), max_chars):
            result.append(part[start:start + max_chars])
    return [p for p in result if p]


def chunk_markdown(md_text, max_chars=4500):
    """Split markdown conservatively while preserving every source character."""
    sections = re.split(r"(?=^#{1,3}\s)", md_text, flags=re.M)
    chunks = []
    cur = ""
    for section in sections:
        if not section:
            continue
        if len(section) > max_chars:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.extend(_split_large_text(section, max_chars))
            continue
        if len(cur) + len(section) > max_chars and cur:
            chunks.append(cur)
            cur = section
        else:
            cur += section
    if cur:
        chunks.append(cur)
    return chunks or [md_text]


def _state_path(article_id):
    return ARTICLES_DIR / article_id / f"{article_id}_translation_state.json"


def write_state(article_id, **updates):
    path = _state_path(article_id)
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            state = {}
    state.update(updates)
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def _translate_chunk(chunk, index, total, title_hint="", retries=2, old_md_text="", target_language="Simplified Chinese", extra_prompt=""):
    # Dynamically adjust completeness thresholds based on target language.
    # Chinese and similar languages are inherently more compact than English,
    # so legitimate translations can be 30-40% the character count of the source.
    _is_cjk = any(tag in target_language.lower() for tag in (
        "chinese", "中文", "japanese", "日本語", "korean", "한국어",
    ))
    _min_ratio = 0.18 if _is_cjk else 0.35   # first pass: suspicious-too-short
    _hard_ratio = 0.10 if _is_cjk else 0.18  # second pass: definitely truncated

    if old_md_text:
        prompt = f"""This document was previously translated, but the original text has been slightly modified or re-parsed by a different OCR engine.
Please translate the following new Markdown chunk into {target_language}.

You are provided with the old full translated text for reference.
**CRITICAL**: You must reuse the translation style, terminology, and matching translated sentences from the old translation wherever the original meaning is similar. Only translate from scratch the parts that are genuinely new or changed.

Old Translation Reference:
<old_translation>
{old_md_text[:10000]}
</old_translation>

Now, translate this specific new chunk:
"""
    else:
        prompt = f"""Translate this markdown chunk from an academic paper into {target_language}."""

    prompt += f"""
You must translate the entire chunk. Do not summarize, skip, merge away, or replace content with placeholders.

Rules:
- Preserve all Markdown structure: headings, lists, tables, blockquotes, images, links, citations, and reference numbers.
- Preserve LaTeX exactly with its original delimiters ($...$ or $$...$$). Do not translate inside LaTeX.
- Keep URLs, image paths, DOI strings, and citation keys unchanged.
- Translate prose and table text to {target_language}. If text is already in the target language, keep it natural.
- Output only translated Markdown. No code fences, no commentary, no [NEXT] or [DONE] tags."""

    if extra_prompt:
        prompt += f"\n\nAdditional Instructions from User:\n{extra_prompt}"

    prompt += f"""

Chunk {index + 1}/{total}
{title_hint}

Markdown:
{chunk}"""

    def _is_truncated(source, output, min_ratio):
        """Check if output appears truncated relative to source."""
        if not output.strip():
            return True
        if re.search(r"\[(?:NEXT|DONE|FIX)\b", output, flags=re.I):
            return True
        # Compare non-whitespace character counts
        compact_source = re.sub(r"\s+", "", source)
        compact_output = re.sub(r"\s+", "", output)
        if len(compact_source) > 900 and len(compact_output) < len(compact_source) * min_ratio:
            return True
        return False

    last_error = None
    last_translation = ""
    for attempt in range(retries + 1):
        try:
            data = call_chat_completion(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=8192,
                timeout=300,
            )
            translated = _clean_llm_markdown(data["choices"][0]["message"]["content"])
            last_translation = translated

            # First pass: check with generous threshold
            if _is_truncated(chunk, translated, _min_ratio):
                if attempt < retries:
                    prompt += "\n\nYour previous output looked incomplete. Translate every paragraph and do not emit control tags."
                    continue
                return f"{translated}\n\n> [翻译可能不完整，以下保留原文]\n\n{chunk}".strip()

            # Second pass: hard check for severely truncated output
            if len(chunk) > 800 and len(translated) < len(chunk) * _hard_ratio and attempt < retries:
                prompt += "\n\nYour previous output was too short. Translate every line and preserve all content."
                continue

            return translated or chunk
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    if last_translation:
        return f"{last_translation}\n\n> [翻译可能不完整，以下保留原文]\n\n{chunk}".strip()
    return f"> [翻译失败，保留原文: {last_error}]\n\n{chunk}"


def translate_article(article_id, mode="update", target_language="Simplified Chinese", extra_prompt="", log_callback=None):
    def log(message):
        if log_callback:
            log_callback(message)

    art_dir = ARTICLES_DIR / article_id
    md_path = art_dir / f"{article_id}_calibrated.md"
    if not md_path.exists():
        md_path = art_dir / f"{article_id}.md"
    if not md_path.exists():
        write_state(article_id, status="error", message="No markdown to translate")
        return False

    md_text = md_path.read_text(encoding="utf-8")
    
    old_md_text = ""
    if mode == "update":
        # 优先使用当前的译文作为修复上下文
        current_trans_path = art_dir / f"{article_id}_translated.md"
        old_trans_path = art_dir / f"{article_id}_translated_old.md"
        if current_trans_path.exists():
            old_md_text = current_trans_path.read_text(encoding="utf-8")
        elif old_trans_path.exists():
            old_md_text = old_trans_path.read_text(encoding="utf-8")
        
    chunks = chunk_markdown(md_text)
    total = len(chunks)
    translated_chunks = []

    write_state(
        article_id,
        status="running",
        message="翻译已开始",
        total=total,
        done=0,
        percent=0,
        started_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    log(f"Translation started: {article_id}, {total} chunks")

    for idx, chunk in enumerate(chunks):
        heading = ""
        match = re.search(r"^#{1,3}\s+(.+)", chunk, flags=re.M)
        if match:
            heading = f"Current heading: {match.group(1).strip()}"
        write_state(
            article_id,
            status="running",
            message=f"正在翻译 {idx + 1}/{total}",
            total=total,
            done=idx,
            percent=round(idx / max(total, 1) * 100, 1),
            current=idx + 1,
        )
        log(f"Chunk {idx + 1}/{total}")
        translated_chunks.append(_translate_chunk(
            chunk, idx, total, heading, 
            old_md_text=old_md_text, 
            target_language=target_language, 
            extra_prompt=extra_prompt
        ))

    translated_md = "\n\n".join(translated_chunks).strip() + "\n"
    out_path = art_dir / f"{article_id}_translated.md"
    if out_path.exists():
        backup_path = art_dir / f"{article_id}_translated_old.md"
        try:
            shutil.copy2(str(out_path), str(backup_path))
        except Exception:
            pass
    out_path.write_text(translated_md, encoding="utf-8")
    write_state(
        article_id,
        status="done",
        message="翻译完成",
        total=total,
        done=total,
        percent=100,
        output=out_path.name,
        completed_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )
    log(f"Translation done: {out_path.name}, {len(translated_md)} chars")
    return True
