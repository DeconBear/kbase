"""Shared helpers for page-by-page OCR engines (range, checkpoint, progress)."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

from engines._paths import ARTICLES_DIR

PAGE_ENGINES = frozenset({"ocr", "vision", "llm_vision"})

# Rough seconds/page for ETA (refined live from elapsed/done when possible).
ETA_SEC_PER_PAGE = {
    "ocr": 8.0,
    "vision": 12.0,
    "llm_vision": 15.0,
}


class ConversionCancelled(Exception):
    """Raised when the user cancels a page-OCR job mid-run."""


def article_dir(article_id: str) -> Path:
    try:
        from storage import resolve_article_dir

        return resolve_article_dir(article_id)
    except Exception:
        return ARTICLES_DIR / article_id


def pages_dir(article_id: str) -> Path:
    return article_dir(article_id) / f"{article_id}_pages"


def checkpoint_path(article_id: str) -> Path:
    return article_dir(article_id) / f"{article_id}_page_ocr.json"


def resolve_page_range(
    total_pages: int,
    page_from: int | None = None,
    page_to: int | None = None,
) -> tuple[int, int]:
    """Return inclusive 1-based (from, to) clamped to the PDF."""
    if total_pages < 1:
        raise ValueError("PDF has no pages")
    start = int(page_from) if page_from is not None else 1
    end = int(page_to) if page_to is not None else total_pages
    start = max(1, min(start, total_pages))
    end = max(1, min(end, total_pages))
    if start > end:
        start, end = end, start
    return start, end


def estimate_seconds(engine: str, page_count: int, *, sec_per_page: float | None = None) -> int:
    if page_count <= 0:
        return 0
    spp = sec_per_page if sec_per_page is not None else ETA_SEC_PER_PAGE.get(engine, 10.0)
    return max(1, int(round(page_count * float(spp))))


def format_eta(seconds: int | float | None) -> str:
    if seconds is None:
        return ""
    s = max(0, int(seconds))
    if s < 60:
        return f"约 {s} 秒"
    m, rem = divmod(s, 60)
    if m < 60:
        return f"约 {m} 分 {rem} 秒" if rem else f"约 {m} 分钟"
    h, m = divmod(m, 60)
    return f"约 {h} 小时 {m} 分"


def clean_page_markdown(text: str, page_num: int) -> str:
    """Normalize OCR/text-layer page Markdown for reading."""
    t = (text or "").strip()
    if not t:
        return f"<!-- kbase-page: {page_num} -->\n\n_（空页）_\n"

    # Unwrap a single fenced block wrapping the whole page (common Qwen habit).
    fence = re.match(r"^```(?:markdown|md)?\s*\n([\s\S]*?)\n```\s*$", t, re.IGNORECASE)
    if fence:
        t = fence.group(1).strip()
    else:
        # Strip leading/trailing fence lines if present.
        lines = t.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        t = "\n".join(lines).strip()

    # Fix odd heading artifacts like ###{Title}
    t = re.sub(r"^(#{1,6})\{+\s*", r"\1 ", t, flags=re.MULTILINE)
    t = re.sub(r"\}+\s*$", "", t, flags=re.MULTILINE)

    if t.startswith(f"<!-- kbase-page: {page_num} -->"):
        return t if t.endswith("\n") else t + "\n"
    return f"<!-- kbase-page: {page_num} -->\n\n{t}\n"


def load_checkpoint(article_id: str) -> dict[str, Any] | None:
    path = checkpoint_path(article_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save_checkpoint(article_id: str, data: dict[str, Any]) -> None:
    path = checkpoint_path(article_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(data)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    # Cloud-synced drives (WPS/Baidu) often deny os.replace briefly — retry, then
    # fall back to a direct write so a flaky FS never aborts a long OCR job.
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    last_err: Exception | None = None
    for attempt in range(6):
        try:
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(path)
            return
        except OSError as exc:
            last_err = exc
            time.sleep(0.15 * (attempt + 1))
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
    try:
        path.write_text(text, encoding="utf-8")
    except OSError:
        if last_err:
            raise last_err
        raise


def clear_checkpoint(article_id: str, *, remove_pages: bool = False) -> None:
    ck = checkpoint_path(article_id)
    try:
        if ck.exists():
            ck.unlink()
    except OSError:
        pass
    if remove_pages:
        pdir = pages_dir(article_id)
        if pdir.is_dir():
            for child in pdir.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                pdir.rmdir()
            except OSError:
                pass


def write_page_md(article_id: str, page_num: int, text: str) -> Path:
    pdir = pages_dir(article_id)
    pdir.mkdir(parents=True, exist_ok=True)
    path = pdir / f"{page_num:04d}.md"
    cleaned = clean_page_markdown(text, page_num)
    path.write_text(cleaned if cleaned.endswith("\n") else cleaned + "\n", encoding="utf-8")
    return path


def read_page_md(article_id: str, page_num: int) -> str | None:
    path = pages_dir(article_id) / f"{page_num:04d}.md"
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def stitch_pages(
    article_id: str,
    page_from: int,
    page_to: int,
    *,
    pdf_total: int,
    partial: bool = False,
) -> str:
    parts: list[str] = []
    for n in range(page_from, page_to + 1):
        text = read_page_md(article_id, n)
        if text is None:
            text = f"<!-- kbase-page: {n} -->\n\n<!-- missing page {n} -->\n"
        parts.append(text.strip())
    flag = "partial" if partial else "done"
    header = (
        f"<!-- kbase-ocr-range: {page_from}-{page_to} / {pdf_total} · {flag} -->\n\n"
    )
    body = "\n\n---\n\n".join(parts).strip()
    return header + body + "\n"


def publish_stitched(
    article_id: str,
    pdf_path: str,
    page_from: int,
    page_to: int,
    pdf_total: int,
    *,
    partial: bool = False,
) -> Path:
    from workspace_paths import publish_engine_markdown

    final = stitch_pages(
        article_id, page_from, page_to, pdf_total=pdf_total, partial=partial,
    )
    adir = article_dir(article_id)
    adir.mkdir(parents=True, exist_ok=True)
    return publish_engine_markdown(adir, article_id, pdf_path, md_text=final)


ProgressCb = Callable[..., None]  # (done, total_in_range, current_page, extras=dict)
CancelCb = Callable[[], bool]
PublishPartialCb = Callable[[int, int], None]  # (page_from, page_to_done)


def run_page_loop(
    *,
    article_id: str,
    engine: str,
    total_pages: int,
    page_from: int | None,
    page_to: int | None,
    resume: bool,
    should_cancel: CancelCb | None,
    progress_callback: ProgressCb | None,
    process_page: Callable[[int], str],
    log: Callable[[str], None],
    publish_partial: PublishPartialCb | None = None,
    usage_state: dict[str, Any] | None = None,
) -> tuple[str, int, int]:
    """Process pages with checkpoint/cancel/progress/partial publish.

    Returns ``(status, page_from, page_to)`` where status is
    ``done`` | ``error``. Cancellation raises ``ConversionCancelled``.
    """
    start, end = resolve_page_range(total_pages, page_from, page_to)
    range_total = end - start + 1
    next_page = start
    usage = usage_state if usage_state is not None else {}
    t0 = time.time()

    if resume:
        ck = load_checkpoint(article_id)
        if not ck:
            log("WARN: resume requested but no checkpoint; starting from page_from")
        elif (
            ck.get("engine") == engine
            and int(ck.get("page_from") or 0) == start
            and int(ck.get("page_to") or 0) == end
        ):
            next_page = max(start, int(ck.get("next_page") or start))
            for key in (
                "prompt_tokens", "completion_tokens", "total_tokens",
                "pages_text", "pages_ocr",
            ):
                if key in ck:
                    try:
                        usage[key] = int(ck.get(key) or 0)
                    except (TypeError, ValueError):
                        pass
            log(f"Resuming from page {next_page} (range {start}-{end})")
        else:
            log("ERROR: checkpoint engine/range mismatch; refuse resume")
            return "error", start, end
    else:
        clear_checkpoint(article_id, remove_pages=True)

    def _ckpt(status: str, next_p: int, done: int) -> dict[str, Any]:
        elapsed = max(0.001, time.time() - t0)
        spp = (elapsed / done) if done else ETA_SEC_PER_PAGE.get(engine, 8.0)
        remaining = max(0, range_total - done)
        payload = {
            "engine": engine,
            "page_from": start,
            "page_to": end,
            "pdf_total": total_pages,
            "next_page": next_p,
            "done": done,
            "total": range_total,
            "status": status,
            "eta_seconds": estimate_seconds(engine, remaining, sec_per_page=spp),
            "sec_per_page": round(float(spp), 2),
            "partial": True,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0),
            "total_tokens": int(usage.get("total_tokens") or 0),
            "pages_text": int(usage.get("pages_text") or 0),
            "pages_ocr": int(usage.get("pages_ocr") or 0),
        }
        save_checkpoint(article_id, payload)
        return payload

    def _emit(done: int, page: int) -> None:
        extras = _ckpt("running", page + 1 if done else page, done)
        if progress_callback:
            try:
                progress_callback(done, range_total, page, extras)
            except TypeError:
                progress_callback(done, range_total, page)

    _emit(max(0, next_page - start), max(start, next_page - 1))

    for page_num in range(next_page, end + 1):
        if should_cancel and should_cancel():
            _ckpt("cancelled", page_num, page_num - start)
            log(f"Cancelled before page {page_num}")
            raise ConversionCancelled(f"cancelled at page {page_num}")

        done_before = page_num - start
        log(f"OCR page {page_num}/{end} (range {start}-{end}, pdf {total_pages})...")
        _emit(done_before, page_num)

        text = process_page(page_num)
        if not (text or "").strip():
            text = f"<!-- empty page {page_num} -->"
        write_page_md(article_id, page_num, text)

        done = page_num - start + 1
        extras = _ckpt("running", page_num + 1, done)

        if publish_partial:
            try:
                publish_partial(start, page_num)
                extras["partial_published"] = True
            except Exception as exc:  # noqa: BLE001
                log(f"WARN: partial publish failed at page {page_num}: {exc}")

        if progress_callback:
            try:
                progress_callback(done, range_total, page_num, extras)
            except TypeError:
                progress_callback(done, range_total, page_num)

    return "done", start, end
