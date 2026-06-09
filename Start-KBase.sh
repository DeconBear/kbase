#!/usr/bin/env bash
# Start-KBase.sh
# Linux/macOS launcher for KBase.
# Mirrors the role of Start-KBase.vbs on Windows.
#
# Usage:
#   chmod +x Start-KBase.sh
#   ./Start-KBase.sh
#
# What it does:
#   1. Verifies Python 3.10+ is available
#   2. Creates local.env from local.env.example on first run
#   3. Starts the HTTP server on port 8765
#   4. Opens the app in the default browser
#   5. On Ctrl+C, shuts the server down cleanly

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# --- 1. Python check ---
PY="${PYTHON:-python3}"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "❌ python3 not found. Install Python 3.10+ first." >&2
    exit 1
fi
PY_VERSION="$($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
echo "🐍 Using Python $PY_VERSION at $(command -v $PY)"

# --- 2. local.env bootstrap ---
if [ ! -f local.env ] && [ -f local.env.example ]; then
    echo "📝 First run: copying local.env.example → local.env"
    cp local.env.example local.env
    echo "   ⚠️  Edit local.env to add your LLM API key before using AI features."
fi

# --- 3. Start server in background ---
KB_PORT="${KB_PORT:-8765}"
echo "🚀 Starting KBase server on port $KB_PORT …"

$PY scripts/serve-headless.py > /tmp/kbase.log 2>&1 &
SERVER_PID=$!
echo "   server PID: $SERVER_PID"

# Wait for the server to be ready (max 15s)
READY=0
for _ in $(seq 1 30); do
    if curl -fsS "http://localhost:$KB_PORT/api/llm-config" -o /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "$READY" -eq 0 ]; then
    echo "❌ Server failed to start. Last log lines:"
    tail -20 /tmp/kbase.log
    kill "$SERVER_PID" 2>/dev/null || true
    exit 1
fi

# --- 4. Open browser ---
URL="http://localhost:$KB_PORT/?v=$(date +%s)"
echo "✅ Server ready"
echo "🌐 Opening $URL"

if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
elif command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
elif command -v gio >/dev/null 2>&1; then
    gio open "$URL" >/dev/null 2>&1 || true
else
    echo "   (no xdg-open / open / gio found; please open the URL manually)"
fi

# --- 5. Wait for Ctrl+C ---
cleanup() {
    echo ""
    echo "🛑 Shutting down …"
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
    echo "👋 Bye"
    exit 0
}
trap cleanup INT TERM

echo ""
echo "Press Ctrl+C to stop the server."
wait "$SERVER_PID"
