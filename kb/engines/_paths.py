"""Shared paths for PDF conversion engines.

Defers path resolution to ``kb.storage`` so the data root layout is
defined in exactly one place.
"""
from __future__ import annotations

from pathlib import Path

from storage import ARTICLES_DIR, DATA_ROOT, LOW_MEMORY_CONFIG, resolve_article_dir

PACKAGE_DIR = Path(__file__).resolve().parent.parent  # kb/
REPO_ROOT = PACKAGE_DIR.parent

__all__ = [
    "ARTICLES_DIR",
    "DATA_ROOT",
    "LOW_MEMORY_CONFIG",
    "PACKAGE_DIR",
    "REPO_ROOT",
    "resolve_article_dir",
]
