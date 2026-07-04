"""Application-level configuration (outside any workspace)."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from storage import _atomic_write_json

if sys.platform == "win32":
    _APP_DIR = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "kbase"
else:
    _xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    _APP_DIR = Path(_xdg) / "kbase"

APP_DIR: Path = _APP_DIR
RECENT_WORKSPACES_FILE: Path = APP_DIR / "recent-workspaces.json"
APP_STATE_FILE: Path = APP_DIR / "app.json"


def ensure_app_dir() -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def load_recent_workspaces() -> list[dict[str, Any]]:
    ensure_app_dir()
    if not RECENT_WORKSPACES_FILE.exists():
        return []
    try:
        data = json.loads(RECENT_WORKSPACES_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = data.get("workspaces") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return []
    return [x for x in items if isinstance(x, dict) and x.get("path")]


def save_recent_workspaces(items: list[dict[str, Any]]) -> None:
    ensure_app_dir()
    _atomic_write_json(RECENT_WORKSPACES_FILE, {"workspaces": items})


def touch_recent_workspace(path: str | Path, *, name: str | None = None) -> None:
    root = str(Path(path).resolve())
    items = load_recent_workspaces()
    items = [x for x in items if x.get("path") != root]
    entry: dict[str, Any] = {
        "path": root,
        "name": name or Path(root).name,
        "openedAt": _now_iso(),
    }
    items.insert(0, entry)
    save_recent_workspaces(items[:20])


def load_app_state() -> dict[str, Any]:
    ensure_app_dir()
    if not APP_STATE_FILE.exists():
        return {}
    try:
        data = json.loads(APP_STATE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return data if isinstance(data, dict) else {}


def save_app_state(state: dict[str, Any]) -> None:
    ensure_app_dir()
    _atomic_write_json(APP_STATE_FILE, state)


def get_last_workspace_path() -> str | None:
    state = load_app_state()
    path = (state.get("lastWorkspace") or "").strip()
    if path and Path(path).is_dir():
        return path
    recent = load_recent_workspaces()
    for item in recent:
        p = (item.get("path") or "").strip()
        if p and Path(p).is_dir():
            return p
    return None


def set_last_workspace_path(path: str | Path) -> None:
    state = load_app_state()
    state["lastWorkspace"] = str(Path(path).resolve())
    save_app_state(state)
