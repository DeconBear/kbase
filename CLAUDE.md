# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Codegraph

> **Auto-update rule**: After every non-trivial change (new module, new endpoint, new engine, changed function signatures), update this codegraph section to reflect the current state. At minimum, re-verify the module graph, API surface, and engine list.

### Module dependency graph

```
                    ┌──────────────┐
                    │  version.py  │  (VERSION string)
                    └──────────────┘

┌──────────┐    ┌──────────────────┐    ┌─────────────────┐
│  storage │◄───│  engines/_paths  │◄───│  engines/*.py   │
│  .py     │    └──────────────────┘    │  marker          │
│          │                            │  docparser       │
│  SQLite  │    ┌──────────────────┐    │  docmind         │
│  CRUD    │◄───│  llm_config.py   │    │  vision_ocr      │
│  paths   │    │  call_chat_      │    │  ocr             │
│  env     │    │  completion()    │    │  llm_vision      │
└────┬─────┘    └────────┬─────────┘    │  unisound_parser │
     │                   │              └─────────────────┘
     │    ┌──────────────┼──────────────┐
     │    │              │              │
     ▼    ▼              ▼              ▼
┌─────────────────────────────────────────────┐
│               serve.py                       │
│  KBHandler (do_GET/POST/PUT/DELETE)          │
│  scan_articles, run_conversion               │
│  _run_translate, _run_calibrate              │
│  _run_extract_info                           │
└──────┬──────────────────────────────────────┘
       │
       │  serves
       ▼
┌──────────────┐    ┌─────────────────┐
│  index.html  │    │  desktop.py     │
│  (SPA)       │    │  (pywebview)    │
└──────────────┘    └─────────────────┘

┌──────────────────┐
│  library_chat.py │──► storage, llm_config
└──────────────────┘

┌──────────────────┐
│  translate.py    │──► storage, llm_config
└──────────────────┘

┌──────────────────┐
│  calibrate.py    │──► storage, llm_config
└──────────────────┘

┌──────────────────┐
│  document_info.py│──► storage, llm_config
└──────────────────┘

┌──────────────────┐
│  updater.py      │──► version
└──────────────────┘
```

**Key dependency rules:**
- `storage.py` is the **root** — it depends on nothing in `kb/` except stdlib. Every other module imports it.
- `engines/_paths.py` re-exports from `storage` — engines never import `storage` directly.
- `llm_config.py` depends on `storage` (for config file paths and env loading). All LLM callers go through it.
- `serve.py` is the **hub** — it imports from `storage`, `llm_config`, `engines`, `updater`, `version`.

### Module reference

#### `storage.py`
```
REPO_ROOT, DATA_ROOT, ARTICLES_DIR, NOTES_DIR, KBASE_DIR, DB_PATH
LOGS_DIR, CHAT_SESSIONS_DIR, LOCAL_ENV, LLM_CONFIG_FILE, LOW_MEMORY_CONFIG
PACKAGE_DIR, STATIC_INDEX_HTML, STATIC_ASSETS

ensure_directories()
load_local_env()
public_local_env() → dict[str,str]
get_data_root_info() → dict
set_data_root(new_root) → dict

init_db()
get_conn() → contextmanager[sqlite3.Connection]

# Articles
get_all_articles() → list[dict]
get_article(aid) → dict|None
upsert_article(article)
update_article_fields(aid, updates)
delete_article(aid)
replace_article_tags(aid, tags)
get_article_note_count(aid) → int
record_article_history(aid, engine, path)
list_article_history(aid) → list[dict]
delete_article_history(aid, engine)
list_article_attachments(aid) → list[dict]
upsert_article_attachment(aid, name, path)
delete_article_attachment(aid, name) → bool

# Notes
get_all_notes() → list[dict]
upsert_note(note)
delete_note(nid)
get_notes_for_article(aid) → list[dict]
sync_note_blocks(note_id, content) → list[dict]
inject_block_anchors(content, rows) → str
sync_note_links(note_id, content) → list[dict]
get_note_backlinks(note_id) → list[dict]
get_note_blocks(note_id) → list[dict]
find_note_block(note_id, anchor) → dict|None

# Notebooks
ensure_default_notebook()
list_notebooks() → list[dict]
upsert_notebook(nb)
delete_notebook(nb_id)

# Translation
save_translation_state(aid, **fields)
load_translation_state(aid) → dict|None
record_conversion(aid, engine, status)
list_conversion_history(aid, limit=50) → list[dict]

# Workspaces
list_workspaces() → list[dict]
upsert_workspace(ws_id, name) → dict
delete_workspace(ws_id)
add_item_to_workspace(ws_id, item_id, item_type)
remove_item_from_workspace(ws_id, item_id)
get_workspace_items(ws_id) → list[dict]

# Chat sessions
list_chat_sessions() → dict
save_chat_index(state)
load_chat_session_file(sid) → dict
save_chat_session_file(sid, data)
delete_chat_session_file(sid)

# Atomic write helper
_atomic_write_json(path, data)
_write_local_env(updates)      # thread-safe, atomic write
```

