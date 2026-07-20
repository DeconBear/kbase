"""Organize scattered literature PDFs into per-paper folders (background)."""
from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from literature_classify import classify_pdf
from workspace import Workspace, get_active_workspace

_INVALID_ID_RE = re.compile(r"[^A-Za-z0-9_-]+")

_scan_lock = threading.Lock()
_scan_thread: threading.Thread | None = None
_scan_status: dict[str, Any] = {
    "phase": "idle",  # idle|scanning|ready|organizing|done|error
    "total": 0,
    "done": 0,
    "percent": 0,
    "skippedKnownId": 0,
    "message": "",
    "targetDir": "",
    "moves": [],
    "skipped": [],
    "moved": [],
    "errors": [],
    "summary": {},
    "startedAt": "",
    "finishedAt": "",
}


def _literature_dir_name(ws: Workspace) -> str:
    return str(ws.load_manifest().get("literatureDir") or ".literature").strip("/") or ".literature"


def _organize_options(ws: Workspace) -> dict[str, Any]:
    m = ws.load_manifest()
    mode = str(m.get("organizeMode") or "copy").strip().lower()
    if mode not in {"copy", "move"}:
        mode = "copy"
    preserve = m.get("organizePreserveStructure")
    if preserve is None:
        preserve = True
    return {
        "mode": mode,
        "move": mode == "move",
        "preserveStructure": bool(preserve),
        "literatureDir": _literature_dir_name(ws),
    }


def _lit_roots(literature_dir: str) -> set[str]:
    roots = {"articles", "literature", ".literature"}
    lit = (literature_dir or ".literature").strip("/").lower()
    if lit:
        roots.add(lit)
    return roots


def article_id_from_rel(rel: str, literature_dir: str = ".literature") -> str | None:
    """Return article id when path is under ``<lit>/.../<id>/<file>``.

    Supports flat (``lit/<id>/original.pdf``) and structure-preserving
    (``lit/<mirrored dirs>/<id>/original.pdf``) layouts.
    """
    parts = rel.replace("\\", "/").strip("/").split("/")
    if len(parts) < 3:
        return None
    if parts[0].lower() not in _lit_roots(literature_dir):
        return None
    # Prefer parent of original.pdf / versioned md / meta as the article id.
    fname = parts[-1].lower()
    if (
        fname == "original.pdf"
        or fname.endswith(".md")
        or fname.endswith("_meta.json")
        or fname.endswith("_info.json")
        or fname == "original.md"
    ):
        aid = parts[-2]
    elif parts[-2].lower() == "attachments" and len(parts) >= 4:
        aid = parts[-3]
    else:
        # Flat legacy: lit/<id>/anything
        aid = parts[1] if len(parts) == 3 else parts[-2]
    if not aid or aid in {".", ".."} or aid.startswith("."):
        return None
    return aid


def _is_organized(rel: str, literature_dir: str) -> bool:
    """True when PDF already lives in a per-article literature folder."""
    return article_id_from_rel(rel, literature_dir) is not None


def _sanitize_rel_dir(rel_dir: str) -> str:
    """Sanitize a relative directory for use under the literature root."""
    parts = []
    for seg in rel_dir.replace("\\", "/").strip("/").split("/"):
        if not seg or seg in {".", ".."}:
            continue
        if seg.startswith("."):
            continue
        safe = re.sub(r'[<>:"|?*]', "_", seg).strip()
        if safe:
            parts.append(safe[:120])
    return "/".join(parts)


def article_dest_rel(
    *,
    literature_dir: str,
    article_id: str,
    source_rel: str,
    preserve_structure: bool,
    filename: str = "original.pdf",
) -> str:
    """Build destination relative path under the literature library root."""
    lit = (literature_dir or ".literature").strip("/")
    aid = article_id.strip("/")
    if not preserve_structure:
        return f"{lit}/{aid}/{filename}"
    parent = str(Path(source_rel.replace("\\", "/")).parent).replace("\\", "/")
    if parent in {".", ""}:
        return f"{lit}/{aid}/{filename}"
    mirrored = _sanitize_rel_dir(parent)
    if not mirrored:
        return f"{lit}/{aid}/{filename}"
    return f"{lit}/{mirrored}/{aid}/{filename}"


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


