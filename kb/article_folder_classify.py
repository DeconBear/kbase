"""Auto-classify articles into article folders by year / venue / category / tag."""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Callable, TypeVar

import storage

T = TypeVar("T")


def _db_retry(fn: Callable[[], T], *, retries: int = 8) -> T:
    """Retry briefly when SQLite is locked by a concurrent scan."""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last = exc
            if "locked" not in str(exc).lower() or attempt == retries - 1:
                raise
            time.sleep(0.15 * (attempt + 1))
    assert last is not None
    raise last

FOLDER_AUTO_MODES = frozenset({"off", "year", "venue", "category", "first_tag"})
DEFAULT_MODE = "off"


def normalize_mode(value: str | None) -> str:
    mode = (value or DEFAULT_MODE).strip().lower()
    return mode if mode in FOLDER_AUTO_MODES else DEFAULT_MODE


def sanitize_folder_name(name: str, *, fallback: str = "未分类") -> str:
    raw = re.sub(r"[\r\n\t]+", " ", str(name or "")).strip()
    raw = re.sub(r"[\\/:*?\"<>|]+", "·", raw)
    raw = re.sub(r"\s+", " ", raw).strip(" .")
    if not raw:
        return fallback
    return raw[:80]


def folder_name_for_article(article: dict[str, Any], mode: str) -> str | None:
    """Return target folder name for *mode*, or None if unclassified."""
    mode = normalize_mode(mode)
    if mode == "off":
        return None
    if mode == "year":
        year = str(article.get("year") or "").strip()
        m = re.search(r"(19|20)\d{2}", year)
        return m.group(0) if m else None
    if mode == "venue":
        venue = str(article.get("venue") or "").strip()
        return sanitize_folder_name(venue) if venue else None
    if mode == "category":
        cat = str(article.get("category") or "").strip()
        return sanitize_folder_name(cat) if cat else None
    if mode == "first_tag":
        tags = article.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
        if not tags:
            return None
        return sanitize_folder_name(str(tags[0]))
    return None


def ensure_root_folder(name: str) -> dict[str, Any]:
    """Find or create a top-level article folder with *name*."""
    name = sanitize_folder_name(name)

    def _run() -> dict[str, Any]:
        for folder in storage.list_article_folders():
            if (folder.get("parent_id") or None) is None and folder.get("name") == name:
                return folder
        return storage.create_article_folder(name=name, parent_id=None)

    return _db_retry(_run)


def classify_article(
    article_id: str,
    mode: str,
    *,
    only_uncategorized: bool = True,
) -> dict[str, Any]:
    """Move one article into the folder implied by *mode*.

    Returns ``{moved, folder_id, folder_name, skipped, reason}``.
    """
    mode = normalize_mode(mode)
    article = storage.get_article(article_id)
    if not article:
        return {"moved": False, "skipped": True, "reason": "missing"}
    if mode == "off":
        return {"moved": False, "skipped": True, "reason": "mode_off"}
    if only_uncategorized and article.get("folder_id"):
        return {
            "moved": False,
            "skipped": True,
            "reason": "already_classified",
            "folder_id": article.get("folder_id"),
        }
    folder_name = folder_name_for_article(article, mode)
    if not folder_name:
        return {"moved": False, "skipped": True, "reason": "no_field"}
    folder = ensure_root_folder(folder_name)
    _db_retry(lambda: storage.move_article_to_folder(article_id, folder["id"]))
    return {
        "moved": True,
        "skipped": False,
        "folder_id": folder["id"],
        "folder_name": folder["name"],
        "reason": "ok",
    }


def classify_all_articles(
    mode: str,
    *,
    only_uncategorized: bool = True,
    article_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Batch classify. Returns summary counts + per-folder moves."""
    mode = normalize_mode(mode)
    if mode == "off":
        return {
            "mode": mode,
            "moved": 0,
            "skipped": 0,
            "total": 0,
            "folders": [],
            "message": "自动分类已关闭",
        }
    articles = storage.get_all_articles()
    if article_ids is not None:
        want = set(article_ids)
        articles = [a for a in articles if a.get("id") in want]
    moved = 0
    skipped = 0
    by_folder: dict[str, int] = {}
    for article in articles:
        result = classify_article(
            article["id"], mode, only_uncategorized=only_uncategorized,
        )
        if result.get("moved"):
            moved += 1
            name = result.get("folder_name") or "?"
            by_folder[name] = by_folder.get(name, 0) + 1
        else:
            skipped += 1
    return {
        "mode": mode,
        "moved": moved,
        "skipped": skipped,
        "total": len(articles),
        "folders": sorted(by_folder.items(), key=lambda x: (-x[1], x[0])),
        "message": f"已归类 {moved} 篇，跳过 {skipped} 篇",
    }


def resolve_article_id_for_path(rel_path: str) -> dict[str, Any] | None:
    """Map a workspace-relative path to an article record if possible."""
    from literature_organize import article_id_from_rel

    rel = str(rel_path or "").replace("\\", "/").strip("/")
    if not rel:
        return None

    # 1) Already under literature/<id>/...
    try:
        from workspace import get_active_workspace

        ws = get_active_workspace()
        lit = ws.literature_dir_name() if ws else "literature"
    except Exception:
        lit = "literature"
    aid = article_id_from_rel(rel, lit) or article_id_from_rel(rel, "articles")
    if aid:
        art = storage.get_article(aid)
        if art:
            return art
        # Folder exists even if SQLite lag — return stub-like dict
        return {"id": aid, "title": aid}

    basename = rel.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    basename_l = basename.lower()
    stem_l = stem.lower()

    articles = storage.get_all_articles()

    # 2) Exact source_filename match (skip useless original.pdf)
    if basename_l not in {"original.pdf", "original.md"}:
        for art in articles:
            src = str(art.get("source_filename") or "").replace("\\", "/")
            src_base = src.rsplit("/", 1)[-1].lower()
            if src_base == basename_l:
                return art

    # 3) Article id equals path stem / contained in stem
    for art in articles:
        aid = str(art.get("id") or "")
        if not aid:
            continue
        if aid.lower() == stem_l or aid.lower() in stem_l or stem_l in aid.lower():
            return art

    # 4) Title stem fuzzy (normalized)
    def _norm(s: str) -> str:
        s = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (s or "").lower())
        return s

    stem_n = _norm(stem)
    if len(stem_n) >= 8:
        for art in articles:
            title_n = _norm(str(art.get("title") or ""))
            if title_n and (stem_n in title_n or title_n in stem_n):
                return art

    return None
