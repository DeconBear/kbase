"""Desktop app launcher for Knowledge Base — starts server + opens Chrome in app mode."""
import subprocess
import time
import threading

from serve import start_server, PORT


def find_chrome():
    import os
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def main():
    chrome = find_chrome()
    if not chrome:
        print("Chrome/Edge not found. Opening in default browser instead.")
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")
        print("Press Enter to stop server...")
        input()
        return

    print("Starting Knowledge Base server...")
    httpd = start_server()

    ts = int(time.time())
    print(f"Opening desktop window at http://localhost:{PORT}")
    proc = subprocess.Popen(
        [chrome, f"--app=http://localhost:{PORT}/?v={ts}", "--new-window"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    try:
        print("Desktop app running. Close the window or press Ctrl+C to stop.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        print("\nShutting down...")
        proc.terminate()
        proc.wait()
        httpd.shutdown()
        print("Server stopped.")


if __name__ == "__main__":
    main()