def _stem_match(a: str, b: str) -> bool:
    sa = re.sub(r"[_\-\s]+(si|supp|supplement).*$", "", a.lower())
    sb = re.sub(r"[_\-\s]+(si|supp|supplement).*$", "", b.lower())
    return sa == sb or sa.startswith(sb) or sb.startswith(sa)


def organize_status() -> dict[str, Any]:
    with _scan_lock:
        return dict(_scan_status)


def _set_status(**fields: Any) -> None:
    with _scan_lock:
        _scan_status.update(fields)
        total = int(_scan_status.get("total") or 0)
        done = int(_scan_status.get("done") or 0)
        if total > 0:
            _scan_status["percent"] = min(100, int(done * 100 / total))
        elif _scan_status.get("phase") in ("ready", "done"):
            _scan_status["percent"] = 100


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def iter_loose_pdfs(
    ws: Workspace,
    literature_dir: str,
) -> Iterator[tuple[str, Path, int]]:
    """Yield (rel, path, skipped_known_id_delta) for PDFs outside article folders.

    Already-ID'd trees (``articles/<id>/…``) are pruned without opening files.
    """
    roots = _lit_roots(literature_dir)
    skipped_batch = 0

    # Only managed workspace root — external @sources are read-only.
    root = ws.root
    if not root.is_dir():
        return

    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        abs_dir = Path(dirpath)
        try:
            rel_dir = ws.rel_path(abs_dir).replace("\\", "/")
        except ValueError:
            dirnames[:] = []
            continue

        parts = [p for p in rel_dir.split("/") if p]
        # Inside articles/<id>/... → skip entire subtree.
        if len(parts) >= 2 and parts[0].lower() in roots:
            dirnames[:] = []
            continue

        # At articles/ (or literature/) level: prune per-id subdirs, keep loose files.
        if len(parts) == 1 and parts[0].lower() in roots:
            known = []
            keep = []
            for name in dirnames:
                sub = abs_dir / name
                if sub.is_dir() and not name.startswith("."):
                    known.append(name)
                else:
                    keep.append(name)
            skipped_batch += len(known)
            dirnames[:] = keep

        if ".kbase" in parts:
            dirnames[:] = []
            continue

        for name in filenames:
            if not name.lower().endswith(".pdf"):
                continue
            path = abs_dir / name
            try:
                rel = ws.rel_path(path).replace("\\", "/")
            except ValueError:
                continue
            if ws.is_readonly_path(rel):
                continue
            if _is_organized(rel, literature_dir):
                skipped_batch += 1
                continue
            delta = skipped_batch
            skipped_batch = 0
            yield rel, path, delta

    if skipped_batch:
        # Flush remaining skip count via a sentinel-free update by caller.
        yield "", Path("."), skipped_batch


