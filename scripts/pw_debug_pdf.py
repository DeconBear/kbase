"""Playwright debug: open multiple article tabs (unique ids), switch
between them, and dump the actual #pdf-embed.src + #global-chat-panel
state after each switch.

Run from the repo root:
    python scripts/pw_debug_pdf.py
"""
from __future__ import annotations
import sys
import time
from playwright.sync_api import sync_playwright

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def main() -> int:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1400, "height": 900})
        page = ctx.new_page()
        page.on("console", lambda msg: print(f"  [console.{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"  [pageerror] {err}"))

        page.goto("http://localhost:8765/?v=" + str(int(time.time())), wait_until="networkidle")
        page.wait_for_selector("#tab-bar", timeout=10000)
        time.sleep(1)

        # Get unique article ids (filter duplicates)
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
        print(f"  detected UNIQUE article ids: {article_ids}")
        if len(article_ids) < 2:
            print("  need at least 2 unique articles")
            browser.close(); return 1

        def state(label):
            s = page.evaluate("""() => {
                const pdf = document.querySelector('.article-pdf-embed.active');
                return {
                    activeTabId: window.TabManager && window.TabManager.activeTabId,
                    pdfSrc: pdf ? pdf.src : null,
                    pdfArticleId: pdf ? pdf.dataset.articleId : null,
                    pdfFrameCount: document.querySelectorAll('.article-pdf-embed').length,
                    pdfDisplay: pdf ? getComputedStyle(pdf).display : null,
                    chatDisplay: getComputedStyle(document.getElementById('chat-column')).display,
                    libraryDisplay: getComputedStyle(document.getElementById('library-view')).display,
                    bodyClass: document.body.className,
                };
            }""")
            print(f"  [{label}] active={s['activeTabId']!r} bodyClass={s['bodyClass']!r}")
            print(f"    pdf.article= {s['pdfArticleId']}  frames={s['pdfFrameCount']}")
            print(f"    pdf.src    = {s['pdfSrc']}")
            print(f"    pdf.display= {s['pdfDisplay']}  chat.display= {s['chatDisplay']}  lib.display= {s['libraryDisplay']}")

        for i, aid in enumerate(article_ids[:2]):
            label = chr(ord('A') + i)
            print(f"\n=== open article {label}: {aid} ===")
            page.evaluate(f"() => openReader('{aid}')")
            time.sleep(1.5)
            state(f"after opening {label}")

        # Switch back to A
        print(f"\n=== switch back to A ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[0]}')")
        time.sleep(0.5)
        state("after switching to A")

        # Switch to B
        print(f"\n=== switch to B ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[1]}')")
        time.sleep(0.5)
        state("after switching to B")

        # Open third article to see if it gets the right PDF
        if len(article_ids) >= 3:
            print(f"\n=== open article C: {article_ids[2]} ===")
            page.evaluate(f"() => openReader('{article_ids[2]}')")
            time.sleep(1.5)
            state("after opening C")

        # Settings tab
        print(f"\n=== open settings ===")
        page.evaluate("() => { if (typeof showSettings === 'function') showSettings(); }")
        time.sleep(0.5)
        state("after settings")

        page.screenshot(path="scripts/pw_pdf_debug.png", full_page=False)
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
