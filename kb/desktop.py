"""Desktop app launcher for Knowledge Base — starts server + opens app window."""
import time
import webview
from serve import start_server, PORT

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
        # Start the pywebview event loop. This blocks until the window is closed.
        webview.start(private_mode=False, debug=False)
    except Exception as e:
        print(f"Webview error: {e}")
    finally:
        print("\nShutting down...")
        httpd.shutdown()
        print("Server stopped.")

if __name__ == "__main__":
    main()