def _collect_plan(
    ws: Workspace,
    lit_dir: str,
    *,
    dry_run: bool,
    progress: bool = False,
) -> dict[str, Any]:
    import storage

    literature_dir = lit_dir
    mains: list[tuple[str, Path, dict]] = []
    supplements: list[tuple[str, Path, dict]] = []
    skipped_known = 0
    candidates: list[tuple[str, Path]] = []

    if progress:
        _set_status(
            phase="scanning",
            message="正在枚举散落 PDF（跳过已有 ID）…",
            done=0,
            total=0,
            skippedKnownId=0,
            startedAt=_now(),
            finishedAt="",
            errors=[],
        )

    for rel, path, delta in iter_loose_pdfs(ws, literature_dir):
        skipped_known += delta
        if not rel:
            continue
        candidates.append((rel, path))
        if progress and len(candidates) % 20 == 0:
            _set_status(
                skippedKnownId=skipped_known,
                message=f"已发现 {len(candidates)} 个待检查 PDF，跳过 {skipped_known} 个已有 ID",
            )

    total = len(candidates)
    if progress:
        _set_status(
            total=total,
            done=0,
            skippedKnownId=skipped_known,
            message=f"开始分类 {total} 个散落 PDF…",
        )

    for idx, (rel, path) in enumerate(candidates):
        try:
            cls = classify_pdf(
                path,
                rel_path=rel,
                literature_dir=literature_dir,
                use_llm="never",
            )
            if cls.get("document_kind") == "supplement":
                supplements.append((rel, path, cls))
            elif cls.get("is_literature") and cls.get("is_main"):
                mains.append((rel, path, cls))
        except Exception as exc:  # noqa: BLE001
            if progress:
                errs = list((_scan_status.get("errors") or []))
                errs.append(f"{rel}: {exc}")
                _set_status(errors=errs[-30:])
        if progress:
            _set_status(
                done=idx + 1,
                skippedKnownId=skipped_known,
                message=f"分类中 {idx + 1}/{total}",
            )

    opts = _organize_options(ws)
    preserve = bool(opts.get("preserveStructure"))
    used_ids: set[str] = set()
    for root in (
        ws.root / literature_dir,
        storage.ARTICLES_DIR,
        ws.root / "articles",
        ws.root / "literature",
        ws.root / ".literature",
    ):
        try:
            if not root.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                folder = Path(dirpath)
                aid = folder.name
                if not aid or aid.startswith("."):
                    dirnames[:] = [d for d in dirnames if not d.startswith(".")]
                    continue
                if (
                    (folder / "original.pdf").is_file()
                    or (folder / f"{aid}_meta.json").is_file()
                    or any(f.lower().endswith(".pdf") for f in filenames)
                ):
                    used_ids.add(aid)
                    dirnames[:] = []
        except OSError:
            continue
    moves: list[dict] = []
    skipped: list[dict] = []
    main_by_rel: dict[str, str] = {}

    for rel, path, cls in mains:
        aid = _propose_article_id(path, info=_read_info_for_path(ws, rel), used=used_ids)
        used_ids.add(aid)
        dest_rel = article_dest_rel(
            literature_dir=literature_dir,
            article_id=aid,
            source_rel=rel,
            preserve_structure=preserve,
            filename="original.pdf",
        )
        main_by_rel[rel] = aid
        moves.append({
            "from": rel,
            "to": dest_rel,
            "articleId": aid,
            "kind": "main",
        })
        src_dir = path.parent
        try:
            extras = list(src_dir.iterdir())
        except OSError:
            extras = []
        for extra in extras:
            if not extra.is_file():
                continue
            try:
                if extra.resolve() == path.resolve():
                    continue
            except OSError:
                continue
            name = extra.name.lower()
            if name.endswith((".parsed.md", ".zh.md", "_meta.json", "_info.json")):
                moves.append({
                    "from": ws.rel_path(extra).replace("\\", "/"),
                    "to": article_dest_rel(
                        literature_dir=literature_dir,
                        article_id=aid,
                        source_rel=rel,
                        preserve_structure=preserve,
                        filename=extra.name,
                    ),
                    "articleId": aid,
                    "kind": "derivative",
                })

    for rel, path, _cls in supplements:
        parent_rel = None
        parent_dir = path.parent
        for main_rel, main_path, _ in mains:
            try:
                if main_path.parent.resolve() == parent_dir.resolve():
                    parent_rel = main_rel
                    break
            except OSError:
                continue
        if not parent_rel:
            for main_rel, main_path, _ in mains:
                if _stem_match(path.stem, main_path.stem):
                    parent_rel = main_rel
                    break
        if not parent_rel or parent_rel not in main_by_rel:
            skipped.append({"from": rel, "reason": "no_parent"})
            continue
        aid = main_by_rel[parent_rel]
        dest_base = article_dest_rel(
            literature_dir=literature_dir,
            article_id=aid,
            source_rel=parent_rel,
            preserve_structure=preserve,
            filename="original.pdf",
        )
        # attachments/ under the article folder
        art_prefix = dest_base.rsplit("/", 1)[0]
        moves.append({
            "from": rel,
            "to": f"{art_prefix}/attachments/{path.name}",
            "articleId": aid,
            "kind": "supplement",
        })

    summary = {
        "mainCount": len(mains),
        "supplementCount": len(supplements),
        "moveCount": len(moves),
        "skippedKnownId": skipped_known,
        "candidateCount": total,
        "organizeMode": opts.get("mode") or "copy",
        "preserveStructure": preserve,
    }
    return {
        "ok": True,
        "dryRun": dry_run,
        "targetDir": literature_dir,
        "organizeMode": opts.get("mode") or "copy",
        "preserveStructure": preserve,
        "moves": moves,
        "skipped": skipped,
        "summary": summary,
        "skippedKnownId": skipped_known,
    }


