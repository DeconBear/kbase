#!/usr/bin/env python3
"""Headless launcher for kbase on Linux/macOS.

Mirrors what kb/desktop.py does, but skips the pywebview GUI.
Use this when you want a pure HTTP server (e.g. for headless testing,
remote access, or systems where WebView2/WebKit2GTK is unavailable).

Usage:
    python3 scripts/serve-headless.py
    # or
    ./Start-KBase.sh
"""
import os
import sys
import time
import signal

# Inject kb/ into sys.path so the bare imports inside kb/serve.py
# (and its siblings) resolve. This is what kb/desktop.py does too.
KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb')
KB_DIR = os.path.abspath(KB_DIR)
if KB_DIR not in sys.path:
    sys.path.insert(0, KB_DIR)

from serve import start_server  # noqa: E402

if __name__ == '__main__':
    httpd = start_server()
    print(' Press Ctrl+C to stop')

    def shutdown(*_):
        print('\n Shutting down…')
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        time.sleep(1)
