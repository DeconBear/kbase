"""Workspace kernel — open local directories, scan documents, manage .kbase sidecars."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app_config import set_last_workspace_path, touch_recent_workspace
from storage import _atomic_write_json

WORKSPACE_SPEC_VERSION = 1
DOC_ID_RE = re.compile(r"^doc_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

DEFAULT_IGNORE_GLOBS = (
    "**/.git/**",
    "**/node_modules/**",
    "**/.kbase/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
)

KIND_BY_EXT: dict[str, str] = {
    ".md": "markdown",
    ".pdf": "pdf",
    ".docx": "word",
    ".html": "html",
    ".htm": "html",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".canvas.json": "canvas",
}

DERIVATION_SUFFIXES = (
    ".parsed.md",
    ".summary.md",
)

DERIVATION_LANG_RE = re.compile(
    r"\.([a-z]{2}(?:-[a-z]{2})?)\.md$", re.IGNORECASE
)
ENGINE_PARSED_RE = re.compile(
    r"\.[a-z0-9_-]+\.parsed\.md$", re.IGNORECASE
)

_active_lock = threading.Lock()
_active_workspace: Workspace | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_doc_id() -> str:
    return f"doc_{uuid.uuid4()}"


def new_link_id() -> str:
    return f"link_{uuid.uuid4().hex[:12]}"


def new_workspace_id() -> str:
    return f"ws_{uuid.uuid4()}"


def _matches_any_glob(rel_posix: str, globs: tuple[str, ...]) -> bool:
    for pattern in globs:
        if fnmatch.fnmatch(rel_posix, pattern):
            return True
        if fnmatch.fnmatch(rel_posix.replace("\\", "/"), pattern):
            return True
    return False


def is_derivation_path(rel_path: str) -> bool:
    name = Path(rel_path).name.lower()
    for suffix in DERIVATION_SUFFIXES:
        if name.endswith(suffix):
            return True
    if ENGINE_PARSED_RE.search(name):
        return True
    if DERIVATION_LANG_RE.search(name) and not name.endswith(".parsed.md"):
        stem = Path(name).stem
        if "." in stem and stem.split(".")[-1] not in ("md", "parsed"):
            return True
    if ".parsed." in name and name.endswith(".md"):
        return True
    return False


def detect_kind(rel_path: str) -> str | None:
    rel = rel_path.replace("\\", "/")
    lower = rel.lower()
    if lower.endswith(".canvas.json"):
        return "canvas"
    ext = Path(rel).suffix.lower()
    return KIND_BY_EXT.get(ext)


def content_hash(path: Path, *, chunk_bytes: int = 1_048_576) -> str:
    size = path.stat().st_size
    digest = hashlib.sha256()
    digest.update(str(size).encode("ascii"))
    with path.open("rb") as fh:
        digest.update(fh.read(chunk_bytes))
    return f"sha256:{digest.hexdigest()}"


class Workspace:
    """A user-chosen directory with an initialized ``.kbase/`` metadata tree."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.kbase = self.root / ".kbase"
        self.documents_dir = self.kbase / "documents"
        self.links_path = self.kbase / "links.json"
        self.manifest_path = self.kbase / "workspace.json"
        self.databases_dir = self.kbase / "databases"
        self.bookmarks_dir = self.kbase / "bookmarks"
        self.tasks_dir = self.kbase / "tasks"
        self.logs_dir = self.kbase / "logs"
        self._manifest_cache: dict[str, Any] | None = None

    @classmethod
    def open(cls, path: str | Path, *, init: bool = True) -> Workspace:
        root = Path(path).resolve()
        if not root.is_dir():
            raise ValueError(f"工作空间路径不存在或不是目录: {root}")
        ws = cls(root)
        if init:
            ws.ensure_initialized()
        return ws

    def ensure_initialized(self) -> None:
        for d in (
            self.kbase,
            self.documents_dir,
            self.databases_dir,
            self.bookmarks_dir,
            self.tasks_dir,
            self.logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            manifest = {
                "specVersion": WORKSPACE_SPEC_VERSION,
                "id": new_workspace_id(),
                "name": self.root.name,
                "root": str(self.root),
                "createdAt": _now_iso(),
                "openedAt": _now_iso(),
                "ignoreGlobs": list(DEFAULT_IGNORE_GLOBS),
                "derivationNaming": {
                    "parsedMd": "{basename}.parsed.md",
                    "translatedMd": "{basename}.{lang}.md",
                    "summaryMd": "{basename}.summary.md",
                },
                "derivationStorage": "adjacent",
                "defaultParseEngine": "marker",
                "defaultTranslateLang": "zh",
            }
            self.save_manifest(manifest)
        if not self.links_path.exists():
            _atomic_write_json(
                self.links_path,
                {"specVersion": 1, "edges": []},
            )
        gitignore = self.kbase / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text(
                "index.db\nlogs/\ncache/\n*.tmp\n",
                encoding="utf-8",
            )

    def load_manifest(self) -> dict[str, Any]:
        if self._manifest_cache is not None:
            return self._manifest_cache
        if not self.manifest_path.exists():
            self.ensure_initialized()
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self._manifest_cache = data
        return data

    def save_manifest(self, data: dict[str, Any]) -> None:
        data["openedAt"] = _now_iso()
        data["root"] = str(self.root)
        _atomic_write_json(self.manifest_path, data)
        self._manifest_cache = data

    def rel_path(self, abs_path: Path) -> str:
        return abs_path.resolve().relative_to(self.root).as_posix()

    def resolve(self, rel_path: str) -> Path:
        rel = rel_path.replace("\\", "/").lstrip("/")
        target = (self.root / rel).resolve()
        if not str(target).startswith(str(self.root)):
            raise ValueError("路径越界")
        return target

    def ignore_globs(self) -> tuple[str, ...]:
        manifest = self.load_manifest()
        globs = manifest.get("ignoreGlobs") or list(DEFAULT_IGNORE_GLOBS)
        return tuple(str(g) for g in globs)

    def iter_candidate_files(self) -> Iterator[Path]:
        globs = self.ignore_globs()
        for path in self.root.rglob("*"):
            if not path.is_file():
                continue
            rel = self.rel_path(path)
            if _matches_any_glob(rel, globs):
                continue
            yield path

    def sidecar_path(self, doc_id: str) -> Path:
        if not DOC_ID_RE.match(doc_id):
            raise ValueError("Invalid doc_id")
        return self.documents_dir / f"{doc_id}.json"

    def load_document(self, doc_id: str) -> dict[str, Any] | None:
        path = self.sidecar_path(doc_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    def save_document(self, sidecar: dict[str, Any]) -> None:
        doc_id = sidecar.get("id") or ""
        if not DOC_ID_RE.match(doc_id):
            raise ValueError("sidecar missing valid id")
        sidecar["updatedAt"] = _now_iso()
        _atomic_write_json(self.sidecar_path(doc_id), sidecar)

    def list_documents(
        self,
        *,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> list[dict[str, Any]]:
        docs: list[dict[str, Any]] = []
        if not self.documents_dir.exists():
            return docs
        q = (query or "").strip().lower()
        for path in sorted(self.documents_dir.glob("doc_*.json")):
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if kind and doc.get("kind") != kind:
                continue
            if status and doc.get("status", "active") != status:
                continue
            if q:
                hay = " ".join(
                    str(doc.get(k, ""))
                    for k in ("title", "path", "id")
                ).lower()
                if q not in hay:
                    continue
            docs.append(doc)
        return docs

    def load_links(self) -> dict[str, Any]:
        if not self.links_path.exists():
            return {"specVersion": 1, "edges": []}
        try:
            return json.loads(self.links_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"specVersion": 1, "edges": []}

    def save_links(self, data: dict[str, Any]) -> None:
        _atomic_write_json(self.links_path, data)

    def upsert_link(
        self,
        *,
        link_type: str,
        from_id: str,
        to_id: str,
        label: str = "",
    ) -> None:
        data = self.load_links()
        edges: list[dict[str, Any]] = list(data.get("edges") or [])
        for edge in edges:
            if (
                edge.get("type") == link_type
                and edge.get("from") == from_id
                and edge.get("to") == to_id
                and (label == "" or edge.get("label") == label)
            ):
                return
        edges.append(
            {
                "id": new_link_id(),
                "type": link_type,
                "from": from_id,
                "to": to_id,
                "label": label,
                "createdAt": _now_iso(),
            }
        )
        data["edges"] = edges
        self.save_links(data)

    def _title_from_path(self, rel_path: str) -> str:
        stem = Path(rel_path).stem
        if stem == "original":
            return Path(rel_path).parent.name
        return stem

    def register_file(
        self,
        rel_path: str,
        *,
        doc_id: str | None = None,
        kind: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        rel = rel_path.replace("\\", "/")
        abs_path = self.resolve(rel)
        if not abs_path.is_file():
            raise FileNotFoundError(rel)
        detected = kind or detect_kind(rel)
        if not detected:
            raise ValueError(f"无法识别文档类型: {rel}")
        if detected == "markdown" and is_derivation_path(rel):
            detected = "markdown"
        now = _now_iso()
        doc_id = doc_id or new_doc_id()
        sidecar: dict[str, Any] = {
            "id": doc_id,
            "kind": detected,
            "path": rel,
            "contentHash": content_hash(abs_path),
            "title": title or self._title_from_path(rel),
            "createdAt": now,
            "updatedAt": now,
            "status": "active",
            "metadata": {},
            "derivations": {},
            "ui": {"icon": "📄", "pinned": False},
        }
        self.save_document(sidecar)
        return sidecar

    def _index_sidecars_by_path(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for doc in self.list_documents():
            path = doc.get("path")
            if path:
                out[str(path).replace("\\", "/")] = doc
        return out

    def _index_sidecars_by_hash(self) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for doc in self.list_documents():
            h = doc.get("contentHash")
            if not h:
                continue
            out.setdefault(h, []).append(doc)
        return out

    def scan(self, *, full: bool = False) -> dict[str, Any]:
        """Scan workspace files and reconcile sidecars."""
        del full  # reserved for future incremental modes
        by_path = self._index_sidecars_by_path()
        by_hash = self._index_sidecars_by_hash()
        seen_paths: set[str] = set()
        registered = 0
        moved = 0
        missing = 0
        orphans = 0

        for abs_path in self.iter_candidate_files():
            rel = self.rel_path(abs_path)
            kind = detect_kind(rel)
            if not kind:
                continue
            if kind == "markdown" and is_derivation_path(rel):
                if rel not in by_path:
                    orphans += 1
                continue
            seen_paths.add(rel)
            try:
                file_hash = content_hash(abs_path)
            except OSError:
                continue

            existing = by_path.get(rel)
            if existing:
                changed = False
                if existing.get("contentHash") != file_hash:
                    existing["contentHash"] = file_hash
                    changed = True
                if existing.get("status") != "active":
                    existing["status"] = "active"
                    changed = True
                if changed:
                    self.save_document(existing)
                continue

            relocated = None
            for candidate in by_hash.get(file_hash, []):
                old_path = str(candidate.get("path") or "").replace("\\", "/")
                if old_path and old_path not in seen_paths:
                    old_abs = self.root / old_path
                    if not old_abs.exists():
                        relocated = candidate
                        break

            if relocated:
                relocated["path"] = rel
                relocated["contentHash"] = file_hash
                relocated["status"] = "active"
                self.save_document(relocated)
                by_path[rel] = relocated
                moved += 1
                continue

            if rel not in by_path:
                doc = self.register_file(rel, kind=kind)
                by_path[rel] = doc
                by_hash.setdefault(file_hash, []).append(doc)
                registered += 1

        for path_key, doc in list(by_path.items()):
            if path_key in seen_paths:
                continue
            abs_old = self.root / path_key
            if abs_old.exists():
                continue
            if doc.get("status") != "missing":
                doc["status"] = "missing"
                self.save_document(doc)
                missing += 1

        manifest = self.load_manifest()
        manifest["lastScanAt"] = _now_iso()
        self.save_manifest(manifest)

        return {
            "registered": registered,
            "moved": moved,
            "missing": missing,
            "orphanDerivations": orphans,
            "total": len(self.list_documents()),
        }

    def parsed_md_path(self, source_rel: str) -> str:
        manifest = self.load_manifest()
        naming = manifest.get("derivationNaming") or {}
        template = naming.get("parsedMd") or "{basename}.parsed.md"
        basename = Path(source_rel).stem
        name = template.replace("{basename}", basename)
        return str(Path(source_rel).with_name(name)).replace("\\", "/")

    def info(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        return {
            "id": manifest.get("id"),
            "name": manifest.get("name") or self.root.name,
            "root": str(self.root),
            "specVersion": manifest.get("specVersion", WORKSPACE_SPEC_VERSION),
            "openedAt": manifest.get("openedAt"),
            "lastScanAt": manifest.get("lastScanAt"),
            "documentCount": len(self.list_documents()),
        }


def get_active_workspace() -> Workspace | None:
    with _active_lock:
        return _active_workspace


def set_active_workspace(ws: Workspace | None) -> None:
    global _active_workspace
    with _active_lock:
        _active_workspace = ws


def open_workspace(path: str | Path, *, scan: bool = True) -> Workspace:
    ws = Workspace.open(path, init=True)
    manifest = ws.load_manifest()
    touch_recent_workspace(ws.root, name=str(manifest.get("name") or ws.root.name))
    set_last_workspace_path(ws.root)
    set_active_workspace(ws)
    if scan:
        ws.scan(full=True)
    return ws


def require_active_workspace() -> Workspace:
    ws = get_active_workspace()
    if ws is None:
        raise RuntimeError("未打开工作空间")
    return ws
