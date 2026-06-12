"""Smoke test for the SiYuan-style notes refactor.

Drives the KBase web UI (served at http://localhost:8765) using
Playwright's Chromium, asserts the new 4-column layout, multi-tab
editing, block-anchor sidebar items, and the gutter that should
appear when the cursor hovers over a block.

Captures a screenshot at every meaningful step into ./scripts/smoke/
so we can eyeball whether the rebuild actually rendered.
"""
from playwright.sync_api import sync_playwright
import sys, time, os, io

# Force UTF-8 stdout so the Chinese characters we print don't break
# the default Windows GBK encoding.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

URL = "http://localhost:8765/"
OUT = os.path.join(os.path.dirname(__file__), "smoke")
os.makedirs(OUT, exist_ok=True)


def shot(page, name):
    p = os.path.join(OUT, f"{name}.png")
    page.screenshot(path=p, full_page=True)
    print(f"  📸 {p}")


def main():
    fail = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("pageerror", lambda e: print(f"  ⚠ pageerror: {e.message}"))
        page.on("console", lambda m: m.type == "error" and print(f"  ⚠ console.error: {m.text}"))

        try:
            print("→ open shell")
            page.goto(URL, wait_until="domcontentloaded")
            # Force-dismiss any leftover dialog overlay so a stale
            # modal from a prior test run doesn't block clicks.
            page.evaluate(
                "() => { const o = document.getElementById('ui-dialog-overlay'); if (o) o.classList.remove('active'); }"
            )
            page.wait_for_timeout(1500)
            shot(page, "01-load")

            print("→ open notes view")
            # The library topbar has "📝 笔记" (button.onclick=switchToNotesView);
            # the reader sidebar has #rbNotes (toggleNotes for the floating
            # note window). We want switchToNotesView, which is line 793.
            page.locator("button.ctrl-btn:has-text('📝 笔记')").first.click(timeout=5000)
            page.wait_for_selector("#notes-view.active", timeout=8000)
            page.wait_for_timeout(1500)
            shot(page, "02-notes-view")

            # Verify the new 4-column structure.
            for col in ["#notes-notebooks-pane", "#notes-docs-pane",
                        "#notes-editor-column", "#notes-right-panel"]:
                if not page.locator(col).is_visible():
                    fail.append(f"missing column: {col}")
            print(f"  columns: " + ", ".join(
                f"{'✓' if page.locator(c).is_visible() else '✗'} {c}"
                for c in ["#notes-notebooks-pane", "#notes-docs-pane",
                          "#notes-editor-column", "#notes-right-panel"]))

            # Verify notebooks tree has at least Inbox.
            nb_count = page.locator("#notebooks-tree .notebook-row").count()
            print(f"  notebooks visible: {nb_count}")
            if nb_count < 1:
                fail.append("no notebooks in left tree")

            # Verify the 4-tab right panel exists.
            for t in ["backlinks", "mentions", "outline", "tags"]:
                if not page.locator(f"button.notes-right-tab[data-tab='{t}']").is_visible():
                    fail.append(f"missing right tab: {t}")

            print("→ create a new notebook")
            page.locator("#notes-notebooks-header button.icon-btn").click()
            page.wait_for_timeout(300)
            # The SPA uses a custom #ui-dialog-overlay + #ui-dialog-input
            # for prompts. Drive it directly.
            page.wait_for_selector("#ui-dialog-overlay.active", timeout=2000)
            page.fill("#ui-dialog-input", "PW Test Notebook")
            shot(page, "03a-prompt-open")
            page.click("#ui-dialog-ok")
            page.wait_for_timeout(800)
            shot(page, "03-new-notebook")

            # Re-fetch the notebooks tree and ensure 'PW Test' is in it.
            nb_titles = page.locator("#notebooks-tree .notebook-row").all_text_contents()
            print(f"  notebooks in tree: {[t.strip()[:30] for t in nb_titles]}")
            if not any("PW Test" in t for t in nb_titles):
                fail.append("PW Test notebook not visible in tree")

            print("→ switch back to Inbox")
            page.locator("#notebooks-tree .notebook-row", has_text="Inbox").first.click()
            page.wait_for_timeout(500)
            shot(page, "04-inbox-selected")

            print("→ create a new note")
            page.locator("#notes-docs-header button.icon-btn").click()
            page.wait_for_timeout(1000)
            shot(page, "05-new-note")
            # The new note should be open in a tab.
            tabs = page.locator("#notes-tabs-bar .notes-tab").count()
            print(f"  tabs visible: {tabs}")
            if tabs < 1:
                fail.append("no tab appeared after creating a note")

            print("→ switch to outline tab and type content")
            page.locator("button.notes-right-tab[data-tab='outline']").click()
            page.wait_for_timeout(200)
            # Type into the editor (Vditor contenteditable). Vditor
            # mounts asynchronously: the IR DOM may not be present for
            # a few hundred ms after the tab is opened. Wait for it.
            editor = page.locator("#notes-editor-container .vditor-ir").first
            try:
                editor.wait_for(state="visible", timeout=8000)
            except Exception:
                fail.append("Vditor IR editor not mounted after createNote")
                shot(page, "06a-no-ir")
                raise
            # Vditor's IR.setValue also needs vditor.lute to be defined
            # (it dispatches to lute.Md2VditorIRDOM). Wait for that
            # readiness signal too.
            try:
                page.wait_for_function(
                    "() => notesEditor && notesEditor.vditor && notesEditor.vditor.lute",
                    timeout=8000,
                )
            except Exception as e:
                fail.append(f"editor not ready before setValue: {e}")
                shot(page, "06a-editor-not-ready")
                raise
            editor.click()
            page.keyboard.press("Control+Home")
            # Use a paste / insert_value style via JS so we can
            # write multi-line content with H1-H3 headings.
            page.evaluate("""(md) => {
                const ed = notesEditor;
                if (ed) { ed.setValue(md); }
            }""",
            "# Project Notes\n\nIntro paragraph.\n\n## Goals\n\n- Fast\n- Reliable\n\n## Architecture\n\n### Backend\n\nFastAPI\n\n### Frontend\n\nVditor")
            # Trigger save.
            page.keyboard.press("Control+s")
            page.wait_for_timeout(1500)
            shot(page, "06-content-typed")

            # Verify outline tab now shows 3 headings.
            page.locator("button.notes-right-tab[data-tab='outline']").click()
            page.wait_for_timeout(400)
            outline_items = page.locator("#notes-right-pane-outline .note-outline-item").count()
            print(f"  outline items: {outline_items}")
            if outline_items < 3:
                fail.append(f"expected ≥3 outline items, got {outline_items}")
            shot(page, "07-outline")

            print("→ switch to backlinks tab")
            page.locator("button.notes-right-tab[data-tab='backlinks']").click()
            page.wait_for_timeout(300)
            shot(page, "08-backlinks")

            print("→ switch to tags tab")
            page.locator("button.notes-right-tab[data-tab='tags']").click()
            page.wait_for_timeout(300)
            shot(page, "09-tags")

            print("→ hover block in editor to summon gutter")
            # The gutter reads block-anchor markers from the saved
            # markdown. Write content + save first so the markers are
            # in the file the next time the editor loads it.
            page.locator("button.notes-right-tab[data-tab='outline']").click()
            page.wait_for_timeout(200)
            # Wait for the editor (which is the same instance after the
            # first setValue + save) to still be ready.
            try:
                page.wait_for_function(
                    "() => notesEditor && notesEditor.vditor && notesEditor.vditor.lute",
                    timeout=8000,
                )
            except Exception as e:
                fail.append(f"editor not ready before second setValue: {e}")
                shot(page, "10a-editor-not-ready")
                raise
            # Type H1 + paragraph directly via Vditor's setValue, then
            # save. The server rewrites the file with <!--kb-block:...-->
            # markers. saveCurrentNote then mirrors the annotated
            # content back into tab.lastContent, so the gutter's source
            # scanner can find anchors without re-mounting the editor.
            page.evaluate("""(md) => {
                if (notesEditor) notesEditor.setValue(md);
            }""", "# Project Notes\n\nIntro paragraph.\n\n## Goals\n\n- Fast\n- Reliable")
            # Re-focus the editor — the outline tab click above may have
            # stolen focus, in which case Ctrl+S would not be caught by
            # handleEditorKeydown.
            page.locator("#notes-editor-container .vditor-ir").first.click()
            page.wait_for_timeout(100)
            page.keyboard.press("Control+s")
            # Wait for both: the 1.5s debounced save AND the network round-trip.
            page.wait_for_timeout(2500)
            # Verify the server-annotated content is now in tab.lastContent
            # (this is the precondition for the gutter's marker scan to
            # succeed).
            marker_state = page.evaluate("""() => {
                const t = currentNoteId && _tabFor ? _tabFor(currentNoteId) : null;
                return {
                    hasTab: !!t,
                    hasMarker: t ? /<!--kb-block:/.test(t.lastContent || '') : false,
                    lastContentSlice: t ? (t.lastContent || '').slice(0, 200) : null
                };
            }""")
            print(f"  tab.lastContent has markers: {marker_state['hasMarker']}")
            if not marker_state['hasMarker']:
                fail.append("tab.lastContent still missing block markers after save")
                shot(page, "10a-no-markers")
                raise Exception("no markers in tab.lastContent")

            editor = page.locator("#notes-editor-container .vditor-ir").first
            try:
                editor.wait_for(state="visible", timeout=5000)
            except Exception:
                shot(page, "10b-no-ir")
                fail.append("Vditor IR did not re-render after re-mount")
                raise
            # Move the mouse directly over an H1/H2 heading element (the
            # only blocks for which the gutter emits an anchor). P blocks
            # and empty lines have no anchor and the gutter stays hidden.
            gutter_visible = False
            heading_loc = page.locator("#notes-editor-container [data-block] h1, #notes-editor-container [data-block] h2, #notes-editor-container [data-block] h3").first
            if heading_loc.count() > 0:
                heading_box = heading_loc.bounding_box()
                if heading_box:
                    page.mouse.move(
                        heading_box['x'] + heading_box['width'] / 2,
                        heading_box['y'] + heading_box['height'] / 2,
                    )
                    # The mouseover handler runs synchronously and sets
                    # display:flex; check immediately, then again after
                    # a short delay to absorb the 200ms hide-timer.
                    page.wait_for_timeout(80)
                    gutter_visible = page.locator("#kb-block-gutter").is_visible()
                    print(f"  gutter visible after hover: {gutter_visible}")
            if not gutter_visible:
                # Fall back to a sequence of synthetic positions so the
                # failure message is useful.
                editor_box = editor.bounding_box()
                if editor_box:
                    for (dx, dy) in [(60, 40), (60, 80), (60, 120), (60, 180), (60, 240)]:
                        page.mouse.move(editor_box['x'] + dx, editor_box['y'] + dy)
                        page.wait_for_timeout(80)
                        if page.locator("#kb-block-gutter").is_visible():
                            gutter_visible = True
                            print(f"  fallback: gutter appeared at ({dx},{dy})")
                            break
            shot(page, "10-gutter-hover")
            if not gutter_visible:
                fail.append("gutter did not appear on block hover")

            print("→ click gutter 🔖 button")
            # Grant clipboard read permission for the test.
            try:
                ctx.grant_permissions(["clipboard-read", "clipboard-write"])
            except Exception as e:
                print(f"  (clipboard perm grant failed: {e})")
            # Re-summon the gutter: clicking via Playwright moves the
            # mouse over the button (and off the heading), so the
            # mouseleave handler may have hidden the gutter by the time
            # the AI button click runs. We dispatch a hover before each
            # click to keep it visible.
            heading_loc2 = page.locator("#notes-editor-container [data-block] h1, #notes-editor-container [data-block] h2, #notes-editor-container [data-block] h3").first
            if heading_loc2.count() > 0:
                hb = heading_loc2.bounding_box()
                if hb:
                    page.mouse.move(hb['x'] + hb['width']/2, hb['y'] + hb['height']/2)
                    page.wait_for_timeout(100)
            try:
                page.locator("#kb-block-gutter button[data-act='copy']").click(timeout=2000)
                page.wait_for_timeout(300)
                shot(page, "11-after-copy")
            except Exception as e:
                fail.append(f"could not click copy button: {e}")

            print("→ click gutter 🤖 button → AI menu")
            # Re-summon the gutter (the previous click may have hidden
            # it via mouseleave), then click the AI button.
            if heading_loc2.count() > 0:
                hb = heading_loc2.bounding_box()
                if hb:
                    page.mouse.move(hb['x'] + hb['width']/2, hb['y'] + hb['height']/2)
                    page.wait_for_timeout(150)
            try:
                page.locator("#kb-block-gutter button[data-act='ai']").click(timeout=2000)
                page.wait_for_timeout(400)
                ai_menu_visible = page.locator("#ai-context-menu").is_visible()
                print(f"  AI menu visible: {ai_menu_visible}")
                shot(page, "12-ai-menu")
                if not ai_menu_visible:
                    fail.append("AI menu did not appear")
            except Exception as e:
                fail.append(f"AI button click failed: {e}")

            print("→ close AI menu and create a second note for tab test")
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            page.locator("#notes-docs-header button.icon-btn").click()
            page.wait_for_timeout(1000)
            tabs2 = page.locator("#notes-tabs-bar .notes-tab").count()
            print(f"  tabs after 2nd note: {tabs2}")
            if tabs2 < 2:
                fail.append("multi-tab: 2nd note didn't create a 2nd tab")
            shot(page, "13-two-tabs")

            print("→ right-click a tab → context menu")
            try:
                page.locator("#notes-tabs-bar .notes-tab").first.click(button="right")
                page.wait_for_timeout(300)
                menu_visible = page.locator(".note-context-menu").is_visible()
                print(f"  tab context menu visible: {menu_visible}")
                shot(page, "14-tab-context-menu")
            except Exception as e:
                fail.append(f"tab right-click: {e}")

            # Cleanup: delete the test notebook + its notes.
            print("→ cleanup")
            page.evaluate("""async () => {
                const r = await fetch('/api/notebooks');
                const d = await r.json();
                for (const nb of (d.notebooks || [])) {
                    if (nb.name === 'PW Test Notebook' || nb.name === 'PW Test') {
                        await fetch('/api/notebooks/' + encodeURIComponent(nb.id), {method:'DELETE'});
                    }
                }
                // Also clean up any notes named 'Untitled' that are empty.
                const n = await fetch('/api/notes');
                const nd = await n.json();
                for (const note of (nd.notes || [])) {
                    if (note.title === 'Untitled' && (note.id || '').startsWith('note_')) {
                        try { await fetch('/api/notes/' + encodeURIComponent(note.id), {method:'DELETE'}); } catch(e) {}
                    }
                }
            }""")
            # If a confirm dialog popped up during cleanup, dismiss it.
            if page.locator("#ui-dialog-overlay.active").count():
                try: page.click("#ui-dialog-ok", timeout=1000)
                except Exception: pass

        finally:
            ctx.close()
            browser.close()

    print()
    if fail:
        print(f"❌ {len(fail)} failure(s):")
        for f in fail:
            print(f"   - {f}")
        return 1
    print("✅ all smoke checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
