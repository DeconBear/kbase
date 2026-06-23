"""KBase Linux/macOS desktop launcher — uses PNG icon to avoid .ico loading errors on GTK."""
from __future__ import annotations

import os
import sys
import time

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))
if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

import storage
from storage import DATA_ROOT, LOGS_DIR

storage.ensure_directories()
storage.load_local_env()

# Don't redirect stdout/stderr so we can see issues
import webview

serve_mod = sys.modules.get("serve")
if serve_mod is None:
    import serve as serve_mod  # type: ignore
start_server = serve_mod.start_server
PORT = serve_mod.PORT


def main() -> None:
    print("Starting Knowledge Base server...", flush=True)
    httpd = start_server()
    ts = int(time.time())
    url = f"http://localhost:{PORT}/?v={ts}"
    print(f"Opening desktop window at {url}", flush=True)

    class Api:
        def save_file(self, content: str, suggested_name: str):
            try:
                result = webview.windows[0].create_file_dialog(
                    webview.SAVE_DIALOG, directory="", save_filename=suggested_name
                )
                if result:
                    with open(result[0], "wb") as f:
                        f.write(content.encode("utf-8"))
                    return True
            except Exception as exc:  # noqa: BLE001
                return str(exc)
            return False

        def quit_app(self) -> None:
            try:
                webview.windows[0].destroy()
            except Exception:
                pass

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

    # Use PNG icon on Linux/macOS (ICO is Windows-only and uses PNG compression
    # which GTK's gdk-pixbuf can't load).
    icon_path = os.path.join(_SELF_DIR, "assets", "kbase-logo-256.png")
    if not sys.platform.startswith("win"):
        # Windows uses .ico
        win_icon = os.path.join(_SELF_DIR, "assets", "kbase-logo.ico")
        if os.path.isfile(win_icon):
            icon_path = win_icon
            # but .ico with PNG compression fails on GTK; prefer PNG fallback
            png_icon = os.path.join(_SELF_DIR, "assets", "kbase-logo-256.png")
            if os.path.isfile(png_icon):
                icon_path = png_icon

    try:
        # debug=True on Linux to surface WebKit issues; debug=False on Windows
        dbg = not sys.platform.startswith("win")
        webview.start(private_mode=False, debug=dbg, icon=icon_path)
    except Exception as exc:  # noqa: BLE001
        print(f"Webview error: {exc}", flush=True)
    finally:
        print("\nShutting down...", flush=True)
        httpd.shutdown()
        print("Server stopped.", flush=True)


if __name__ == "__main__":
    main()