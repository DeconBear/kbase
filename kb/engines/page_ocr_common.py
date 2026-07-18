"""Shared helpers for page-by-page OCR engines (range, checkpoint, progress)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from engines._paths import ARTICLES_DIR

PAGE_ENGINES = frozenset({"ocr", "vision", "llm_vision"})


class ConversionCancelled(Exception):
    """Raised when the user cancels a page-OCR job mid-run."""


def article_dir(article_id: str) -> Path:
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
    tmp = path.with_suffix(".tmp")
    payload = dict(data)
    payload["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


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
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
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
) -> str:
    parts: list[str] = []
    for n in range(page_from, page_to + 1):
        text = read_page_md(article_id, n)
        if text is None:
            text = f"<!-- missing page {n} -->\n"
        parts.append(text.strip())
    header = f"<!-- kbase-ocr-range: {page_from}-{page_to} / {pdf_total} -->\n\n"
    body = "\n\n---\n\n".join(parts).strip()
    return header + body + "\n"


def publish_stitched(
    article_id: str,
    pdf_path: str,
    page_from: int,
    page_to: int,
    pdf_total: int,
) -> Path:
    from workspace_paths import publish_engine_markdown

    final = stitch_pages(article_id, page_from, page_to, pdf_total=pdf_total)
    adir = article_dir(article_id)
    adir.mkdir(parents=True, exist_ok=True)
    return publish_engine_markdown(adir, article_id, pdf_path, md_text=final)


ProgressCb = Callable[[int, int, int], None]  # done, total_in_range, current_page
CancelCb = Callable[[], bool]


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
) -> tuple[str, int, int]:
    """Process pages with checkpoint/cancel/progress.

    Returns ``(status, page_from, page_to)`` where status is
    ``done`` | ``error``. Cancellation raises ``ConversionCancelled``.
    """
    start, end = resolve_page_range(total_pages, page_from, page_to)
    range_total = end - start + 1
    next_page = start

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
            log(f"Resuming from page {next_page} (range {start}-{end})")
        else:
            log("ERROR: checkpoint engine/range mismatch; refuse resume")
            return "error", start, end
    else:
        clear_checkpoint(article_id, remove_pages=True)

    save_checkpoint(
        article_id,
        {
            "engine": engine,
            "page_from": start,
            "page_to": end,
            "pdf_total": total_pages,
            "next_page": next_page,
            "done": max(0, next_page - start),
            "total": range_total,
            "status": "running",
        },
    )
    if progress_callback:
        progress_callback(max(0, next_page - start), range_total, max(start, next_page - 1))

    for page_num in range(next_page, end + 1):
        if should_cancel and should_cancel():
            save_checkpoint(
                article_id,
                {
                    "engine": engine,
                    "page_from": start,
                    "page_to": end,
                    "pdf_total": total_pages,
                    "next_page": page_num,
                    "done": page_num - start,
                    "total": range_total,
                    "status": "cancelled",
                },
            )
            log(f"Cancelled before page {page_num}")
            raise ConversionCancelled(f"cancelled at page {page_num}")

        done_before = page_num - start
        log(f"OCR page {page_num}/{end} (range {start}-{end}, pdf {total_pages})...")
        if progress_callback:
            progress_callback(done_before, range_total, page_num)

        text = process_page(page_num)
        if not (text or "").strip():
            text = f"<!-- empty page {page_num} -->"
        write_page_md(article_id, page_num, text)

        done = page_num - start + 1
        save_checkpoint(
            article_id,
            {
                "engine": engine,
                "page_from": start,
                "page_to": end,
                "pdf_total": total_pages,
                "next_page": page_num + 1,
                "done": done,
                "total": range_total,
                "status": "running",
            },
        )
        if progress_callback:
            progress_callback(done, range_total, page_num)

    return "done", start, end