#### `serve.py`
```
PORT = 8765

class KBHandler(BaseHTTPRequestHandler):
    _json(data, status=200)
    _error(status, message)
    _read_json() → dict
    serve_static(path)
    # Handlers: handle_upload, handle_convert, handle_calibrate,
    #   handle_translate, handle_extract_info, handle_save_env,
    #   handle_save_file, handle_export, handle_article_update,
    #   handle_article_delete, handle_open_folder, handle_apply_update,
    #   handle_set_data_root, handle_create_note, handle_save_note,
    #   handle_delete_note, handle_rename_note, handle_create_notebook,
    #   handle_update_notebook, handle_delete_notebook,
    #   handle_library_chat_*, handle_get_note, handle_note_backlinks,
    #   handle_get_attachments, handle_upload_attachment,
    #   handle_delete_attachment, handle_history_delete

scan_articles() → list[dict]
run_conversion(pdf_path, aid, engine_name, docparser_engine=None)
record_article_history_safe(aid, engine, path)

validate_article_id(value) → str
validate_note_id(value) → str
article_dir_for(aid) → Path
note_file_for(note_id) → Path
sanitize_article_id(value) → str
resolve_save_path(filepath) → Path

start_server() → ReusableThreadingTCPServer
```

#### `desktop.py`
```
main()           # pywebview entry point
class Api:
    save_file(content, suggested_name) → bool
    quit_app()   # closes the window (used by auto-updater)
```

#### `llm_config.py`
```
load_llm_config() → dict
public_llm_config() → dict
save_llm_config_from_public(data) → dict
resolve_llm_settings(provider_id, model) → dict
call_chat_completion(messages, *, provider_id, model,
                     temperature, max_tokens, timeout, stream) → dict
```

#### `engines/__init__.py`
```
ENGINES = {name: "module.Class", ...}
  "marker", "docmind", "docparser", "vision", "ocr",
  "llm_vision", "unisound"
get_engine(name) → Engine instance
check_marker_available() → bool
install_marker_deps(log_callback) → bool
```

#### `engines/_paths.py`
```
ARTICLES_DIR, LOW_MEMORY_CONFIG, REPO_ROOT  # re-exported from storage
```

#### `library_chat.py`
```
list_sessions() → dict
get_session(sid) → dict
create_session(title) → dict
delete_session(sid) → dict
clear_session(sid) → dict
ask_library_question(question, session_id, provider_id, model, workspace_id) → dict
search_library(query, limit, context_chars, workspace_id) → list[dict]
```

#### `translate.py`
```
translate_article(aid, mode, target_language, extra_prompt, log_callback) → bool
chunk_markdown(md_text, max_chars=4500) → list[str]
```

#### `calibrate.py`
```
calibrate(aid, log_callback) → bool
```

#### `document_info.py`
```
quick_parse_pdf(aid, pdf_path, source_filename) → dict
ingest_non_pdf_file(aid, file_path, source_filename) → dict
extract_document_info(aid, log_callback, provider_id, model, reason) → dict
material_kind_from_filename(filename) → str
```

#### `updater.py`
```
VERSION (via version.py)
check_for_update(force=False) → dict
apply_update(asset_url) → bool        # launches detached PS updater
is_installed_build() → bool
```

