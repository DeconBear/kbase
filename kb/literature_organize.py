"""Organize scattered literature PDFs into per-paper folders."""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path
from typing import Any

from literature_classify import classify_pdf
from workspace import Workspace, get_active_workspace

_INVALID_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _literature_dir_name(ws: Workspace) -> str:
    return str(ws.load_manifest().get("literatureDir") or "articles")


def _sanitize_article_id(value: str) -> str:
    from serve import sanitize_article_id

    return sanitize_article_id(value)[:80] or "paper"


def _propose_article_id(
    pdf_path: Path,
    *,
    info: dict | None,
    used: set[str],
) -> str:
    if info:
        title = str(info.get("title") or "").strip()
        year = str(info.get("year") or "").strip()
        doi = str(info.get("doi") or "").strip()
        if title and year:
            stem = _sanitize_article_id(re.sub(r"\s+", "", title[:40]) + year)
            if stem not in used:
                return stem
        if doi:
            stem = _sanitize_article_id(doi.replace("/", "_").replace(".", "_")[:60])
            if stem not in used:
                return stem
    stem = _sanitize_article_id(pdf_path.stem)
    if stem.lower() == "original":
        stem = _sanitize_article_id(pdf_path.parent.name)
    candidate = stem
    n = 2
    while candidate in used:
        candidate = f"{stem}_{n}"
        n += 1
    return candidate


def _is_organized(rel: str, literature_dir: str) -> bool:
    norm = rel.replace("\\", "/")
    return bool(
        re.match(
            rf"^{re.escape(literature_dir)}/[^/]+/original\.pdf$",
            norm,
            re.I,
        )
        or re.match(r"^(articles|literature|\.literature)/[^/]+/original\.pdf$", norm, re.I)
    )


def _stem_match(a: str, b: str) -> bool:
    sa = re.sub(r"[_\-\s]+(si|supp|supplement).*$", "", a.lower())
    sb = re.sub(r"[_\-\s]+(si|supp|supplement).*$", "", b.lower())
    return sa == sb or sa.startswith(sb) or sb.startswith(sa)


def organize_preview(
    ws: Workspace | None = None,
    *,
    target_dir: str | None = None,
) -> dict[str, Any]:
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")
    lit_dir = target_dir or _literature_dir_name(ws)
    return _collect_plan(ws, lit_dir, dry_run=True)


