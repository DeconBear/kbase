# v0.2.0 — SiYuan-style notes + 文章小记

Big release focused on the notes module. Brings the reader-view notes
and the floating article notes ("文章小记") up to the SiYuan-style
interaction model: block-anchor hover gutter, slash-command palette,
selection toolbar, article ↔ note linking, and a not-freezing editor.

## What's new

- **文章小记 (article-scoped notes)** — A docked panel inside the
  article reader that lists every note that references the paper,
  either via `notes.article_id` (a scoped "小记") or a
  `[[art-link:<id>]]` mention. Renamed from "笔记" to "文章小记",
  and shows a count badge on the toolbar.
- **Block-anchor hover gutter** — Hovering an H1-H3 block surfaces a
  small toolbar (🔖 copy block ref / 🤖 block-level AI). Anchors are
  stable markdown markers (`<!--kb-block:slug-->`) injected by the
  server on save, kept hidden in the rendered DOM via CSS so users
  never see them.
- **Slash-command palette in the notes editor** — Type `/` in an empty
  line / after a list marker to open a categorized command palette
  (18 items across 标题 / 基础 / 代码与公式 / 媒体 / 笔记). Supports
  English and IME input (Chinese pinyin). Arrow keys navigate one
  item at a time, Enter picks, Escape closes.
- **`@` mention of notes, 小记, and articles** — Type `@` to list
  every notebook note, every article-scoped note, and every article.
  Selecting an article inserts a stable `[[art-link:<id>]]` link so the
  note shows up under that article's notes tab. The same link index
  powers the article notes count badge.
- **Text-selection toolbar in the article reader** — Selecting text
  in the markdown / translation panes surfaces a floating toolbar
  with highlight (yellow) / underline / strikethrough / copy /
  "加入小记" (creates a scoped note containing the quoted snippet +
  `[[art-link:<id>]]` citation marker). Highlights persist in
  localStorage and re-apply after re-render.
- **Richer emoji panel** — The notes editor's stock emoji button
  now opens an 8-category picker (表情 / 手势 / 人物 / 动物 / 食物 /
  活动 / 旅行 / 符号, 600+ emojis) with a search box.

## Bug fixes

- Slash and `@` command picks no longer freeze the editor.
  Replaced the broken `document.execCommand('delete')` + `insertValue`
  combo (which corrupted Vditor IR's selection / block state) with
  a single splice on the markdown source captured before the menu
  opened, followed by `setValue` + `setSelection`.
- `withMdMode` no longer calls Vditor 3.9.6's nonexistent
  `isWysiwygMode()` / `changeMode()`; it now just hides the editor
  briefly around the callback.
- Slash-menu arrow keys navigate one item at a time. The two
  capture-phase keydown listeners that both routed to
  `handleSlashMenuKeydown` were collapsed into one.
- The floating-note editor was rendering on a black canvas because
  Vditor was constructed with `theme: 'dark'`. Removed; the
  editor now inherits the app's light surface.
- `<!--kb-block:-->` HTML comment markers injected by the server
  no longer show as code-block-looking rectangles in the editor
  (CSS hides the html-block wrapper).
- Removed the legacy `<!--kb-block-->` visible text in IR mode.
- Many smaller fixes in the data layer: idempotent `ALTER TABLE`
  for `notes.article_id`, `get_notes_for_article()` for the article
  panel, `[art-link:<id>]` link resolution in `sync_note_links`.

## Internal

- All notes editor and floating-note editor mutations now go
  through the same slash / `@` / block-gutter pipeline; the
  floating note's `Vditor` instance calls `attachSlashListeners`
  + `attachBlockGutter` from its `after` hook.
- `init()` now eagerly awaits `loadNotebooksAndNotes()` so the
  `@` picker has data even when the user goes straight to the
  article reader.
- New endpoints:
  - `GET /api/articles/<id>/notes` — list + count of every note
    that references the article.
- `handle_save_note` persists `article_id` on update; `createNote`
  accepts an `article_id` + `slug` and uses the stable id
  `art_<article_id>__<slug>`.

## End-to-end verification

- Full Playwright smoke (`scripts/pw_smoke.py`) — 14/14 checks pass.
- Slash / `@` regression test — typing continues after picking a
  command, no editor freeze.
- Article notes panel test — count badge, list, delete button,
  header sticky, `[[art-link:<id>]]` mention in a free note both
  surface correctly in the article view.

## Build

`dist/KBase/KBase.exe` rebuilt and run end-to-end against the
packaged exe. The PyInstaller onedir bundle ships the full Python
runtime plus Vditor / PyMuPDF / requests / openai. SQLite is the
single source of truth (no more kb-index.json, notes_index.json,
or library_chat_sessions.json); all user data lives under
`dist/KBase/data/`.

## Roll-up commits since v0.1.0

- `84765c1` fix(notes): slash and @ command picks no longer freeze the editor
- `6a8d0b9` fix(notes): pin 文章小记 header, add delete, extend @ to articles
- `ef2a3da` feat(notes): 文章小记 — article-scoped notes + selection toolbar
- `08fce16` fix(notes): arrow keys now navigate slash menu one item at a time
- `abb1ede` feat(notes): hide markers, slash menu IME support, rich emoji panel
- `d1451d1` fix(notes): gutter hover, block anchors, AI menu — verified end-to-end
- `a1b9ed5` feat(notes): block anchors, hover gutter, AI menu, full shortcuts
- `497e781` refactor(notes): notebooks, document tree, multi-tab, 4-tab right panel
- `89776cd` feat(notes): add notebooks, document tree, and block anchors
- `fa7b43e` refactor(notes): introduce store, fix save races, add toast + skeleton