#### `version.py`
```
VERSION = "0.3.6"
```

#### `migrate.py`
```
One-shot legacy data migration. Run once: python kb/migrate.py
```

### Storage schema

```
articles (id, title, author, authors_json, pages, date_added, category,
          doi, year, venue, abstract, translated, has_old_translation,
          summarized, pdf_available, md_available, file_available,
          converting, source_filename, kind, metadata_extracted,
          metadata_extracted_at, metadata_source, parser, preparse_error)

notes (id, title, created_at, modified_at, folder,
       notebook_id, parent_id, sort_order, doc_icon, article_id)

notebooks (id, name, icon, sort_order, closed, created_at, updated_at)

tags (item_id, tag, item_type)          -- item_type ∈ {paper, note}

note_blocks (id, note_id, anchor, heading, level, sort_order)
note_links (source_note_id, source_anchor, target_note_id,
            target_anchor, raw)

article_history (article_id, engine, file_path, updated_at)
article_attachments (article_id, name, path, size, mtime)
translation_state (article_id, status, percent, current, total,
                   message, started_at, completed_at, target_language,
                   output_file)
conversion_history (id, article_id, engine, status, ts)
workspaces (id, name, created_at)
workspace_items (workspace_id, item_id, item_type)
```

### API endpoint map

```
GET  /                          → serve_static (index.html)
GET  /api/articles              → scan_articles + get_all_articles
GET  /api/settings              → _collect_settings
GET  /api/llm-config            → public_llm_config
GET  /api/local-env             → public_env
GET  /api/conversion-status/<id>  → _conv_status_response
GET  /api/translation-status/<id> → _translation_state
GET  /api/conversion-history/<id> → list_conversion_history
GET  /api/articles/<id>/attachments → handle_get_attachments
GET  /api/articles/<id>/notes   → handle_get_article_notes
GET  /api/notes                 → get_all_notes
GET  /api/notes/<id>            → handle_get_note
GET  /api/notes/<id>/backlinks  → handle_note_backlinks
GET  /api/notes/<id>/blocks     → handle_get_note_blocks
GET  /api/library-chat/sessions → handle_library_chat_sessions
GET  /api/library-chat/sessions/<id> → handle_library_chat_session_get
GET  /api/notebooks             → list_notebooks
GET  /api/workspaces            → list_workspaces
GET  /api/check-update          → check_for_update
GET  /api/data-root             → get_data_root_info
GET  /api/export?ids=...&format=bibtex|pdf|markdown → handle_export
GET  /assets/*                  → PACKAGE_DIR/assets/
GET  /articles/<id>/<file>      → ARTICLES_DIR/<id>/<file>

POST /api/upload                → handle_upload (multipart)
POST /api/local-env             → handle_save_env
POST /api/articles/delete       → handle_article_delete
POST /api/articles/update       → handle_article_update
POST /api/llm-config            → save_llm_config_from_public
POST /api/chat                  → call_chat_completion
POST /api/library-chat/ask      → handle_library_chat_ask
POST /api/library-chat/sessions → handle_library_chat_sessions_create
POST /api/library-chat/sessions/delete → handle_library_chat_session_delete
POST /api/library-chat/sessions/clear → handle_library_chat_session_clear
POST /api/notebooks             → handle_create_notebook
POST /api/notes                 → handle_create_note
POST /api/convert/<id>          → handle_convert
POST /api/calibrate/<id>        → handle_calibrate
POST /api/translate/<id>        → handle_translate
POST /api/extract-info/<id>     → handle_extract_info
POST /api/open-folder/<id>      → handle_open_folder
POST /api/export                → handle_export
POST /api/apply-update          → handle_apply_update
POST /api/data-root             → handle_set_data_root
POST /api/articles/<id>/attachments → handle_upload_attachment
POST /api/articles/<id>/history/delete → handle_history_delete

PUT  /save                      → handle_save_file
PUT  /api/llm-config            → save_llm_config_from_public
PUT  /api/articles/update       → handle_article_update
PUT  /api/notebooks/<id>        → handle_update_notebook
PUT  /api/notes/<id>/rename     → handle_rename_note
PUT  /api/notes/<id>            → handle_save_note

DELETE /api/notes/<id>          → handle_delete_note
DELETE /api/notebooks/<id>      → handle_delete_notebook
DELETE /api/articles/<id>/attachments/<name> → handle_delete_attachment
```

