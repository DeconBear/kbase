import time
import webview
import ctypes
import sys
import os
from serve import start_server, PORT

# Redirect stdout/stderr to a log file so we don't crash in windowless mode
log_path = os.path.join(os.path.dirname(__file__), 'app.log')
try:
    sys.stdout = sys.stderr = open(log_path, 'a', encoding='utf-8')
except Exception:
    pass

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
    
    # Create the webview window
    class Api:
        def save_file(self, content, suggested_name):
            try:
                import webview
                result = webview.windows[0].create_file_dialog(webview.SAVE_DIALOG, directory='', save_filename=suggested_name)
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
        import os
        icon_path = os.path.join(os.path.dirname(__file__), 'assets', 'kbase-logo.ico')
        # Start the pywebview event loop. This blocks until the window is closed.
        webview.start(private_mode=False, debug=False, icon=icon_path)
    except Exception as e:
        print(f"Webview error: {e}")
    finally:
        print("\nShutting down...")
        httpd.shutdown()
        print("Server stopped.")

if __name__ == "__main__":
    main()
