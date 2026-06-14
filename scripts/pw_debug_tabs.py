"""Playwright debug script: open http://localhost:8765/ in a real Chromium
browser, click around, and dump the actual DOM state so we can see whether
the library-view / AI chat panel is correctly hidden on settings + article
tabs, and whether the tab content cache works.

Run from the repo root:
    python scripts/pw_debug_tabs.py
"""
from __future__ import annotations
import json
import sys
import time
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [pageerror] {err}"))

        print("=== 1. open the page ===")
        page.goto("http://localhost:8765/?v=" + str(int(time.time())), wait_until="networkidle")
        page.wait_for_selector("#tab-bar", timeout=10000)
        time.sleep(1)  # let TabManager init

        def snapshot(label: str) -> dict:
            data = page.evaluate("""() => {
                const $ = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    const cs = getComputedStyle(el);
                    return {display: cs.display, visibility: cs.visibility, classes: el.className};
                };
                return {
                    body: { classes: document.body.className, dataset: {...document.body.dataset} },
                    tabBarTabs: [...document.querySelectorAll('.tab-item')].map(t => ({id: t.id, text: t.textContent.trim().slice(0, 40), active: t.classList.contains('active')})),
                    activeTabId: window.TabManager ? TabManager.activeTabId : 'no TabManager',
                    libraryView: $('#library-view'),
                    globalChatPanel: $('#global-chat-panel'),
                    chatPanel: $('#chat-column'),
                    settingsView: $('#iframe-settings'),
                    readerView: $('#reader-view'),
                };
            }""")
            print(f"\n--- snapshot: {label} ---")
            print(json.dumps(data, indent=2, ensure_ascii=False))
            return data

        snap1 = snapshot("home (initial)")

        print("\n=== 2. click settings button to open settings tab ===")
        page.evaluate("() => { if (typeof showSettings === 'function') showSettings(); }")
        time.sleep(0.5)
        snap2 = snapshot("after settings button click")
        lv_visible = page.evaluate("() => getComputedStyle(document.getElementById('library-view')).display !== 'none'")
        chat_visible = page.evaluate("() => getComputedStyle(document.getElementById('chat-column')).display !== 'none'")
        print(f"\nRESULT on settings tab: library-view visible={lv_visible}, chat-panel visible={chat_visible}")

        print("\n=== 3. go back to home, open first article ===")
        page.evaluate("() => { if (typeof TabManager !== 'undefined') TabManager.activateTab('home'); }")
        time.sleep(0.5)
        snapshot("back to home")

        article_id = page.evaluate("""() => {
            const cards = document.querySelectorAll('[onclick*="openReader"]');
            if (cards.length) {
                const m = (cards[0].getAttribute('onclick') || '').match(/openReader\\(['\"]([^'\"]+)['\"]\\)/);
                if (m) return m[1];
            }
            return null;
        }""")
        print(f"  first article id: {article_id}")
        if not article_id:
            browser.close(); return 1

        page.evaluate(f"() => openReader('{article_id}')")
        time.sleep(1.5)
        snap3 = snapshot(f"article {article_id} (first open)")

        lv_visible = page.evaluate("() => getComputedStyle(document.getElementById('library-view')).display !== 'none'")
        chat_visible = page.evaluate("() => getComputedStyle(document.getElementById('chat-column')).display !== 'none'")
        reader_visible = page.evaluate("() => getComputedStyle(document.getElementById('reader-view')).display !== 'none'")
        print(f"\nRESULT on article tab: library-view visible={lv_visible}, chat-panel visible={chat_visible}, reader-view visible={reader_visible}")

        other_id = page.evaluate(f"""() => {{
            const cards = document.querySelectorAll('[onclick*="openReader"]');
            for (const c of cards) {{
                const m = (c.getAttribute('onclick') || '').match(/openReader\\(['\"]([^'\"]+)['\"]\\)/);
                if (m && m[1] !== '{article_id}') return m[1];
            }}
            return null;
        }}""")
        print(f"  second article id: {other_id}")
        if not other_id:
            browser.close(); return 1

        print(f"\n=== 4. open second article {other_id} ===")
        page.evaluate(f"() => openReader('{other_id}')")
        time.sleep(1.5)
        snap4 = snapshot(f"second article {other_id}")

        print("\n=== 5. check window globals + tab data BEFORE switching back ===")
        cur_now = page.evaluate("() => ({raw: (currentRawMd || '').length, trans: (translatedMd || '').length, sum: (summaryText || '').length})")
        print(f"  current reader state lengths: {cur_now}")
        tab1_data = page.evaluate(f"""() => {{
            const t = TabManager.tabs.find(t => t.id === 'article-{article_id}');
            return t ? {{
                currentRawMd_len: (t.data.currentRawMd || '').length,
                translatedMd_len: (t.data.translatedMd || '').length,
                summaryText_len: (t.data.summaryText || '').length,
                scrollTop: t.data.scrollTop || 0,
            }} : null;
        }}""")
        print(f"  tab1.data (article-1): {tab1_data}")

        print(f"\n=== 6. switch back to first article (should hit cache) ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_id}')")
        time.sleep(0.5)
        snap5 = snapshot(f"back to first article (cache hit expected)")

        md_html_len = page.evaluate("() => document.getElementById('md-content').innerHTML.length")
        md_loading = page.evaluate("() => getComputedStyle(document.getElementById('md-loading')).display")
        print(f"\nCache hit check: md-content html length = {md_html_len}, loading spinner display = {md_loading}")

        page.screenshot(path="scripts/pw_debug_state.png", full_page=False)
        print("\nscreenshot saved to scripts/pw_debug_state.png")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
