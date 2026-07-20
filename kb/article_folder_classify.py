"""Auto-classify articles into article folders (year / venue / topic / …)."""
from __future__ import annotations

import re
import sqlite3
import time
from typing import Any, Callable, TypeVar

import storage

T = TypeVar("T")

FOLDER_AUTO_MODES = frozenset({
    "off", "year", "venue", "category", "first_tag", "topic",
})
DEFAULT_MODE = "off"

# Ordered rules: first match wins. Keywords matched against tags/category/title/abstract.
TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("量子存储", (
        "quantum memory", "photon echo", "atomic frequency comb", "afc",
        "spin-wave", "spin wave", "optical memory", "quantum storage",
        "光存储", "量子存储", "echo",
    )),
    ("稀土离子", (
        "rare-earth", "rare earth", "erbium", "thulium", "praseodymium",
        "europium", "ytterbium", "tm:yag", "er:", "pr:", "稀土",
    )),
    ("金刚石NV", (
        "diamond", "nv center", "nv色心", "nitrogen-vacancy", "colour centre",
        "color center", "色心",
    )),
    ("量子网络", (
        "quantum repeater", "quantum internet", "quantum network",
        "entanglement distribution", "quantum node", "量子中继", "量子网络",
    )),
    ("量子计算", (
        "quantum computing", "quantum computer", "qubit", "quantum gate",
        "quantum control", "量子计算", "量子比特",
    )),
    ("量子光学", (
        "quantum optics", "cavity qed", "single photon", "photon source",
        "量子光学",
    )),
    ("光谱与相干", (
        "spectroscopy", "stark", "hyperfine", "coherence", "raman",
        "free induction", "hole-burning", "光谱",
    )),
    ("因果推断", (
        "causal inference", "causal", "causality", "因果",
    )),
    ("机器学习", (
        "machine learning", "deep learning", "reinforcement learning",
        "neural network", "llm", "large language", "bayesian optimization",
        "机器学习", "深度学习",
    )),
    ("材料与器件", (
        "materials", "photonics", "integrated optic", "solid-state",
        "semiconductor", "材料",
    )),
    ("量子信息总论", (
        "quantum information", "quant-ph", "quantum physics", "quantum",
        "量子信息", "量子物理",
    )),
]


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


def _year_from_arxiv_stem(text: str) -> str | None:
    """Map arXiv-like ``YYMM…`` stems (e.g. 2508, 2203.12345) to 20YY."""
    m = re.match(r"^(?P<yy>\d{2})(?P<mm>\d{2})(?:[.\-_].*)?$", text.strip())
    if not m:
        return None
    yy, mm = int(m.group("yy")), int(m.group("mm"))
    if not (1 <= mm <= 12):
        return None
    year = 2000 + yy if yy <= 35 else 1900 + yy
    if 1950 <= year <= 2035:
        return str(year)
    return None


def _infer_year(article: dict[str, Any]) -> str | None:
    """Pick a 4-digit year from metadata, title, id, or source filename."""
    for key in ("year", "title", "id", "source_filename", "author", "venue"):
        text = str(article.get(key) or "").strip()
        if not text:
            continue
        matches = re.findall(r"(?:19|20)\d{2}", text)
        if matches:
            if key == "year":
                return matches[0]
            for y in reversed(matches):
                n = int(y)
                if 1950 <= n <= 2035:
                    return y
        if key in {"id", "source_filename", "title"}:
            stem = text.rsplit("/", 1)[-1]
            stem = stem.rsplit(".", 1)[0] if "." in stem else stem
            for token in re.split(r"[_\s]+", stem):
                y = _year_from_arxiv_stem(token)
                if y:
                    return y
            m = re.search(r"(?:^|[-_])0?(?P<yy>\d{2})(?:[-_])", stem)
            if m:
                yy = int(m.group("yy"))
                year = 2000 + yy if yy <= 35 else 1900 + yy
                if 1950 <= year <= 2035:
                    return str(year)
    return None


def _article_text_blob(article: dict[str, Any]) -> str:
    tags = article.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
    parts = [
        str(article.get("category") or ""),
        str(article.get("title") or ""),
        str(article.get("abstract") or "")[:800],
        str(article.get("venue") or ""),
        " ".join(str(t) for t in tags),
    ]
    return " ".join(parts).lower()


_CATEGORY_TOPIC_MAP = {
    "cs.lg": "机器学习",
    "cs.ai": "机器学习",
    "stat.ml": "机器学习",
    "computer science": "机器学习",
    "information science": "机器学习",
    "ai": "机器学习",
    "quant-ph": "量子信息总论",
    "physics": "光谱与相干",
    "physics.atom-ph": "光谱与相干",
    "cond-mat.mes-hall": "材料与器件",
    "optics": "量子光学",
    "quantum physics": "量子信息总论",
    "quantum information": "量子信息总论",
    "machine learning": "机器学习",
    "artificial intelligence": "机器学习",
    "causal inference": "因果推断",
    "photonics": "材料与器件",
    "materials science": "材料与器件",
    "spectroscopy": "光谱与相干",
    "quantum optics": "量子光学",
}