### Frontend (index.html) structure

```
~9000 lines vanilla JS. No framework. Key sections:

Views:
  #library-view     — main library + global chat
  #reader-view      — article reader (PDF / Markdown / chat / notes tabs)
  #notes-view       — notebook + document tree + Vditor editor

Global state:
  articles[], currentArticle, currentRawMd, translatedMd
  notesStore (reactive Map-based), notebooksStore
  llmConfig, LLM_CFG (active provider/model)
  currentNotebookId, currentNoteId, noteTabs[]

Key functions:
  loadArticles(), renderLibrary(), openArticle(id)
  renderReaderMarkdown(md, article, rebuildTOC)
  renderNotebooksTree(), renderNoteTree()
  createNote(), saveCurrentNote(), openNoteInTab(id)
  loadNotebooksAndNotes()
  askGlobalQuestion(), doChat(messages)    — streaming chat
  renderChatMarkdown(text) → sanitized HTML
  abortableFetch(url, opts)                — auto-cancels stale requests
  batchExtractAndSummarize()               — bulk metadata + summary
  checkUpdate(), applyUpdate()             — auto-update flow
```

---

## What this is

KBase is an AI-powered, local-first knowledge management app for Windows (with Linux/macOS headless support). It parses academic PDFs (PyMuPDF, Marker, DocParser, DocMind, Vision OCR, Unisound U1), supports Markdown notes with bidirectional wiki-links and notebook organization, and integrates LLMs for chat, translation, metadata extraction, and calibration. The frontend is a single-page HTML/JS app served by an in-process HTTP server; the whole thing packages into `KBase.exe` via PyInstaller (onedir).

## Run / build commands

```bash
# Install minimum runtime deps
pip install pymupdf pywebview

# Source-mode run (data root = <repo>/data)
python kb/serve.py            # HTTP server only, browser at http://localhost:8765
python kb/desktop.py          # pywebview window wrapping the server

# Linux/macOS headless mode
./Start-KBase.sh              # bash launcher, auto-opens browser

# PyInstaller onedir build (~89 MB _internal/, ~8 MB exe)
python -m PyInstaller --noconfirm kbase.spec
# Output: dist/KBase/KBase.exe (+ dist/KBase/_internal/...)
# Data root for the packaged build: <exe dir>/data

# Linux PyInstaller build
python -m PyInstaller --noconfirm kbase-linux.spec
```

There is no real test suite. Do not add one without explicit request — the project has CI (build-release.yml) but no pytest config or test runner wired up.

No linter, formatter, or type checker is configured.

## Data flow

1. **User uploads** → `serve.handle_upload` parses multipart, writes `original.<ext>` to `data/articles/<id>/`, kicks off `quick_parse_pdf` (PyMuPDF) and `_start_extract_info` (LLM metadata).
2. **User triggers a parser** → `handle_convert` starts a thread running `engines.get_engine(name).run(...)`; the engine writes `<id>.md` plus `<id>_<engine>.md`. On success, `run_conversion` records versions, deletes derived files, and triggers metadata extraction.
3. **Frontend polls** `GET /api/articles` → `scan_articles` reconciles filesystem with SQLite.
4. **Settings page** POSTs to `/api/local-env` → updates `data/local.env` atomically.

## Storage layout

```
<data_root>/
  articles/<id>/              # original.<ext>, <id>.md, <id>_<engine>.md,
                              #   <id>_meta.json, <id>_info.json,
                              #   conversion.log, translation.log, attachments/
  notes/<id>.md               # flat .md files; metadata in SQLite
  .kbase/
    index.db                  # SQLite WAL (see schema above)
    chat_sessions/<id>.json
    chat_sessions_index.json
    logs/<id>/<task>.log
  local.env                   # auto-generated, 8 known keys
  llm_config.json             # multi-provider LLM config
  low_memory_config.json      # per-engine runtime flags
```

