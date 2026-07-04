"""Engine output paths — legacy ``{id}.md`` plus adjacent ``{basename}.parsed.md``."""
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


def publish_engine_markdown(
    article_dir: Path,
    article_id: str,
    pdf_path: Path | str,
    md_text: str | None = None,
    *,
    from_path: Path | None = None,
) -> Path:
    """Write parse output to legacy and workspace-adjacent paths. Returns legacy path."""
    legacy = legacy_md_path(article_dir, article_id)
    adjacent = adjacent_parsed_md_path(article_dir, pdf_path, article_id)

    if from_path is not None and from_path.exists():
        shutil.copy2(from_path, legacy)
        shutil.copy2(from_path, adjacent)
    elif md_text is not None:
        legacy.write_text(md_text, encoding="utf-8")
        adjacent.write_text(md_text, encoding="utf-8")
    elif legacy.exists() and not adjacent.exists():
        shutil.copy2(legacy, adjacent)
    return legacy