_NOISE_TOPICS = frozenset({
    "ai", "rf", "bsm", "aom", "empty", "physics", "bwt", "ce3+", "hsrl",
    "gr test", "hii regions", "020.1670", "computer science", "information science",
})


def infer_topic(article: dict[str, Any]) -> str:
    """Return a topic folder name for the article."""
    blob = _article_text_blob(article)
    for topic, keywords in TOPIC_RULES:
        for kw in keywords:
            if kw.lower() in blob:
                return topic
    cat = str(article.get("category") or "").strip()
    if cat:
        mapped = _CATEGORY_TOPIC_MAP.get(cat.lower())
        if mapped:
            return mapped
    tags = article.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
    for tag in tags:
        tag0 = str(tag).strip()
        if not tag0:
            continue
        for topic, keywords in TOPIC_RULES:
            for kw in keywords:
                if kw.lower() in tag0.lower() or tag0.lower() in kw.lower():
                    return topic
    # Keep the Zotero tree tidy: unmapped items land in 其他主题 / year.
    return "其他主题"


def folder_path_for_article(article: dict[str, Any], mode: str) -> list[str] | None:
    """Return nested folder path segments for *mode*, or None."""
    mode = normalize_mode(mode)
    if mode == "off":
        return None
    if mode == "year":
        year = _infer_year(article)
        return [year] if year else ["未标注年份"]
    if mode == "venue":
        venue = str(article.get("venue") or "").strip()
        return [sanitize_folder_name(venue)] if venue else None
    if mode == "category":
        cat = str(article.get("category") or "").strip()
        return [sanitize_folder_name(cat)] if cat else None
    if mode == "first_tag":
        tags = article.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in re.split(r"[,;]", tags) if t.strip()]
        if not tags:
            return None
        return [sanitize_folder_name(str(tags[0]))]
    if mode == "topic":
        topic = infer_topic(article)
        year = _infer_year(article) or "未标注年份"
        return [topic, year]
    return None


def folder_name_for_article(article: dict[str, Any], mode: str) -> str | None:
    """Backward-compatible: leaf folder name only."""
    path = folder_path_for_article(article, mode)
    return path[-1] if path else None


def ensure_root_folder(name: str) -> dict[str, Any]:
    """Find or create a top-level article folder with *name*."""
    return ensure_folder_path([name])


def ensure_folder_path(parts: list[str]) -> dict[str, Any]:
    """Find or create a nested folder path; return the leaf folder dict."""
    parent_id: str | None = None
    leaf: dict[str, Any] | None = None
    for raw in parts:
        name = sanitize_folder_name(raw)

        def _run(pid: str | None = parent_id, n: str = name) -> dict[str, Any]:
            for folder in storage.list_article_folders():
                if (folder.get("parent_id") or None) == pid and folder.get("name") == n:
                    return folder
            return storage.create_article_folder(name=n, parent_id=pid)

        leaf = _db_retry(_run)
        parent_id = leaf["id"]
    assert leaf is not None
    return leaf


def classify_article(
    article_id: str,
    mode: str,
    *,
    only_uncategorized: bool = True,
) -> dict[str, Any]:
    """Move one article into the folder implied by *mode*.

    Returns ``{moved, folder_id, folder_name, folder_path, skipped, reason}``.
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
    path = folder_path_for_article(article, mode)
    if not path:
        return {"moved": False, "skipped": True, "reason": "no_field"}
    folder = ensure_folder_path(path)
    path_label = " / ".join(path)
    _db_retry(lambda: storage.move_article_to_folder(article_id, folder["id"]))
    return {
        "moved": True,
        "skipped": False,
        "folder_id": folder["id"],
        "folder_name": path_label,
        "folder_path": path,
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

    try:
        from workspace import get_active_workspace

        ws = get_active_workspace()
        lit = ws.literature_dir_name() if ws else ".literature"
    except Exception:
        lit = ".literature"
    aid = article_id_from_rel(rel, lit) or article_id_from_rel(rel, "articles")
    if aid:
        art = storage.get_article(aid)
        if art:
            return art
        return {"id": aid, "title": aid}

    basename = rel.rsplit("/", 1)[-1]
    stem = basename.rsplit(".", 1)[0] if "." in basename else basename
    basename_l = basename.lower()
    stem_l = stem.lower()

    articles = storage.get_all_articles()

    if basename_l not in {"original.pdf", "original.md"}:
        for art in articles:
            src = str(art.get("source_filename") or "").replace("\\", "/")
            src_base = src.rsplit("/", 1)[-1].lower()
            if src_base == basename_l:
                return art

    for art in articles:
        aid = str(art.get("id") or "")
        if not aid:
            continue
        if aid.lower() == stem_l or aid.lower() in stem_l or stem_l in aid.lower():
            return art
    return None