`data/` is gitignored. `plan/` is gitignored. `kb/` source must stay clean — no runtime data.

## Path resolution

- `storage._KB_PKG_DIR` = `Path(__file__).parent` (the `kb/` source dir).
- Frozen: `DATA_ROOT = Path(sys.executable).parent / "data"`.
- Source: `DATA_ROOT = _KB_PKG_DIR.parent / "data"`.
- `engines/_paths.py` re-exports paths from `storage` — engines use this, not direct path math.

## Things that will bite you

### PyInstaller frozen-mode imports
`kb/` uses bare top-level imports (`import storage`, `from llm_config import …`). PyInstaller only registers them as `kb.storage` etc. The pre-alias block at the top of `kb/desktop.py` is **load-bearing** — must run before any `import storage`. If you add a new top-level import used by a frozen module, add it to `_aliases` in `desktop.py`.

### `data/` location — easy to regress
Always use `storage.DATA_ROOT` (or `engines._paths` for engines). Never `Path(__file__).parent / "data"`.

### `tags` is a separate table
`articles.tags` lives in `tags(item_id, tag, item_type)`. Use `upsert_article` / `update_article_fields` — only `replace_article_tags` should touch the tags table directly.

### LLM response access
Defensive form required: `(data.get("choices") or [{}])[0].get("message", {}).get("content") or ""`. Never `data["choices"][0]["message"]["content"]` directly.

### Thread safety
- `_conv_status` is protected by `_conv_lock`.
- `_translation_threads` by `_translation_lock`.
- `_write_local_env` by `_ENV_LOCK`.
- `SESSION_LOCK` protects chat session I/O.
- `get_conn()` opens a new SQLite connection per call (WAL mode).

### Atomic writes
Prefer `_atomic_write_json` for JSON files. `_write_local_env` already uses `os.replace()` + lock. `llm_config.json` save also uses atomic write pattern.

## Style / conventions

- Python: `from __future__ import annotations` on every new module; type hints; one-line module docstring.
- Path safety: validate user input with `validate_article_id` / `validate_note_id` before `Path(...)`.
- Engines: `class XxxEngine: name = "..."; def run(self, pdf_path, article_id, log_callback=None, **kwargs) -> bool`. Register in `engines/__init__.py:ENGINES`.
- Frontend: vanilla JS, no build step, `DOMPurify.sanitize()` before `innerHTML`.

## Where to make changes

| I want to… | Edit |
|---|---|
| Add article column / metadata field | `storage._SCHEMA` + `storage._article_to_values` + `storage._row_to_article` |
| Add env key | `storage.LOCAL_ENV_TEMPLATE` + `serve.KNOWN_ENV_KEYS` + `serve.SENSITIVE_KEYS` + frontend `ENV_KEY_TO_INPUT` in `index.html` |
| Add conversion engine | `engines/<name>.py` + `ENGINES` in `engines/__init__.py` + `hiddenimports` in `kbase.spec` |
| Add API endpoint | Route in `KBHandler.do_GET`/`do_POST`/etc + `handle_*` method in `serve.py` |
| Add tool for Claude Code | `kb/tools/<name>.py` (CLI script, argparse, JSON stdout) |
| Change data root scheme | `storage._REPO_ROOT` / `DATA_ROOT` + `ensure_directories` |
| Add notebook feature | `storage._SCHEMA` + `storage` CRUD + `serve` handler + `index.html` UI |

## Things NOT to do

- **Don't `git push` (or `gh release create`, `gh pr create`) unless the user explicitly asks.**
- Don't add `kb/articles/`, `kb/notes/`, or `kb/.kbase/` as runtime paths — data lives in `data/`.
- Don't reintroduce `kb-index.json`, `notes_index.json`, or `library_chat_sessions.json` — SQLite is the source of truth.
- Don't write `data/` or `plan/` into the git tree.
- Don't commit real `data/local.env` or `data/.kbase/`.
- Don't add a test suite without explicit request.
- After any non-trivial change, update the Codegraph section above.
