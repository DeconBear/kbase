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
                if (m && !seen.has(m[1]) && ids.length < 4) {
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

        failures = []

        def state(label):
            s = page.evaluate("""() => {
                const $ = (sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return {present: false, display: '(detached)', inline: '(detached)'};
                    return {present: true, display: getComputedStyle(el).display, inline: el.style.display};
                };
                const pdf = document.querySelector('.article-pdf-embed.active');
                return {
                    activeTabId: window.TabManager && window.TabManager.activeTabId,
                    pdfSrc: pdf ? pdf.src : null,
                    pdfArticleId: pdf ? pdf.dataset.articleId : null,
                    pdfFrameCount: document.querySelectorAll('.article-pdf-embed').length,
                    chat: $('main#global-chat-panel'),
                    articleChat: $('div#chat-column'),
                    library: $('div#library-view'),
                    readerTitle: document.getElementById('readerTitle').textContent,
                    bodyClass: document.body.className,
                    bodyDataTab: document.body.dataset.tab || '(none)',
                };
            }""")
            print(f"  [{label}] active={s['activeTabId']!r}")
            print(f"    bodyClass={s['bodyClass']!r}  dataTab={s['bodyDataTab']!r}")
            print(f"    readerTitle = {s['readerTitle']!r}")
            print(f"    pdf.article = {s['pdfArticleId']!r}  frames={s['pdfFrameCount']}")
            print(f"    pdf.src     = {s['pdfSrc']}")
            print(f"    chat        present={s['chat']['present']}  display={s['chat']['display']}  inline={s['chat']['inline']}")
            print(f"    articleChat present={s['articleChat']['present']}  display={s['articleChat']['display']}")
            print(f"    library     present={s['library']['present']}  display={s['library']['display']}  inline={s['library']['inline']}")
            return s

        for i, aid in enumerate(article_ids[:2]):
            print(f"\n=== open article {chr(ord('A')+i)}: {aid} ===")
            page.evaluate(f"() => openReader('{aid}')")
            time.sleep(1.5)
            current = state(f"after opening {chr(ord('A')+i)}")
            if current["pdfArticleId"] != aid:
                failures.append(f"article {aid}: active PDF belongs to {current['pdfArticleId']}")
            if current["articleChat"]["display"] != "none":
                failures.append(f"article {aid}: AI chat should be hidden by default")

        print(f"\n=== switch back to A ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[0]}')")
        time.sleep(0.5)
        switched_a = state("after switching to A")
        if switched_a["pdfArticleId"] != article_ids[0]:
            failures.append("switch to A selected the wrong cached PDF")

        print(f"\n=== switch to B ===")
        page.evaluate(f"() => TabManager.activateTab('article-{article_ids[1]}')")
        time.sleep(0.5)
        switched_b = state("after switching to B")
        if switched_b["pdfArticleId"] != article_ids[1]:
            failures.append("switch to B selected the wrong cached PDF")
        if switched_b["pdfFrameCount"] < 2:
            failures.append("PDF frames were not retained per article tab")

        print("\n=== enable article chat, then leave the article ===")
        page.evaluate("() => { colVisible.chat = true; applyColumnVisibility(); }")
        time.sleep(0.2)
        chat_enabled = state("article chat explicitly enabled")
        if chat_enabled["articleChat"]["display"] != "flex":
            failures.append("article: AI chat did not open when explicitly enabled")

        print(f"\n=== open settings ===")
        page.evaluate("() => { if (typeof showSettings === 'function') showSettings(); }")
        time.sleep(0.5)
        settings = state("after settings")
        if settings["library"]["display"] != "none":
            failures.append("settings: library view is still visible")
        if settings["articleChat"]["display"] != "none":
            failures.append("settings: article AI chat is still visible")

        print(f"\n=== back to home ===")
        page.evaluate("() => { if (typeof TabManager !== 'undefined') TabManager.activateTab('home'); }")
        time.sleep(0.5)
        home = state("back to home (library should be visible)")
        if not home["chat"]["present"] or home["chat"]["display"] != "flex":
            failures.append("home: global chat panel was lost")
        if home["library"]["display"] != "flex":
            failures.append("home: library view is not visible")

        if len(article_ids) >= 4:
            print("\n=== rapid-open race check on a fresh page ===")
            race_page = ctx.new_page()
            race_page.goto("http://localhost:8765/?race=" + str(int(time.time())), wait_until="networkidle")
            race_page.wait_for_selector("#tab-bar", timeout=10000)
            race_a, race_b = article_ids[2], article_ids[3]
            race_page.evaluate(
                "([a, b]) => { openReader(a); openReader(b); }",
                [race_a, race_b],
            )
            time.sleep(2)
            race_state = race_page.evaluate("""() => ({
                activeTabId: TabManager.activeTabId,
                currentArticleId: currentArticle && currentArticle.id,
                activePdfId: document.querySelector('.article-pdf-embed.active')?.dataset.articleId || null,
                readerTitle: document.getElementById('readerTitle').textContent,
                loading: getComputedStyle(document.getElementById('md-loading')).display,
            })""")
            print(f"  rapid A={race_a}")
            print(f"  rapid B={race_b}")
            print(f"  final state={race_state}")
            if race_state["activeTabId"] != "article-" + race_b:
                failures.append("rapid open: the wrong tab remained active")
            if race_state["currentArticleId"] != race_b:
                failures.append("rapid open: stale async work replaced currentArticle")
            if race_state["activePdfId"] != race_b:
                failures.append("rapid open: stale async work replaced the active PDF")
            race_page.close()

        page.screenshot(path="scripts/pw_edge_debug.png", full_page=True)
        browser.close()
    if failures:
        print("\nFAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("\nAll tab/PDF/chat checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
