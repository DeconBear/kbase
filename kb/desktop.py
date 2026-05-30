"""Desktop app launcher for Knowledge Base — starts server + opens app window."""
import time
import webview
import ctypes
from serve import start_server, PORT

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
    window = webview.create_window(
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