def organize_literature(
    ws: Workspace | None = None,
    *,
    dry_run: bool = False,
    target_dir: str | None = None,
    move: bool = True,
) -> dict[str, Any]:
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")
    lit_dir = (target_dir or _literature_dir_name(ws)).strip("/")
    plan = _collect_plan(ws, lit_dir, dry_run=dry_run)
    if dry_run:
        return plan

    import storage
    from document_info import quick_parse_pdf
    from serve import _start_extract_info, scan_articles

    moved: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []

    for item in plan.get("moves") or []:
        src = ws.resolve(item["from"])
        dest = ws.resolve(item["to"])
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if move:
                try:
                    shutil.move(str(src), str(dest))
                except OSError:
                    shutil.copy2(src, dest)
                    item["copied"] = True
            else:
                shutil.copy2(src, dest)
            moved.append(item)
            aid = item.get("articleId")
            if aid:
                art_dir = storage.ARTICLES_DIR / aid
                if art_dir.resolve() != dest.parent.resolve():
                    storage.ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
                if not (art_dir / f"{aid}.md").exists():
                    quick_parse_pdf(aid, dest, source_filename="original.pdf")
                _start_extract_info(aid, reason="organize", allow_parallel=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{item['from']}: {exc}")

    for item in plan.get("skipped") or []:
        skipped.append(item)

    ws.scan(full=True)
    scan_articles()
    try:
        from workspace_index import rebuild_index

        rebuild_index(ws)
    except Exception:
        pass

    log_path = ws.tasks_dir / f"organize_{int(time.time())}.json"
    report = {"ok": True, "moved": moved, "skipped": skipped, "errors": errors}
    try:
        from storage import _atomic_write_json

        _atomic_write_json(log_path, report)
    except Exception:
        pass
    return report


def _collect_plan(ws: Workspace, lit_dir: str, *, dry_run: bool) -> dict[str, Any]:
    import storage

    literature_dir = lit_dir
    mains: list[tuple[str, Path, dict]] = []
    supplements: list[tuple[str, Path, dict]] = []

    for path in ws.iter_candidate_files():
        if path.suffix.lower() != ".pdf":
            continue
        rel = ws.rel_path(path)
        if _is_organized(rel, literature_dir):
            continue
        cls = classify_pdf(path, rel_path=rel, literature_dir=literature_dir, use_llm="never")
        if cls.get("document_kind") == "supplement":
            supplements.append((rel, path, cls))
        elif cls.get("is_literature") and cls.get("is_main"):
            mains.append((rel, path, cls))

    used_ids = {p.name for p in storage.ARTICLES_DIR.iterdir() if p.is_dir()} if storage.ARTICLES_DIR.exists() else set()
    moves: list[dict] = []
    skipped: list[dict] = []

    main_by_rel: dict[str, str] = {}

    for rel, path, cls in mains:
        aid = _propose_article_id(path, info=_read_info_for_path(ws, rel), used=used_ids)
        used_ids.add(aid)
        dest_rel = f"{literature_dir}/{aid}/original.pdf"
        main_by_rel[rel] = aid
        moves.append({
            "from": rel,
            "to": dest_rel,
            "articleId": aid,
            "kind": "main",
        })
        src_dir = path.parent
        for extra in src_dir.iterdir():
            if not extra.is_file():
                continue
            if extra.resolve() == path.resolve():
                continue
            name = extra.name.lower()
            if name.endswith(".parsed.md") or name.endswith(".zh.md") or name.endswith("_meta.json") or name.endswith("_info.json"):
                moves.append({
                    "from": ws.rel_path(extra),
                    "to": f"{literature_dir}/{aid}/{extra.name}",
                    "articleId": aid,
                    "kind": "derivative",
                })

    for rel, path, _cls in supplements:
        parent_rel = None
        parent_dir = path.parent
        for main_rel, main_path, _ in mains:
            if main_path.parent.resolve() == parent_dir.resolve():
                parent_rel = main_rel
                break
        if not parent_rel:
            for main_rel, main_path, _ in mains:
                if _stem_match(path.stem, main_path.stem):
                    parent_rel = main_rel
                    break
        if not parent_rel or parent_rel not in main_by_rel:
            skipped.append({"from": rel, "reason": "no_parent"})
            continue
        aid = main_by_rel[parent_rel]
        moves.append({
            "from": rel,
            "to": f"{literature_dir}/{aid}/attachments/{path.name}",
            "articleId": aid,
            "kind": "supplement",
        })

    return {
        "ok": True,
        "dryRun": dry_run,
        "targetDir": literature_dir,
        "moves": moves,
        "skipped": skipped,
        "summary": {
            "mainCount": len(mains),
            "supplementCount": len(supplements),
            "moveCount": len(moves),
        },
    }


def _read_info_for_path(ws: Workspace, rel: str) -> dict | None:
    import json

    path = ws.resolve(rel)
    parent = path.parent
    for pattern in ("*_info.json",):
        for info_path in parent.glob(pattern):
            try:
                return json.loads(info_path.read_text(encoding="utf-8"))
            except Exception:
                continue
    parts = rel.replace("\\", "/").split("/")
    if len(parts) >= 2:
        aid = parts[-2]
        for root in (ws.root / "articles", ws.root / "literature", ws.root):
            info_path = root / aid / f"{aid}_info.json"
            if info_path.exists():
                try:
                    return json.loads(info_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
    return None
