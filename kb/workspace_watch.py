"""Optional filesystem watcher for active workspace (watchdog or polling)."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from workspace import Workspace, get_active_workspace

_DEBOUNCE_SEC = 2.0
_POLL_SEC = 30.0


class WorkspaceWatcher:
    def __init__(
        self,
        workspace: Workspace,
        *,
        on_scan: Callable[[dict], None] | None = None,
    ) -> None:
        self._ws = workspace
        self._on_scan = on_scan
        self._stop = threading.Event()
        self._pending = threading.Event()
        self._thread: threading.Thread | None = None
        self._observer = None

    def start(self) -> str:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            return self._start_polling("polling")

        ws_root = str(self._ws.root)
        watcher = self

        class _Handler(FileSystemEventHandler):
            def on_any_event(self, event) -> None:
                if event.is_directory:
                    return
                src = str(event.src_path or "").replace("\\", "/")
                kbase = str(Path(ws_root) / ".kbase").replace("\\", "/")
                if src.startswith(kbase):
                    return
                watcher._pending.set()

        handler = _Handler()
        observer = Observer()
        observer.schedule(handler, ws_root, recursive=True)
        for source in self._ws.sources():
            if source.get("available"):
                observer.schedule(handler, str(source["path"]), recursive=True)
        observer.start()
        self._observer = observer
        self._thread = threading.Thread(target=self._debounce_loop, daemon=True)
        self._thread.start()
        return "watchdog"

    def _start_polling(self, mode: str) -> str:
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
        return mode

    def _debounce_loop(self) -> None:
        while not self._stop.is_set():
            if self._pending.wait(timeout=1.0):
                self._pending.clear()
                time.sleep(_DEBOUNCE_SEC)
                while self._pending.is_set():
                    self._pending.clear()
                    time.sleep(_DEBOUNCE_SEC)
                self._run_scan()

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self._run_scan()
            self._stop.wait(_POLL_SEC)

    def _run_scan(self) -> None:
        ws = get_active_workspace()
        if ws is None or ws.root != self._ws.root:
            return
        try:
            stats = ws.scan(full=False)
        except Exception:
            return
        if self._on_scan:
            try:
                self._on_scan(stats)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._observer is not None:
            try:
                self._observer.stop()
                self._observer.join(timeout=5)
            except Exception:
                pass


_watcher: WorkspaceWatcher | None = None
_watcher_lock = threading.Lock()


def start_workspace_watcher(workspace: Workspace) -> str:
    global _watcher
    with _watcher_lock:
        if _watcher is not None:
            _watcher.stop()
        _watcher = WorkspaceWatcher(workspace)
        return _watcher.start()


def stop_workspace_watcher() -> None:
    global _watcher
    with _watcher_lock:
        if _watcher is not None:
            _watcher.stop()
            _watcher = None
