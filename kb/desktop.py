"""KBase desktop entry point.

Launches the local HTTP server inside a pywebview window. In PyInstaller
bundles, kb modules are pre-aliased so source-style bare imports still work.
"""
from __future__ import annotations

import base64
import os
import sys
import traceback

# When the executable is built as a windowed application (PyInstaller
# console=False) or when running via pythonw.exe, there is no console
# and sys.stdout / sys.stderr are None.  Any print() call before we
# redirect them to the app log would crash with an AttributeError.
# Stub them with a null device EARLY so module-level imports that
# happen to print won't blow up.
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")

# ---------- crash log for frozen builds (console flashes too fast) ----------
if getattr(sys, "frozen", False):
    _CRASH_LOG = os.path.join(os.path.dirname(sys.executable), "kbase_crash.log")
    def _excepthook(exc_type, exc_value, exc_tb):
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            with open(_CRASH_LOG, "w", encoding="utf-8") as f:
                f.write(msg)
        except Exception:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

# In PyInstaller frozen bundles the bare imports used throughout ``kb/``
# (``import storage``, ``from llm_config import ...``) cannot find the
# top-level names, because PyInstaller only registers them as
# ``kb.storage`` etc. Pre-alias them BEFORE the first import.
if getattr(sys, "frozen", False):
    _aliases = [
        ("kb.utils_yaml", "utils_yaml"),
        ("kb.storage", "storage"),
        ("kb.engines._paths", "engines._paths"),
        ("kb.llm_config", "llm_config"),
        ("kb.calibrate", "calibrate"),
        ("kb.translate", "translate"),
        ("kb.document_info", "document_info"),
        ("kb.engines", "engines"),
        ("kb.library_chat", "library_chat"),
        ("kb.database", "database"),
        ("kb.version", "version"),
        ("kb.updater", "updater"),
        ("kb.serve", "serve"),
        ("kb.app_config", "app_config"),
        ("kb.bookmarks", "bookmarks"),
        ("kb.word_extract", "word_extract"),
        ("kb.workspace_watch", "workspace_watch"),
        ("kb.workspace", "workspace"),
        ("kb.derivations", "derivations"),
        ("kb.workspace_paths", "workspace_paths"),
        ("kb.workspace_search", "workspace_search"),
        ("kb.legacy_bridge", "legacy_bridge"),
        ("kb.workspace_index", "workspace_index"),
        ("kb.literature_classify", "literature_classify"),
        ("kb.workspace_ingest", "workspace_ingest"),
        ("kb.literature_organize", "literature_organize"),
        ("kb.article_folder_classify", "article_folder_classify"),
        ("kb.cli", "cli"),
    ]
    for _fq_name, _alias in _aliases:
        try:
            _mod = __import__(_fq_name, fromlist=[_alias])
            sys.modules[_alias] = _mod
        except Exception:
            pass

    # Help pythonnet find the .NET runtime inside PyInstaller bundles
    _dotnet_root = os.environ.get("DOTNET_ROOT", "")
    if not _dotnet_root or not os.path.isdir(_dotnet_root):
        for _c in (r"C:\Program Files\dotnet", r"C:\Program Files (x86)\dotnet"):
            if os.path.isdir(_c):
                os.environ["DOTNET_ROOT"] = _c
                break

# Make sure the bundled kb package directory is importable in source mode.
_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

import storage
from storage import DATA_ROOT, LOGS_DIR

storage.ensure_directories()
storage.load_local_env()

# Redirect stdout/stderr to a log file in the data dir so windowless mode
# does not crash and PyInstaller temp cleanup does not eat the log.
log_path = str(LOGS_DIR / "app.log")
try:
    sys.stdout = sys.stderr = open(log_path, "a", encoding="utf-8")
except Exception:
    pass

import ctypes
import time
import webview

serve_mod = sys.modules.get("serve")
if serve_mod is None:
    import serve as serve_mod  # type: ignore
start_server = serve_mod.start_server
PORT = serve_mod.PORT


def _set_app_user_model_id() -> None:
    if not sys.platform.startswith("win"):
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("kbase.desktop.app.1")
    except Exception:
        pass


def main() -> None:
    print("Starting Knowledge Base server...")
    httpd = start_server()

    _set_app_user_model_id()
    ts = int(time.time())
    url = f"http://localhost:{PORT}/?v={ts}"
    print(f"Opening desktop window at {url}")

    class Api:
        def _save_bytes(self, content: bytes, suggested_name: str):
            result = webview.windows[0].create_file_dialog(
                webview.SAVE_DIALOG, directory="", save_filename=suggested_name
            )
            if not result:
                return False
            target = result[0]
            temp_target = f"{target}.kbase-tmp-{os.getpid()}"
            try:
                with open(temp_target, "wb") as file:
                    file.write(content)
                os.replace(temp_target, target)
            finally:
                if os.path.exists(temp_target):
                    os.unlink(temp_target)
            return True

        def save_file(self, content: str, suggested_name: str):
            try:
                return self._save_bytes(content.encode("utf-8"), suggested_name)
            except Exception as exc:  # noqa: BLE001
                return str(exc)

        def save_file_base64(self, content: str, suggested_name: str):
            try:
                return self._save_bytes(base64.b64decode(content, validate=True), suggested_name)
            except Exception as exc:  # noqa: BLE001
                return str(exc)

        def quit_app(self) -> None:
            """Close the application window — used by the auto-updater flow."""
            try:
                webview.windows[0].destroy()
            except Exception:
                pass

        def pick_folder(self, initial_dir: str = ""):
            """Open a native folder picker (Windows/macOS/Linux)."""
            try:
                directory = initial_dir if initial_dir and os.path.isdir(initial_dir) else ""
                result = webview.windows[0].create_file_dialog(
                    webview.FOLDER_DIALOG,
                    directory=directory,
                )
                if result:
                    return result[0]
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}
            return None

    window = webview.create_window(
        js_api=Api(),
        title="Knowledge Base",
        url=url,
        width=1280,
        height=800,
        min_size=(800, 600),
        text_select=False,
        zoomable=False,
    )

    try:
        icon_path = os.path.join(_SELF_DIR, "assets", "kbase-logo.ico")
        webview.start(private_mode=False, debug=False, icon=icon_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Webview error: {exc}")
    finally:
        print("\nShutting down...")
        httpd.shutdown()
        print("Server stopped.")


if __name__ == "__main__":
    main()
