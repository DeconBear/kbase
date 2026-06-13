"""Playwright debug using Microsoft Edge (closer to pywebview's WebView2
than Chromium). The user's pywebview session uses Edge WebView2 which
shares the Edge HTML rendering engine.

Run from the repo root:
    python scripts/pw_debug_edge.py
"""
from __future__ import annotations
import sys
import time
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    with sync_playwright() as pw:
        # Try Edge first (closer to WebView2). Fall back to Chromium.
        browser = None
        try:
            browser = pw.chromium.launch(headless=True, channel="msedge")
            print("  using Microsoft Edge (msedge channel)")
        except Exception as e:
            print(f"  Edge not available: {e}; falling back to chromium")
            browser = pw.chromium.launch(headless=True)

        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [pageerror] {err}"))

        page.goto("http://localhost:8765/?v=" + str(int(time.time())), wait_until="networkidle")
        page.wait_for_selector("#tab-bar", timeout=10000)
        time.sleep(1)

        article_ids = page.evaluate("""() => {
            const cards = document.querySelectorAll('[onclick*="openReader"]');
            const seen = new Set();
            const ids = [];
            for (const c of cards) {
                const m = (c.getAttribute('onclick') || '').match(/openReader\\(['\"]([^'\"]+)['\"]\\)/);
                if (m && !seen.has(m[1]) && ids.length < 3) {
                    seen.add(m[1]);
                    ids.push(m[1]);
                }
            }
            return ids;
        }""")
        print(f"  unique article ids: {article_ids}")
        if len(article_ids) < 2:
            print("  need at least 2 articles")
            browser.close(); return 1

        def state(label):
            s = page.evaluate("""() => {
                const $ = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return {present: false, display: '(detached)', inline: '(detached)'};
                    return {present: true, display: getComputedStyle(el).display, inline: el.style.display};
                };
                const pdf = document.getElementById('pdf-embed');
                return {
                    activeTabId: window.TabManager && window.TabManager.activeTabId,
                    pdfSrc: pdf ? pdf.src : null,
                    chat: $('main#global-chat-panel'),
                    library: $('div#library-view'),
                    bodyClass: document.body.className,
                    bodyDataTab: document.body.dataset.tab || '(none)',
                };
            }""")
            print(f"  [{label}] active={s['activeTabId']!r}")
            print(f"    bodyClass={s['bodyClass']!r}  dataTab={s['bodyDataTab']!r}")
            print(f"    pdf.src     = {s['pdfSrc']}")
            print(f"    chat        present={s['chat']['present']}  display={s['chat']['display']}  inline={s['chat']['inline']}")
            print(f"    library     present={s['library']['present']}  display={s['library']['display']}  inline={s['library']['inline']}")

        for i, aid in enumerate(article_ids[:2]):
            print(f"\n=== open article {chr(ord('A')+i)}: {aid} ===")
            page.evaluate(f"() => openReader('{aid}')")
            time.sleep(1.5)
            state(f"after opening {chr(ord('A')+i)}")

        print(f"\n=== switch back to A ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[0]}')")
        time.sleep(0.5)
        state("after switching to A")

        print(f"\n=== switch to B ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[1]}')")
        time.sleep(0.5)
        state("after switching to B")

        print(f"\n=== open settings ===")
        page.evaluate("() => { if (typeof showSettings === 'function') showSettings(); }")
        time.sleep(0.5)
        state("after settings")

        print(f"\n=== back to home ===")
        page.evaluate("() => { if (typeof TabManager !== 'undefined') TabManager.activateTab('home'); }")
        time.sleep(0.5)
        state("back to home (library should be visible)")

        page.screenshot(path="scripts/pw_edge_debug.png", full_page=True)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
