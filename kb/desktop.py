import sys
import os

_SELF_DIR = os.path.dirname(os.path.abspath(__file__))

# In PyInstaller frozen bundles, kb/ modules are registered as `kb.serve`,
# `kb.llm_config`, etc., but the source uses bare imports like
# `from llm_config import ...`. Pre-alias them in dependency order.
if getattr(sys, 'frozen', False):
    _aliases = [
        ('kb.utils_yaml',     'utils_yaml'),
        ('kb.llm_config',     'llm_config'),
        ('kb.db_api',         'db_api'),
        ('kb.db_index',       'db_index'),
        ('kb.document_info',  'document_info'),
        ('kb.translate',      'translate'),
        ('kb.calibrate',      'calibrate'),
        ('kb.library_chat',   'library_chat'),
        ('kb.engines',        'engines'),
        ('kb.serve',          'serve'),
    ]
    for _fq_name, _alias in _aliases:
        try:
            _mod = __import__(_fq_name, fromlist=[_alias])
            sys.modules[_alias] = _mod
        except Exception:
            pass

    # Help pythonnet find the .NET runtime inside PyInstaller bundles
    _dotnet_root = os.environ.get('DOTNET_ROOT', '')
    if not _dotnet_root or not os.path.isdir(_dotnet_root):
        for _c in [r'C:\Program Files\dotnet', r'C:\Program Files (x86)\dotnet']:
            if os.path.isdir(_c):
                os.environ['DOTNET_ROOT'] = _c
                break

if _SELF_DIR not in sys.path:
    sys.path.insert(0, _SELF_DIR)

# Redirect stdout/stderr to a log file so we don't crash in windowless mode
log_path = os.path.join(_SELF_DIR, 'app.log')
try:
    sys.stdout = sys.stderr = open(log_path, 'a', encoding='utf-8')
except Exception:
    pass

import time
import webview
import ctypes

# Use the aliased serve module (frozen) or bare import (dev mode)
serve_mod = sys.modules.get('serve')
if serve_mod is None:
    import serve as _serve
    serve_mod = _serve
start_server = serve_mod.start_server
PORT = serve_mod.PORT

try:
    # Tell Windows this is a separate app, not a generic Python process
    myappid = 'kbase.desktop.app.1'
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
except Exception:
    pass

def main():
    print("Starting Knowledge Base server...")
    httpd = start_server()

    ts = int(time.time())
    url = f"http://localhost:{PORT}/?v={ts}"
    print(f"Opening desktop window at {url}")

    class Api:
        def save_file(self, content, suggested_name):
            try:
                result = webview.windows[0].create_file_dialog(
                    webview.SAVE_DIALOG, directory='', save_filename=suggested_name)
                if result:
                    with open(result[0], 'wb') as f:
                        f.write(content.encode('utf-8'))
                    return True
            except Exception as e:
                return str(e)
            return False

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
        icon_path = os.path.join(os.path.dirname(__file__), 'assets', 'kbase-logo.ico')
        webview.start(private_mode=False, debug=False, icon=icon_path)
    except Exception as e:
        print(f"Webview error: {e}")
    finally:
        print("\nShutting down...")
        httpd.shutdown()
        print("Server stopped.")

if __name__ == "__main__":
    main()
