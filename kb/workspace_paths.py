"""Engine output paths — workspace-adjacent ``.parsed.md`` / ``.zh.md`` primary, legacy copies optional."""
from __future__ import annotations

import shutil
from pathlib import Path


def parsed_md_basename(pdf_path: Path | str, article_id: str) -> str:
    stem = Path(pdf_path).stem
    if stem.lower() == "original":
        return article_id
    return stem


def legacy_md_path(article_dir: Path, article_id: str) -> Path:
    return article_dir / f"{article_id}.md"


def adjacent_parsed_md_path(
    article_dir: Path,
    pdf_path: Path | str,
    article_id: str,
) -> Path:
    basename = parsed_md_basename(pdf_path, article_id)
    return article_dir / f"{basename}.parsed.md"


def adjacent_zh_md_path(
    article_dir: Path,
    pdf_path: Path | str,
    article_id: str,
    lang: str = "zh",
) -> Path:
    basename = parsed_md_basename(pdf_path, article_id)
    return article_dir / f"{basename}.{lang}.md"


def legacy_translated_path(article_dir: Path, article_id: str) -> Path:
    return article_dir / f"{article_id}_translated.md"


def _sync_legacy_copy(primary: Path, legacy: Path) -> None:
    if primary.exists():
        shutil.copy2(primary, legacy)


def publish_engine_markdown(
    article_dir: Path,
    article_id: str,
    pdf_path: Path | str,
    md_text: str | None = None,
    *,
    from_path: Path | None = None,
) -> Path:
    """Write parse output — adjacent ``.parsed.md`` is canonical; legacy ``{id}.md`` kept in sync."""
    legacy = legacy_md_path(article_dir, article_id)
    adjacent = adjacent_parsed_md_path(article_dir, pdf_path, article_id)

    if from_path is not None and from_path.exists():
        shutil.copy2(from_path, adjacent)
        _sync_legacy_copy(adjacent, legacy)
    elif md_text is not None:
        adjacent.write_text(md_text, encoding="utf-8")
        _sync_legacy_copy(adjacent, legacy)
    elif adjacent.exists() and not legacy.exists():
        _sync_legacy_copy(adjacent, legacy)
    elif legacy.exists() and not adjacent.exists():
        _sync_legacy_copy(legacy, adjacent)
    return adjacent if adjacent.exists() else legacy


def publish_translation_markdown(
    article_dir: Path,
    article_id: str,
    pdf_path: Path | str,
    md_text: str,
    *,
    lang: str = "zh",
) -> Path:
    """Write translation — adjacent ``.{lang}.md`` is canonical; legacy ``_translated.md`` kept in sync."""
    adjacent = adjacent_zh_md_path(article_dir, pdf_path, article_id, lang)
    legacy = legacy_translated_path(article_dir, article_id)
    adjacent.write_text(md_text, encoding="utf-8")
    _sync_legacy_copy(adjacent, legacy)
    return adjacent
