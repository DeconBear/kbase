#!/usr/bin/env python3
"""Headless launcher for kbase on Linux.

Mirrors what kb/desktop.py does, but skips the pywebview GUI.
Usage: python3 scripts/serve-headless.py
"""
import os
import sys
import time
import signal

KB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'kb')
KB_DIR = os.path.abspath(KB_DIR)

# desktop.py pattern: prepend kb/ to sys.path so the bare imports in
# serve.py / llm_config.py / db_api.py work.
if KB_DIR not in sys.path:
    sys.path.insert(0, KB_DIR)

from serve import start_server  # noqa: E402

if __name__ == '__main__':
    httpd = start_server()
    print(f' ✅ KBase running on http://localhost:{httpd.server_address[1]}')
    print(' Press Ctrl+C to stop')

    def shutdown(*_):
        print('\n Shutting down…')
        httpd.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    while True:
        time.sleep(1)
