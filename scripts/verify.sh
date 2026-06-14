#!/usr/bin/env bash
# verify.sh — End-to-end smoke test for KBase headless mode on Linux/macOS.
#
# Boots the headless server, hits all key API endpoints with curl,
# and uses google-chrome to render + screenshot the SPA.
#
# Verified on Ubuntu 22.04 with Python 3.10.12 and Google Chrome 148.
#
# Usage:
#     bash scripts/verify.sh

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/docs/media"
mkdir -p "$OUT"

cleanup() {
    if [ -n "${SERVER_PID:-}" ]; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait 2>/dev/null || true
    fi
}
trap cleanup EXIT

# 1. Start server
echo "▶ 1. Starting kbase HTTP server in background…"
cd "$ROOT"
python3 scripts/serve-headless.py >/tmp/kbase-server.log 2>&1 &
SERVER_PID=$!
echo "   server PID: $SERVER_PID"

# Wait for the server to start accepting connections (poll via curl)
SERVER_READY=0
for _ in $(seq 1 30); do
    if curl -fsS http://localhost:8765/api/llm-config -o /dev/null 2>&1; then
        SERVER_READY=1
        break
    fi
    sleep 0.5
done
if [ "$SERVER_READY" -eq 0 ]; then
    echo "❌ server did not start within 15s"
    cat /tmp/kbase-server.log
    exit 1
fi
echo "   ✅ server up"

PASS=0
FAIL=0
check() {
    if eval "$1" >/dev/null 2>&1; then
        echo "   ✅ $2"
        PASS=$((PASS+1))
    else
        echo "   ❌ $2"
        FAIL=$((FAIL+1))
    fi
}

echo ""
echo "▶ 2. API endpoint checks"
check "curl -fsS http://localhost:8765/ -o /dev/null" "GET / → 200 (HTML)"
check "curl -fsS http://localhost:8765/api/articles -o /dev/null" "GET /api/articles → 200"
check "curl -fsS http://localhost:8765/api/llm-config -o /dev/null" "GET /api/llm-config → 200"
check "curl -fsS http://localhost:8765/api/notes -o /dev/null" "GET /api/notes → 200"

ART_COUNT=$(curl -fsS http://localhost:8765/api/articles | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('articles', [])))")
echo "   📚 /api/articles has $ART_COUNT entries"
[ "$ART_COUNT" -ge 0 ] && PASS=$((PASS+1)) && echo "   ✅ API responds" || { FAIL=$((FAIL+1)); echo "   ❌ API broken"; }

PROV_COUNT=$(curl -fsS http://localhost:8765/api/llm-config | python3 -c "import json,sys; print(len(json.load(sys.stdin).get('providers', [])))")
echo "   🤖 /api/llm-config has $PROV_COUNT providers"
[ "$PROV_COUNT" -ge 1 ] && PASS=$((PASS+1)) && echo "   ✅ LLM provider configured" || { FAIL=$((FAIL+1)); echo "   ❌ no LLM provider"; }

HTML_SIZE=$(curl -fsS http://localhost:8765/ | wc -c)
echo "   📄 / index HTML: $HTML_SIZE bytes"
[ "$HTML_SIZE" -gt 50000 ] && PASS=$((PASS+1)) && echo "   ✅ SPA HTML loaded" || { FAIL=$((FAIL+1)); echo "   ❌ SPA HTML too small"; }

TITLE=$(curl -fsS http://localhost:8765/ | grep -oE '<title>[^<]+</title>' | head -1)
echo "   🏷  page title: $TITLE"
echo "$TITLE" | grep -qiE "Knowledge Base|KBase" && PASS=$((PASS+1)) && echo "   ✅ title OK" || { FAIL=$((FAIL+1)); echo "   ❌ title missing"; }

echo ""
echo "▶ 3. Headless Chrome SPA render"
if command -v google-chrome >/dev/null 2>&1; then
    google-chrome --headless --disable-gpu --no-sandbox --hide-scrollbars \
        --virtual-time-budget=4000 \
        --window-size=1280,800 \
        --screenshot="$OUT/kbase-home-linux.png" \
        http://localhost:8765/ 2>&1 | tail -1

    if [ -f "$OUT/kbase-home-linux.png" ]; then
        SIZE=$(stat -c%s "$OUT/kbase-home-linux.png")
        echo "   📸 screenshot saved: $OUT/kbase-home-linux.png ($SIZE bytes)"
        [ "$SIZE" -gt 50000 ] && PASS=$((PASS+1)) && echo "   ✅ screenshot OK" || { FAIL=$((FAIL+1)); echo "   ❌ screenshot too small"; }
    else
        FAIL=$((FAIL+1))
        echo "   ❌ screenshot not generated"
    fi
else
    echo "   ⚠️  google-chrome not installed; skipping screenshot step"
fi

echo ""
echo "=============================="
echo "  PASS: $PASS  FAIL: $FAIL"
echo "=============================="
[ "$FAIL" -eq 0 ]
