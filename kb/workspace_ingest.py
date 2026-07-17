"""Background workspace PDF ingest — classify, preparse, extract metadata."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any

from literature_classify import classify_pdf
from workspace import Workspace, get_active_workspace

_ingest_lock = threading.Lock()
_ingest_thread: threading.Thread | None = None
_ingest_status: dict[str, Any] = {
    "phase": "idle",
    "total": 0,
    "done": 0,
    "errors": [],
    "skippedKnownId": 0,
    "startedAt": "",
    "finishedAt": "",
}


def ingest_status() -> dict[str, Any]:
    with _ingest_lock:
        return dict(_ingest_status)


def _set_status(**fields: Any) -> None:
    with _ingest_lock:
        _ingest_status.update(fields)


def _manifest_flags(ws: Workspace) -> dict[str, Any]:
    m = ws.load_manifest()
    return {
        "ingestOnOpen": m.get("ingestOnOpen", True),
        "autoClassifyPdfs": m.get("autoClassifyPdfs", True),
        "autoExtractMetadata": m.get("autoExtractMetadata", True),
        "classifyUseLlm": m.get("classifyUseLlm", "uncertain_only"),
        "literatureDir": m.get("literatureDir") or "articles",
    }


def _is_in_literature_folder(rel: str, literature_dir: str) -> bool:
    norm = rel.replace("\\", "/").lower()
    lit = literature_dir.lower().strip("/")
    for prefix in (f"{lit}/", "articles/", "literature/", ".literature/"):
        if norm.startswith(prefix) and norm.endswith("/original.pdf"):
            return True
        if norm.startswith(prefix):
            parts = norm.split("/")
            if len(parts) >= 3 and parts[-1] == "original.pdf":
                return True
    return False


def _article_id_from_path(rel: str, literature_dir: str) -> str | None:
    norm = rel.replace("\\", "/")
    parts = norm.split("/")
    if len(parts) >= 3 and parts[-1].lower() == "original.pdf":
        parent = parts[-2]
        root = parts[-3].lower()
        if root in {literature_dir.lower(), "articles", "literature", ".literature"}:
            return parent
    m = re.match(r"^(?:articles|literature|\.literature|" + re.escape(literature_dir) + r")/([^/]+)/", norm, re.I)
    return m.group(1) if m else None


def _update_sidecar(ws: Workspace, rel: str, classification: dict, article_id: str | None) -> None:
    docs = ws.list_documents()
    doc = next((d for d in docs if d.get("path") == rel), None)
    if not doc:
        try:
            doc = ws.register_file(rel)
        except (OSError, ValueError):
            return
    meta = dict(doc.get("metadata") or {})
    meta["isLiterature"] = bool(classification.get("is_literature"))
    meta["documentKind"] = classification.get("document_kind") or "file"
    meta["organizeStatus"] = "organized" if _is_in_literature_folder(rel, _manifest_flags(ws)["literatureDir"]) else "pending"
    if article_id:
        meta["linkedArticleId"] = article_id
    doc["metadata"] = meta
    ws.save_document(doc)


def run_workspace_ingest(ws: Workspace | None = None, *, force: bool = False) -> None:
    """Scan workspace PDFs, classify, preparse and queue metadata extraction."""
    global _ingest_thread

    ws = ws or get_active_workspace()
    if ws is None:
        return

    flags = _manifest_flags(ws)
    if not flags["ingestOnOpen"] and not force:
        return

    with _ingest_lock:
        if _ingest_thread and _ingest_thread.is_alive():
            if not force:
                return

    def _worker() -> None:
        import storage
        from document_info import quick_parse_pdf
        from serve import _start_extract_info, scan_articles

        from literature_organize import article_id_from_rel, iter_loose_pdfs

        errors: list[str] = []
        literature_dir = flags["literatureDir"]
        # Loose PDFs only — already-ID'd trees are skipped (no classify/open).
        pdfs: list[tuple[str, Path]] = []
        skipped_known = 0
        for rel, path, delta in iter_loose_pdfs(ws, literature_dir):
            skipped_known += delta
            if rel:
                pdfs.append((rel, path))

        _set_status(
            phase="scanning",
            total=len(pdfs),
            done=0,
            errors=[],
            skippedKnownId=skipped_known,
            startedAt=time.strftime("%Y-%m-%d %H:%M:%S"),
            finishedAt="",
        )

        for idx, (rel, path) in enumerate(pdfs):
            try:
                # Defensive: never re-scan PDFs that already have an article id.
                if article_id_from_rel(rel, literature_dir):
                    _set_status(done=idx + 1, errors=errors)
                    continue
                if not flags["autoClassifyPdfs"]:
                    classification = {
                        "is_literature": True,
                        "is_main": True,
                        "document_kind": "paper",
                        "confidence": 0.5,
                        "reason": "skipped_classify",
                    }
                else:
                    classification = classify_pdf(
                        path,
                        rel_path=rel,
                        literature_dir=literature_dir,
                        use_llm=flags["classifyUseLlm"],
                    )

                article_id = _article_id_from_path(rel, literature_dir)
                if classification.get("is_literature") and classification.get("is_main"):
                    if article_id:
                        art_dir = storage.ARTICLES_DIR / article_id
                        art_dir.mkdir(parents=True, exist_ok=True)
                        md_legacy = art_dir / f"{article_id}.md"
                        if not md_legacy.exists():
                            quick_parse_pdf(
                                article_id,
                                path,
                                source_filename=path.name,
                            )
                        if flags["autoExtractMetadata"]:
                            _start_extract_info(article_id, reason="ingest", allow_parallel=True)
                    else:
                        _update_sidecar(ws, rel, classification, None)
                else:
                    _update_sidecar(ws, rel, classification, article_id)

            except Exception as exc:  # noqa: BLE001
                errors.append(f"{rel}: {exc}")
            _set_status(done=idx + 1, errors=errors)

        try:
            scan_articles()
        except Exception:
            pass
        try:
            from workspace_index import rebuild_index

            rebuild_index(ws)
        except Exception:
            pass

        _set_status(phase="done", finishedAt=time.strftime("%Y-%m-%d %H:%M:%S"))

    _ingest_thread = threading.Thread(target=_worker, daemon=True)
    _ingest_thread.start()


def start_workspace_ingest(ws: Workspace | None = None, *, force: bool = False) -> bool:
    """Start ingest in background if not already running."""
    global _ingest_thread
    with _ingest_lock:
        if _ingest_thread and _ingest_thread.is_alive() and not force:
            return False
    run_workspace_ingest(ws, force=force)
    return True


def library_status(ws: Workspace | None = None) -> dict[str, Any]:
    """Fast library counters — never walks/classifies the whole tree.

    Pending counts come from the latest organize scan cache when available;
    otherwise ``pendingOrganize`` is 0 until a background scan finishes.
    """
    import storage
    from literature_organize import organize_status

    ws = ws or get_active_workspace()
    if ws is None:
        return {
            "scatteredPdfs": 0,
            "indexedArticles": 0,
            "pendingOrganize": 0,
            "supplements": 0,
            "linkedPdfs": 0,
            "scanPhase": "idle",
        }

    indexed = 0
    try:
        indexed = len(storage.get_all_articles())
    except Exception:
        indexed = 0

    org = organize_status()
    phase = org.get("phase") or "idle"
    summary = org.get("summary") or {}
    moves = org.get("moves") or []
    pending = 0
    supplements = 0
    if phase in ("ready", "organizing", "done", "scanning"):
        pending = int(summary.get("mainCount") or 0)
        if phase == "ready" and not pending and moves:
            pending = sum(1 for m in moves if m.get("kind") == "main")
        supplements = int(summary.get("supplementCount") or 0)

    return {
        "scatteredPdfs": pending,
        "indexedArticles": indexed,
        "pendingOrganize": pending,
        "supplements": supplements,
        "linkedPdfs": 0,
        "scanPhase": phase,
        "scanPercent": org.get("percent") or 0,
        "scanMessage": org.get("message") or "",
        "skippedKnownId": org.get("skippedKnownId") or summary.get("skippedKnownId") or 0,
    }
