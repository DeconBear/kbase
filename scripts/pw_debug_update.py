"""Playwright debug: verify the auto-update flow.

Steps:
  1. Open the app.
  2. Open the settings modal, click on the "软件更新" pane.
  3. Click "检查更新" to force a fresh check.
  4. Verify that the status text shows the current/latest version, and
     that if an update is available, the "立即更新" button is shown
     (and the "手动下载" link points at the assetUrl).
  5. Test /api/apply-update with a fake URL and confirm the
     bootstrap script is written + the endpoint returns ok.

Run from the repo root:
    python scripts/pw_debug_update.py
"""
from __future__ import annotations
import sys
import time
import urllib.request
import urllib.error
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    # First: hit the endpoint directly to confirm the API contract.
    print("=== 1. /api/check-update (direct curl) ===")
    req = urllib.request.Request("http://localhost:8765/api/check-update?force=1")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.URLError as e:
        print(f"  /api/check-update failed: {e}")
        return 1
    for k in ("current", "latest", "hasUpdate", "assetUrl", "installedBuild",
              "installerUrl", "portableUrl", "releaseUrl"):
        v = data.get(k)
        if isinstance(v, str) and len(v) > 100:
            v = v[:100] + "..."
        print(f"  {k:20s} = {v}")

    print("\n=== 2. open the app in a browser and check the UI ===")
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_context(viewport={"width": 1400, "height": 900}).new_page()
        page.on("console", lambda m: print(f"  [console.{m.type}] {m.text}"))
        page.on("pageerror", lambda e: print(f"  [pageerror] {e}"))
        page.goto("http://localhost:8765/?v=" + str(int(time.time())), wait_until="networkidle")
        page.wait_for_selector("#tab-bar", timeout=10000)
        time.sleep(4)  # wait for auto-update check

        # Open settings
        page.evaluate("() => { if (typeof showSettings === 'function') showSettings(); }")
        time.sleep(0.5)

        # Force a check
        page.evaluate("() => { if (typeof checkUpdate === 'function') checkUpdate(); }")
        time.sleep(2)

        # Read the update pane state
        state = page.evaluate("""() => {
            const btn = document.getElementById('updateBtn');
            const link = document.getElementById('updateManualLink');
            return {
                btnVisible: btn ? getComputedStyle(btn).display !== 'none' : null,
                btnText: btn ? btn.textContent.trim() : null,
                linkVisible: link ? getComputedStyle(link).display !== 'none' : null,
                linkHref: link ? link.href : null,
                status: document.getElementById('updateStatus').textContent,
                currentVer: document.getElementById('updateCurrentVer').textContent,
            };
        }""")
        print(f"  state: {json.dumps(state, indent=2, ensure_ascii=False)}")

        # Trigger /api/apply-update (don't actually download — just check it returns ok)
        if state.get("btnVisible"):
            print("\n=== 3. /api/apply-update (synthetic, won't really run installer) ===")
            r = page.evaluate("""async () => {
                try {
                    const r = await fetch('/api/apply-update', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({assetUrl: 'http://localhost:8765/api/llm-config'})
                    });
                    return {ok: r.ok, status: r.status, body: await r.text()};
                } catch (e) { return {error: e.message}; }
            }""")
            print(f"  result: {r}")

        page.screenshot(path="scripts/pw_update_debug.png", full_page=False)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
