"""Workspace kernel — open local directories, scan documents, manage .kbase sidecars."""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from app_config import (
    clear_last_workspace_if,
    load_recent_workspaces,
    remove_recent_workspace,
    set_last_workspace_path,
    touch_recent_workspace,
)
from storage import _atomic_write_json

WORKSPACE_SPEC_VERSION = 1
DOC_ID_RE = re.compile(r"^doc_[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
SOURCE_ID_RE = re.compile(r"^src_[0-9a-f]{12}$")
SOURCE_PATH_PREFIX = "@sources"

DEFAULT_IGNORE_GLOBS = (
    "**/.git/**",
    "**/node_modules/**",
    "**/.kbase/**",
    "**/__pycache__/**",
    "**/.venv/**",
    "**/venv/**",
    "local.env",
    "llm_config.json",
    "low_memory_config.json",
    "chat_sessions/**",
    "logs/**",
)

KIND_BY_EXT: dict[str, str] = {
    ".md": "markdown",
    ".markdown": "markdown",
    ".pdf": "pdf",
    ".docx": "word",
    ".html": "html",
    ".htm": "html",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".txt": "text",
    ".log": "text",
    ".json": "json",
    ".csv": "csv",
    ".tsv": "csv",
    ".bib": "text",
    ".tex": "text",
    ".py": "code",
    ".js": "code",
    ".ts": "code",
    ".tsx": "code",
    ".jsx": "code",
    ".css": "code",
    ".rs": "code",
    ".go": "code",
    ".java": "code",
    ".c": "code",
    ".cpp": "code",
    ".h": "code",
    ".hpp": "code",
    ".r": "code",
    ".m": "code",
    ".sh": "code",
    ".yaml": "code",
    ".yml": "code",
    ".toml": "code",
    ".ini": "code",
    ".xml": "code",
    ".sql": "code",
    ".ipynb": "code",
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


def _derivation_basename(source_rel: str) -> str:
    path = Path(source_rel.replace("\\", "/"))
    if path.stem.lower() == "original" and path.parent.name:
        return path.parent.name
    return path.stem


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


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
        self.managed_files_dir = self.root / "managed-files"
        self.managed_inbox_dir = self.managed_files_dir / "inbox"
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
            self.managed_inbox_dir,
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
                "literatureDir": "literature",
                "literatureLayout": "per-paper-folder",
                "managedFilesDir": "managed-files",
                "sources": [],
                "ingestOnOpen": True,
                "autoClassifyPdfs": True,
                "autoExtractMetadata": True,
                "classifyUseLlm": "uncertain_only",
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
        # On Windows, Path.resolve() may yield \\?\ extended paths while
        # self.root stays as a normal path — normalize both before relative_to.
        target = abs_path.resolve()
        candidates = [(None, self.root), *[(source["id"], Path(source["path"])) for source in self.sources()]]
        for source_id, candidate_root in candidates:
            candidate_root = candidate_root.resolve()
            try:
                relative = target.relative_to(candidate_root).as_posix()
            except ValueError:
                root_s = os.path.normcase(os.path.normpath(str(candidate_root)))
                target_s = os.path.normcase(os.path.normpath(str(target)))
                if root_s.startswith("\\\\?\\"):
                    root_s = root_s[4:]
                if target_s.startswith("\\\\?\\"):
                    target_s = target_s[4:]
                try:
                    relative = Path(target_s).relative_to(Path(root_s)).as_posix()
                except ValueError:
                    continue
            if source_id:
                return f"{SOURCE_PATH_PREFIX}/{source_id}/{relative}"
            return relative
        raise ValueError("File is outside the workspace and linked sources")

    def resolve(self, rel_path: str) -> Path:
        rel = rel_path.replace("\\", "/").lstrip("/")
        source_path = self._split_source_path(rel)
        if source_path:
            source_id, source_rel = source_path
            source = self._source_by_id(source_id)
            if source is None or not source_rel:
                raise ValueError("Invalid source path")
            root = Path(source["path"]).resolve()
            target = (root / source_rel).resolve()
        else:
            root = self.root.resolve()
            target = (root / rel).resolve()
        root_s = os.path.normcase(os.path.normpath(str(root)))
        target_s = os.path.normcase(os.path.normpath(str(target)))
        if root_s.startswith("\\\\?\\"):
            root_s = root_s[4:]
        if target_s.startswith("\\\\?\\"):
            target_s = target_s[4:]
        try:
            common = os.path.commonpath([root_s, target_s])
        except ValueError as exc:
            raise ValueError("Path escapes workspace") from exc
        if common != root_s:
            raise ValueError("路径越界")
        return target

    def ignore_globs(self) -> tuple[str, ...]:
        manifest = self.load_manifest()
        globs = [*DEFAULT_IGNORE_GLOBS, *(manifest.get("ignoreGlobs") or [])]
        return tuple(dict.fromkeys(str(g) for g in globs))

    def literature_dir_name(self) -> str:
        return str(self.load_manifest().get("literatureDir") or "articles").strip("/") or "articles"

    def sources(self) -> list[dict[str, Any]]:
        """Return configured external folders without modifying their contents."""
        raw_sources = self.load_manifest().get("sources") or []
        result: list[dict[str, Any]] = []
        for raw in raw_sources:
            if not isinstance(raw, dict):
                continue
            source_id = str(raw.get("id") or "")
            path_text = str(raw.get("path") or "").strip()
            if not SOURCE_ID_RE.match(source_id) or not path_text:
                continue
            root = Path(path_text).expanduser().resolve()
            result.append({
                **raw,
                "id": source_id,
                "name": str(raw.get("name") or root.name or source_id),
                "path": str(root),
                "mode": "linked",
                "readOnly": True,
                "available": root.is_dir(),
            })
        return result

    def _source_by_id(self, source_id: str) -> dict[str, Any] | None:
        return next((source for source in self.sources() if source["id"] == source_id), None)

    @staticmethod
    def _split_source_path(rel_path: str) -> tuple[str, str] | None:
        rel = rel_path.replace("\\", "/").strip("/")
        parts = rel.split("/", 2)
        if len(parts) >= 2 and parts[0] == SOURCE_PATH_PREFIX and SOURCE_ID_RE.match(parts[1]):
            return parts[1], parts[2] if len(parts) == 3 else ""
        return None

    def is_readonly_path(self, rel_path: str) -> bool:
        return self._split_source_path(rel_path) is not None

    def add_source(self, path: str | Path, *, name: str | None = None) -> dict[str, Any]:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"资料源目录不存在: {root}")
        if _path_is_within(root, self.root) or _path_is_within(self.root, root):
            raise ValueError("资料源不能与工作空间互相包含；工作空间内文件已自动管理")
        root_key = os.path.normcase(os.path.normpath(str(root)))
        for source in self.sources():
            if os.path.normcase(os.path.normpath(source["path"])) == root_key:
                return source
        source = {
            "id": f"src_{uuid.uuid4().hex[:12]}",
            "name": (name or root.name or "资料源").strip(),
            "path": str(root),
            "mode": "linked",
            "readOnly": True,
            "addedAt": _now_iso(),
            "documentCount": 0,
        }
        manifest = self.load_manifest()
        manifest["sources"] = [*(manifest.get("sources") or []), source]
        self.save_manifest(manifest)
        return {**source, "available": True}

    def remove_source(self, source_id: str) -> dict[str, Any]:
        if not SOURCE_ID_RE.match(source_id):
            raise ValueError("无效的资料源 ID")
        manifest = self.load_manifest()
        sources = [source for source in (manifest.get("sources") or []) if isinstance(source, dict)]
        removed = next((source for source in sources if source.get("id") == source_id), None)
        if removed is None:
            raise ValueError("资料源不存在")
        manifest["sources"] = [source for source in sources if source.get("id") != source_id]
        self.save_manifest(manifest)
        prefix = f"{SOURCE_PATH_PREFIX}/{source_id}/"
        removed_docs = 0
        for doc in self.list_documents():
            if str(doc.get("path") or "").replace("\\", "/").startswith(prefix):
                try:
                    self.sidecar_path(str(doc.get("id") or "")).unlink(missing_ok=True)
                    removed_docs += 1
                except (OSError, ValueError):
                    continue
        self._refresh_document_count()
        return {"source": removed, "removedDocuments": removed_docs, "filesDeleted": False}

    def import_managed_file(self, rel_path: str) -> dict[str, Any]:
        """Atomically copy a linked file into the managed inbox."""
        if not self.is_readonly_path(rel_path):
            raise ValueError("只有外部资料源文件需要复制到托管区")
        source = self.resolve(rel_path)
        if not source.is_file():
            raise FileNotFoundError(rel_path)
        self.managed_inbox_dir.mkdir(parents=True, exist_ok=True)
        destination = self.managed_inbox_dir / source.name
        index = 2
        while destination.exists():
            destination = self.managed_inbox_dir / f"{source.stem} ({index}){source.suffix}"
            index += 1
        tmp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copy2(source, tmp)
            os.replace(tmp, destination)
        finally:
            tmp.unlink(missing_ok=True)
        rel = self.rel_path(destination)
        detected = detect_kind(rel)
        doc = self.register_file(rel, kind=detected) if detected else None
        if doc:
            self._refresh_document_count()
        return {"path": rel, "document": doc, "sourcePath": rel_path, "copied": True}

    def iter_candidate_files(self) -> Iterator[Path]:
        globs = self.ignore_globs()
        roots = [self.root, *(Path(source["path"]) for source in self.sources() if source.get("available"))]
        for root in roots:
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                rel = self.rel_path(path)
                local_rel = rel.split("/", 2)[-1] if self.is_readonly_path(rel) else rel
                if ".kbase" in Path(local_rel).parts:
                    continue
                if _matches_any_glob(local_rel, globs):
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
        stat = abs_path.stat()
        now = _now_iso()
        doc_id = doc_id or new_doc_id()
        sidecar: dict[str, Any] = {
            "id": doc_id,
            "kind": detected,
            "path": rel,
            "contentHash": content_hash(abs_path),
            "fileSize": stat.st_size,
            "mtimeNs": stat.st_mtime_ns,
            "title": title or self._title_from_path(rel),
            "createdAt": now,
            "updatedAt": now,
            "status": "active",
            "metadata": {},
            "derivations": {},
            "ui": {"icon": "📄", "pinned": False},
        }
        source_path = self._split_source_path(rel)
        if source_path:
            sidecar["sourceId"] = source_path[0]
            sidecar["sourceReadOnly"] = True
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
        del full  # the stat cache is safe for both normal and explicit scans
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
            existing = by_path.get(rel)
            try:
                stat = abs_path.stat()
                unchanged = bool(
                    existing
                    and existing.get("fileSize") == stat.st_size
                    and existing.get("mtimeNs") == stat.st_mtime_ns
                )
                if unchanged:
                    if existing.get("status") != "active":
                        existing["status"] = "active"
                        self.save_document(existing)
                    continue
                file_hash = content_hash(abs_path)
            except OSError:
                continue

            if existing:
                changed = False
                if existing.get("contentHash") != file_hash:
                    existing["contentHash"] = file_hash
                    changed = True
                if existing.get("status") != "active":
                    existing["status"] = "active"
                    changed = True
                if existing.get("fileSize") != stat.st_size:
                    existing["fileSize"] = stat.st_size
                    changed = True
                if existing.get("mtimeNs") != stat.st_mtime_ns:
                    existing["mtimeNs"] = stat.st_mtime_ns
                    changed = True
                if changed:
                    self.save_document(existing)
                continue

            relocated = None
            for candidate in by_hash.get(file_hash, []):
                old_path = str(candidate.get("path") or "").replace("\\", "/")
                if old_path and old_path not in seen_paths:
                    try:
                        old_abs = self.resolve(old_path)
                    except ValueError:
                        old_abs = self.root / "__missing_source__"
                    if not old_abs.exists():
                        relocated = candidate
                        break

            if relocated:
                relocated["path"] = rel
                relocated["contentHash"] = file_hash
                relocated["fileSize"] = stat.st_size
                relocated["mtimeNs"] = stat.st_mtime_ns
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
            try:
                abs_old = self.resolve(path_key)
            except ValueError:
                abs_old = self.root / "__missing_source__"
            if abs_old.exists():
                continue
            if doc.get("status") != "missing":
                doc["status"] = "missing"
                self.save_document(doc)
                missing += 1

        # Count sidecar files by name only — never parse tens of thousands of JSON
        # docs here (Baidu Sync / large workspaces would block for minutes).
        try:
            total = sum(1 for _ in self.documents_dir.glob("doc_*.json")) if self.documents_dir.exists() else 0
        except OSError:
            total = 0
        manifest = self.load_manifest()
        manifest["lastScanAt"] = _now_iso()
        manifest["documentCount"] = total
        source_counts: dict[str, int] = {}
        for rel in seen_paths:
            source_path = self._split_source_path(rel)
            if source_path:
                source_counts[source_path[0]] = source_counts.get(source_path[0], 0) + 1
        updated_sources = []
        for source in manifest.get("sources") or []:
            if not isinstance(source, dict):
                continue
            item = dict(source)
            item["documentCount"] = source_counts.get(str(item.get("id") or ""), 0)
            item["lastScanAt"] = manifest["lastScanAt"]
            updated_sources.append(item)
        manifest["sources"] = updated_sources
        self.save_manifest(manifest)

        return {
            "registered": registered,
            "moved": moved,
            "missing": missing,
            "orphanDerivations": orphans,
            "total": total,
        }

    def find_document_by_path(self, rel_path: str) -> dict[str, Any] | None:
        rel = rel_path.replace("\\", "/")
        for doc in self.list_documents():
            if str(doc.get("path") or "").replace("\\", "/") == rel:
                return doc
        return None

    def parsed_md_path(self, source_rel: str) -> str:
        manifest = self.load_manifest()
        naming = manifest.get("derivationNaming") or {}
        template = naming.get("parsedMd") or "{basename}.parsed.md"
        basename = _derivation_basename(source_rel)
        name = template.replace("{basename}", basename)
        return str(Path(source_rel).with_name(name)).replace("\\", "/")

    def translated_md_path(self, source_rel: str, lang: str = "zh") -> str:
        manifest = self.load_manifest()
        naming = manifest.get("derivationNaming") or {}
        template = naming.get("translatedMd") or "{basename}.{lang}.md"
        basename = _derivation_basename(source_rel)
        name = template.replace("{basename}", basename).replace("{lang}", lang)
        return str(Path(source_rel).with_name(name)).replace("\\", "/")

    def document_count(self) -> int:
        """Fast document count from manifest cache only (never parse/glob sidecars)."""
        manifest = self.load_manifest()
        cached = manifest.get("documentCount")
        if isinstance(cached, bool):
            return 0
        if isinstance(cached, (int, float)) and cached >= 0:
            return int(cached)
        # Older manifests: avoid blocking on huge Baidu Sync trees; scan() fills this.
        return 0

    def _refresh_document_count(self) -> int:
        try:
            total = sum(1 for _ in self.documents_dir.glob("doc_*.json"))
        except OSError:
            total = 0
        manifest = self.load_manifest()
        manifest["documentCount"] = total
        self.save_manifest(manifest)
        return total

    def info(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        return {
            "id": manifest.get("id"),
            "name": manifest.get("name") or self.root.name,
            "root": str(self.root),
            "specVersion": manifest.get("specVersion", WORKSPACE_SPEC_VERSION),
            "openedAt": manifest.get("openedAt"),
            "lastScanAt": manifest.get("lastScanAt"),
            "documentCount": self.document_count(),
            "literatureDir": manifest.get("literatureDir") or "articles",
            "ingestOnOpen": manifest.get("ingestOnOpen", True),
            "autoClassifyPdfs": manifest.get("autoClassifyPdfs", True),
            "autoExtractMetadata": manifest.get("autoExtractMetadata", True),
            "managedFilesDir": manifest.get("managedFilesDir") or "managed-files",
            "sources": self.sources(),
        }

    def list_directory_tree(
        self,
        *,
        max_depth: int = 48,
        include_derivations: bool = False,
    ) -> dict[str, Any]:
        """Return the workspace filesystem as a nested folder/file tree."""
        globs = self.ignore_globs()
        doc_by_path = {
            str(doc.get("path") or "").replace("\\", "/"): doc
            for doc in self.list_documents()
        }

        def should_skip(rel: str, *, is_dir: bool) -> bool:
            rel = rel.replace("\\", "/").strip("/")
            if not rel:
                return False
            parts = Path(rel).parts
            if ".kbase" in parts:
                return True
            probe = rel if is_dir else rel
            if _matches_any_glob(probe, globs):
                return True
            if _matches_any_glob(f"{probe}/", globs):
                return True
            if not is_dir and not include_derivations and is_derivation_path(rel):
                return True
            return False

        def build_dir(
            abs_dir: Path,
            rel: str,
            depth: int,
            *,
            display_name: str | None = None,
            source_id: str | None = None,
        ) -> dict[str, Any]:
            display = display_name or (self.root.name if not rel else abs_dir.name)
            node: dict[str, Any] = {
                "name": display,
                "path": rel.replace("\\", "/"),
                "type": "dir",
                "children": [],
                "readOnly": source_id is not None,
            }
            if source_id:
                node["sourceId"] = source_id
                node["isSourceRoot"] = rel == f"{SOURCE_PATH_PREFIX}/{source_id}"
            if depth >= max_depth:
                return node
            try:
                entries = sorted(
                    abs_dir.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError:
                return node
            for entry in entries:
                entry_rel = f"{rel}/{entry.name}" if rel else entry.name
                entry_rel = entry_rel.replace("\\", "/")
                if entry.is_dir():
                    if should_skip(entry_rel, is_dir=True):
                        continue
                    node["children"].append(build_dir(
                        entry,
                        entry_rel,
                        depth + 1,
                        source_id=source_id,
                    ))
                    continue
                if not entry.is_file() or should_skip(entry_rel, is_dir=False):
                    continue
                kind = detect_kind(entry_rel) or "file"
                doc = doc_by_path.get(entry_rel)
                file_node: dict[str, Any] = {
                    "name": entry.name,
                    "path": entry_rel,
                    "type": "file",
                    "kind": kind,
                    "readOnly": source_id is not None,
                }
                if source_id:
                    file_node["sourceId"] = source_id
                if doc:
                    file_node["docId"] = doc.get("id")
                    file_node["title"] = doc.get("title")
                node["children"].append(file_node)
            return node

        tree = build_dir(self.root, "", 0)
        for source in self.sources():
            source_rel = f"{SOURCE_PATH_PREFIX}/{source['id']}"
            if not source.get("available"):
                tree["children"].append({
                    "name": source["name"],
                    "path": source_rel,
                    "type": "dir",
                    "children": [],
                    "readOnly": True,
                    "sourceId": source["id"],
                    "isSourceRoot": True,
                    "available": False,
                })
                continue
            tree["children"].append(build_dir(
                Path(source["path"]),
                source_rel,
                0,
                display_name=source["name"],
                source_id=source["id"],
            ))
        return {
            "root": str(self.root),
            "name": self.root.name,
            "tree": tree,
            "sources": self.sources(),
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


def create_workspace(
    path: str | Path,
    *,
    name: str | None = None,
    scan: bool = True,
) -> Workspace:
    """Create a new workspace directory with default kbase layout."""
    root = Path(path).resolve()
    root.mkdir(parents=True, exist_ok=True)
    (root / "literature").mkdir(exist_ok=True)
    (root / "notes").mkdir(exist_ok=True)
    ws = open_workspace(root, scan=scan)
    if name and name.strip():
        manifest = ws.load_manifest()
        manifest["name"] = name.strip()
        ws.save_manifest(manifest)
        touch_recent_workspace(ws.root, name=name.strip())
    return ws


def _is_kbase_workspace_root(root: Path) -> bool:
    if (root / ".kbase" / "workspace.json").is_file():
        return True
    for name in ("notes", "articles", "literature", ".literature"):
        if (root / name).is_dir():
            return True
    return False


def _protected_workspace_roots() -> set[Path]:
    try:
        from storage import REPO_ROOT, default_data_root

        return {
            Path(REPO_ROOT).resolve() / "data",
            default_data_root().resolve(),
            Path.home().resolve(),
            Path.home().resolve() / "Documents",
        }
    except ImportError:
        return set()


def _is_protected_workspace(root: Path) -> bool:
    import os

    key = os.path.normcase(str(root.resolve()))
    return any(os.path.normcase(str(p)) == key for p in _protected_workspace_roots())


def _rmtree_force(root: Path) -> None:
    """Best-effort recursive delete (handles read-only files on Windows)."""
    import os
    import shutil
    import stat

    def _onerror(func, path, exc_info):  # noqa: ANN001
        try:
            os.chmod(path, stat.S_IWRITE)
            func(path)
        except Exception:
            pass

    shutil.rmtree(root, onerror=_onerror)


def _pick_fallback_workspace(exclude: Path) -> Path | None:
    import os

    exclude_key = os.path.normcase(str(exclude.resolve()))
    for item in load_recent_workspaces():
        candidate = Path(str(item.get("path") or "")).resolve()
        if os.path.normcase(str(candidate)) == exclude_key:
            continue
        if candidate.is_dir():
            return candidate
    try:
        from storage import default_data_root

        default = default_data_root().resolve()
        if os.path.normcase(str(default)) != exclude_key and default.is_dir():
            return default
    except ImportError:
        pass
    return None


def destroy_workspace(path: str | Path, *, delete_files: bool = False) -> dict[str, Any]:
    """Remove a workspace from recents; optionally delete its directory."""
    import os

    root = Path(path).resolve()
    active = get_active_workspace()
    was_active = (
        active is not None
        and os.path.normcase(str(active.root.resolve())) == os.path.normcase(str(root))
    )

    removed_from_recents = remove_recent_workspace(root)
    clear_last_workspace_if(root)

    switched_to: str | None = None
    if was_active:
        set_active_workspace(None)
        fallback = _pick_fallback_workspace(root)
        if fallback is not None:
            open_workspace(fallback, scan=True)
            switched_to = str(fallback)
            try:
                from storage import bind_data_root_runtime

                bind_data_root_runtime(fallback)
            except ImportError:
                pass

    deleted_files = False
    notice: str | None = None

    if delete_files:
        if not root.is_dir():
            notice = "工作空间目录已不存在，已从最近列表移除"
        elif _is_protected_workspace(root):
            notice = "默认数据目录受保护，已从最近列表移除（未删除文件）"
        elif not _is_kbase_workspace_root(root):
            raise ValueError("不是有效的 KBase 工作空间")
        else:
            try:
                _rmtree_force(root)
                deleted_files = not root.exists()
                if not deleted_files:
                    notice = "部分文件未能删除，请关闭占用后重试"
            except OSError as exc:
                raise OSError(f"删除文件夹失败: {exc}") from exc

    return {
        "path": str(root),
        "wasActive": was_active,
        "removedFromRecents": removed_from_recents,
        "deletedFiles": deleted_files,
        "switchedTo": switched_to,
        "notice": notice,
    }


def require_active_workspace() -> Workspace:
    ws = get_active_workspace()
    if ws is None:
        raise RuntimeError("未打开工作空间")
    return ws