def _read_info_for_path(ws: Workspace, rel: str) -> dict | None:
    import json

    path = ws.resolve(rel)
    parent = path.parent
    for info_path in parent.glob("*_info.json"):
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


def start_organize_scan(
    ws: Workspace | None = None,
    *,
    target_dir: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Start background preview scan; returns current status immediately."""
    global _scan_thread
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")
    lit_dir = (target_dir or _literature_dir_name(ws)).strip("/")

    with _scan_lock:
        alive = _scan_thread is not None and _scan_thread.is_alive()
        phase = _scan_status.get("phase")
        if alive and phase in ("scanning", "organizing") and not force:
            return dict(_scan_status)
        if (
            not force
            and phase == "ready"
            and _scan_status.get("targetDir") == lit_dir
            and isinstance(_scan_status.get("moves"), list)
        ):
            return dict(_scan_status)
        # Mark scanning synchronously so the HTTP response never races as idle.
        _scan_status.update({
            "phase": "scanning",
            "targetDir": lit_dir,
            "total": 0,
            "done": 0,
            "percent": 0,
            "skippedKnownId": 0,
            "moves": [],
            "skipped": [],
            "moved": [],
            "errors": [],
            "summary": {},
            "message": "开始扫描…",
            "startedAt": _now(),
            "finishedAt": "",
        })

    def _worker() -> None:
        try:
            plan = _collect_plan(ws, lit_dir, dry_run=True, progress=True)
            _set_status(
                phase="ready",
                targetDir=plan.get("targetDir") or lit_dir,
                moves=plan.get("moves") or [],
                skipped=plan.get("skipped") or [],
                summary=plan.get("summary") or {},
                skippedKnownId=plan.get("skippedKnownId") or 0,
                message=(
                    f"扫描完成：{(plan.get('summary') or {}).get('moveCount', 0)} 项待整理，"
                    f"跳过 {plan.get('skippedKnownId') or 0} 个已有 ID"
                ),
                finishedAt=_now(),
                percent=100,
            )
        except Exception as exc:  # noqa: BLE001
            _set_status(
                phase="error",
                message=str(exc),
                finishedAt=_now(),
            )

    with _scan_lock:
        _scan_thread = threading.Thread(target=_worker, daemon=True, name="org-scan")
        _scan_thread.start()
    return organize_status()


def start_organize_apply(
    ws: Workspace | None = None,
    *,
    move: bool | None = None,
    target_dir: str | None = None,
) -> dict[str, Any]:
    """Apply the last ready plan in a background thread."""
    global _scan_thread
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")

    opts = _organize_options(ws)
    do_move = bool(opts["move"]) if move is None else bool(move)

    with _scan_lock:
        phase = _scan_status.get("phase")
        moves = list(_scan_status.get("moves") or [])
        if phase == "organizing" and _scan_thread and _scan_thread.is_alive():
            return dict(_scan_status)
        if phase != "ready" or not moves:
            raise ValueError("请先完成整理预览扫描")

    lit_dir = (target_dir or _scan_status.get("targetDir") or _literature_dir_name(ws)).strip("/")
    plan_moves = moves
    plan_skipped = list(_scan_status.get("skipped") or [])

    def _worker() -> None:
        import storage
        from serve import scan_articles
        from storage import bind_data_root_runtime

        # Ensure library scan points at the same folder we move into.
        try:
            manifest = ws.load_manifest()
            if str(manifest.get("literatureDir") or "") != lit_dir:
                manifest["literatureDir"] = lit_dir
                ws.save_manifest(manifest)
        except Exception:
            pass
        try:
            bind_data_root_runtime(ws.root, literature_dir=lit_dir)
        except Exception:
            pass

        moved: list[dict] = []
        errors: list[str] = []
        main_ids: list[str] = []
        total = len(plan_moves)
        action_msg = "正在移动文件…" if do_move else "正在复制文件（保留原位）…"
        _set_status(
            phase="organizing",
            total=total,
            done=0,
            percent=0,
            moved=[],
            errors=[],
            organizeMode="move" if do_move else "copy",
            message=action_msg,
            startedAt=_now(),
            finishedAt="",
        )
        for idx, item in enumerate(plan_moves):
            src = ws.resolve(item["from"])
            dest = ws.resolve(item["to"])
            try:
                if not src.exists():
                    raise FileNotFoundError(f"源文件不存在: {item.get('from')}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists() and dest.resolve() != src.resolve():
                    # Avoid silent overwrite when ids collide.
                    raise FileExistsError(f"目标已存在: {item.get('to')}")
                if do_move:
                    try:
                        shutil.move(str(src), str(dest))
                    except OSError:
                        shutil.copy2(src, dest)
                        item = dict(item)
                        item["copied"] = True
                else:
                    shutil.copy2(src, dest)
                    item = dict(item)
                    item["copied"] = True
                moved.append(item)
                aid = item.get("articleId")
                if aid and item.get("kind") == "main":
                    main_ids.append(str(aid))
                    try:
                        storage.register_article_dir(str(aid), dest.parent)
                    except Exception:
                        pass
                    # Keep PDF under lit tree; bulk quick_parse deferred —
                    # scan_articles + ingest will register/index afterwards.
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{item.get('from')}: {exc}")
            _set_status(
                done=idx + 1,
                moved=moved,
                errors=errors[-40:],
                message=f"整理中 {idx + 1}/{total}",
            )

        _set_status(message="刷新文献库索引…")
        articles_count = 0
        try:
            bind_data_root_runtime(ws.root, literature_dir=lit_dir)
        except Exception:
            pass
        try:
            ws.scan(full=False)
        except Exception:
            pass
        try:
            arts = scan_articles()
            articles_count = len(arts or [])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"scan_articles: {exc}")
            arts = []
        try:
            from workspace_index import rebuild_index

            rebuild_index(ws)
        except Exception:
            pass
        # Background ingest for newly organized mains (preparse + metadata).
        try:
            from workspace_ingest import start_workspace_ingest

            start_workspace_ingest(ws, force=True)
        except Exception:
            pass

        report = {
            "ok": True,
            "moved": moved,
            "skipped": plan_skipped,
            "errors": errors,
            "mainIds": main_ids,
            "articlesCount": articles_count,
        }
        try:
            from storage import _atomic_write_json

            log_path = ws.tasks_dir / f"organize_{int(time.time())}.json"
            _atomic_write_json(log_path, report)
        except Exception:
            pass

        _set_status(
            phase="done",
            moved=moved,
            skipped=plan_skipped,
            errors=errors,
            summary={
                "movedCount": len(moved),
                "errorCount": len(errors),
                "mainCount": len(main_ids),
                "articlesCount": articles_count,
            },
            message=(
                f"已整理 {len(moved)} 个文件（{'移动' if do_move else '复制'}），"
                f"文献库现有 {articles_count} 篇"
                + (f"，{len(errors)} 个失败" if errors else "")
            ),
            finishedAt=_now(),
            percent=100,
            moves=[],
            articlesCount=articles_count,
        )

    with _scan_lock:
        _scan_thread = threading.Thread(target=_worker, daemon=True, name="org-apply")
        _scan_thread.start()
    return organize_status()


def _latest_organize_log(ws: Workspace) -> Path | None:
    tasks = ws.tasks_dir
    if not tasks.is_dir():
        return None
    # Only apply logs (organize_<ts>.json), not restore reports.
    logs = [
        p for p in tasks.glob("organize_*.json")
        if re.fullmatch(r"organize_\d+\.json", p.name)
    ]
    logs.sort(
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True,
    )
    return logs[0] if logs else None


def restore_from_organize_log(
    ws: Workspace | None = None,
    *,
    log_name: str | None = None,
    keep_library: bool = True,
) -> dict[str, Any]:
    """Copy organized files back to their original ``from`` paths.

    By default keeps library copies (``keep_library=True``). Uses the latest
    ``.kbase/tasks/organize_*.json`` unless ``log_name`` is given.
    """
    import json

    from storage import _atomic_write_json

    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")

    if log_name:
        safe = Path(str(log_name).replace("\\", "/")).name
        log_path = ws.tasks_dir / safe
    else:
        log_path = _latest_organize_log(ws)
    if log_path is None or not log_path.is_file():
        raise ValueError("未找到整理日志 organize_*.json")

    try:
        report = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取整理日志: {exc}") from exc

    items = list(report.get("moved") or [])
    restored: list[dict] = []
    skipped: list[dict] = []
    errors: list[str] = []

    def _resolve_library_src(rel: str) -> Path | None:
        """Resolve a library file, trying literatureDir aliases if needed."""
        candidates = [rel]
        parts = rel.replace("\\", "/").split("/", 1)
        if len(parts) == 2 and parts[0].lower() in {"articles", "literature", ".literature"}:
            for root_name in (".literature", "literature", "articles"):
                alt = f"{root_name}/{parts[1]}"
                if alt not in candidates:
                    candidates.append(alt)
        for cand in candidates:
            path = ws.resolve(cand)
            if path.is_file():
                return path
        return None

    for item in items:
        src_rel = str(item.get("to") or "").replace("\\", "/")
        dest_rel = str(item.get("from") or "").replace("\\", "/")
        if not src_rel or not dest_rel:
            skipped.append({"item": item, "reason": "bad_paths"})
            continue
        src = _resolve_library_src(src_rel)
        dest = ws.resolve(dest_rel)
        try:
            if src is None:
                skipped.append({"from": dest_rel, "to": src_rel, "reason": "library_missing"})
                continue
            if dest.exists():
                skipped.append({"from": dest_rel, "to": src_rel, "reason": "already_exists"})
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            entry = {
                "from": src_rel,
                "to": dest_rel,
                "articleId": item.get("articleId"),
                "kind": item.get("kind"),
            }
            if not keep_library:
                try:
                    src.unlink()
                    entry["removedLibrary"] = True
                except OSError:
                    entry["removedLibrary"] = False
            restored.append(entry)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{dest_rel}: {exc}")

    out = {
        "ok": True,
        "log": log_path.name,
        "keepLibrary": keep_library,
        "restored": restored,
        "skipped": skipped,
        "errors": errors,
        "summary": {
            "restoredCount": len(restored),
            "skippedCount": len(skipped),
            "errorCount": len(errors),
        },
        "message": (
            f"已还原 {len(restored)} 个文件到原路径"
            + ("（文献库副本已保留）" if keep_library else "（已删除文献库副本）")
            + (f"，跳过 {len(skipped)}" if skipped else "")
            + (f"，失败 {len(errors)}" if errors else "")
        ),
    }
    try:
        out_path = ws.tasks_dir / f"organize_restore_{int(time.time())}.json"
        _atomic_write_json(out_path, out)
        out["restoreLog"] = out_path.name
    except Exception:
        pass
    return out


def migrate_literature_dir_name(
    ws: Workspace | None = None,
    new_name: str = ".literature",
) -> dict[str, Any]:
    """Rename on-disk literature folder and update manifest ``literatureDir``."""
    from storage import bind_data_root_runtime

    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")

    new_name = (new_name or ".literature").strip().strip("/") or ".literature"
    if "/" in new_name or "\\" in new_name or ".." in new_name:
        raise ValueError("literatureDir must be a single folder name")

    manifest = ws.load_manifest()
    old_name = str(manifest.get("literatureDir") or "").strip("/") or "literature"
    old_path = ws.root / old_name
    new_path = ws.root / new_name

    renamed = False
    if old_name != new_name:
        if old_path.is_dir() and not new_path.exists():
            old_path.rename(new_path)
            renamed = True
        elif old_path.is_dir() and new_path.is_dir() and old_path.resolve() != new_path.resolve():
            # Merge: move children that don't collide.
            for child in list(old_path.iterdir()):
                dest = new_path / child.name
                if dest.exists():
                    continue
                shutil.move(str(child), str(dest))
            renamed = True
        elif not old_path.exists() and (ws.root / "literature").is_dir() and new_name == ".literature":
            # Common migration: literature → .literature even if manifest already updated.
            lit = ws.root / "literature"
            if not new_path.exists():
                lit.rename(new_path)
                renamed = True
                old_name = "literature"

    # Also migrate legacy aliases when targeting .literature
    if new_name == ".literature":
        for alias in ("literature", "articles"):
            alias_path = ws.root / alias
            if not alias_path.is_dir():
                continue
            if alias_path.resolve() == new_path.resolve():
                continue
            if not new_path.exists():
                alias_path.rename(new_path)
                renamed = True
                old_name = alias
                break

    manifest["literatureDir"] = new_name
    if manifest.get("organizeMode") is None:
        manifest["organizeMode"] = "copy"
    if manifest.get("organizePreserveStructure") is None:
        manifest["organizePreserveStructure"] = True
    ws.save_manifest(manifest)
    bind_data_root_runtime(ws.root, literature_dir=new_name)
    return {
        "ok": True,
        "renamed": renamed,
        "from": old_name,
        "to": new_name,
        "path": str(new_path),
    }


# ---- backward-compatible sync helpers (prefer background APIs) ----

def organize_preview(
    ws: Workspace | None = None,
    *,
    target_dir: str | None = None,
) -> dict[str, Any]:
    """Synchronous preview (tests / CLI). Prefer ``start_organize_scan``."""
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")
    lit_dir = target_dir or _literature_dir_name(ws)
    return _collect_plan(ws, lit_dir, dry_run=True, progress=False)


def organize_literature(
    ws: Workspace | None = None,
    *,
    dry_run: bool = False,
    target_dir: str | None = None,
    move: bool | None = None,
) -> dict[str, Any]:
    """Synchronous organize (tests / CLI). Prefer ``start_organize_apply``."""
    ws = ws or get_active_workspace()
    if ws is None:
        raise ValueError("未打开工作空间")
    lit_dir = (target_dir or _literature_dir_name(ws)).strip("/")
    opts = _organize_options(ws)
    do_move = bool(opts["move"]) if move is None else bool(move)
    plan = _collect_plan(ws, lit_dir, dry_run=dry_run, progress=False)
    if dry_run:
        return plan

    # Reuse apply path by seeding status then running worker body inline.
    _set_status(phase="ready", moves=plan.get("moves") or [], skipped=plan.get("skipped") or [], targetDir=lit_dir)
    # Run apply synchronously for CLI compatibility.
    import storage
    from document_info import quick_parse_pdf
    from serve import _start_extract_info, scan_articles

    moved: list[dict] = []
    errors: list[str] = []
    for item in plan.get("moves") or []:
        src = ws.resolve(item["from"])
        dest = ws.resolve(item["to"])
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if do_move:
                try:
                    shutil.move(str(src), str(dest))
                except OSError:
                    shutil.copy2(src, dest)
                    item = dict(item)
                    item["copied"] = True
            else:
                shutil.copy2(src, dest)
                item = dict(item)
                item["copied"] = True
            moved.append(item)
            aid = item.get("articleId")
            if aid and item.get("kind") == "main":
                storage.register_article_dir(str(aid), dest.parent)
                art_dir = storage.resolve_article_dir(str(aid), create=True)
                if not (art_dir / f"{aid}.md").exists():
                    quick_parse_pdf(aid, dest, source_filename="original.pdf")
                _start_extract_info(aid, reason="organize", allow_parallel=True)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{item['from']}: {exc}")

    try:
        ws.scan(full=False)
    except Exception:
        pass
    try:
        scan_articles()
    except Exception:
        pass
    return {"ok": True, "moved": moved, "skipped": plan.get("skipped") or [], "errors": errors}
