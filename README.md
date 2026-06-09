# KBase — Personal Knowledge Base

> [中文](README.zh-CN.md)

A local personal knowledge base desktop app. Upload PDFs, documents, code, data files — parse, read, chat, translate, summarize with AI. All data stored locally.

## Features

- **Document Ingestion** — Upload PDFs, Markdown, text, code, data files (CSV/JSON), archives. Auto-detect format and generate readable Markdown previews.
- **PDF Parsing** — Multi-engine parsing: PyMuPDF (instant pre-parse) / Marker (local Surya) / DocParser (cloud GPU) / DocMind (Alibaba Cloud API). Version history preserved per engine.
- **Library Management** — Card/board view, full-text search, categories, tags, batch operations, workspaces for grouping.
- **AI Chat** — Context-aware dialogue on any document, plus cross-library RAG search across all papers and notes (OpenAI-compatible API).
- **Translation** — Segment-by-segment LLM translation with smart reuse of previous translations.
- **AI Summary & Metadata** — Auto-extract title, authors, DOI, year, venue, abstract, keywords from documents.
- **Full-Featured Notes** — WYSIWYG Markdown editor, folders, tags, bidirectional wiki-links (`[[note]]`), daily notes, slash commands, code highlighting, KaTeX math, Mermaid diagrams.
- **Three-Panel Reader** — Markdown + AI Chat + PDF preview, draggable resizers, cycle layouts.

## Quick Start

Download the latest release from [Releases](https://github.com/DeconBear/kbase/releases), extract and double-click `KBase.exe`.

Or run from source:

```bash
# 1. Install core dependencies
pip install pymupdf pywebview

# 2. Configure LLM API (OpenAI-compatible)
cp local.env.example local.env  # only required for the legacy v0.x release; current builds auto-generate data/local.env
# Edit local.env with your API key, URL, and model

# 3. Launch
python kb/desktop.py
```

The local Marker PDF engine (PyTorch + Surya models) is **optional** and can be downloaded on-demand from within the app when first used. Cloud engines (DocParser, DocMind) work out of the box with an API key.

## 📦 Packaging & Portable Usage

KBase supports being packaged into a standalone Windows executable or directory via PyInstaller:

```bash
python -m PyInstaller --noconfirm KBase.spec
```

**Data Storage Mechanism:**
All user data lives under a single `data/` root, which is created automatically on first launch:
- **Running from Source** (`python serve.py`): `data/` is created at the repository root (the parent of `kb/`). The source tree itself stays clean.
- **Running Packaged App** (`KBase.exe`): `data/` is created next to the executable.

```
data/
├── articles/                     # one folder per uploaded paper / file
├── notes/                        # note_*.md files
├── .kbase/
│   ├── index.db                  # SQLite: articles, notes, tags, workspaces, translations
│   ├── chat_sessions/            # one JSON per library-chat session
│   ├── chat_sessions_index.json
│   └── logs/                     # per-task *.log files
├── local.env                     # generated on first launch with empty API keys
└── llm_config.json               # UI-managed LLM provider list
```

**`local.env`:** Generated automatically the first time KBase runs. Contains empty values for the eight known keys (LLM, DocMind, DocParser). Edit the file directly or use the in-app Settings page — secret values are masked in the UI, never displayed in plaintext.

**Portable Moving & Data Migration:**
1. **Multi-Device Roaming**: Copy `KBase.exe` and the `data` folder together to a USB drive, another disk, or another Windows PC. Double-click to run.
2. **Migrating from an Older Source Build (pre-SQLite)**: If you previously used KBase with the legacy `kb/kb-index.json`, `kb/notes_index.json`, and `kb/library_chat_sessions.json` files, run the migration once:
   ```bash
   python kb/migrate.py
   ```
   The script copies your `kb/articles/`, `kb/notes/` and JSON indices into `data/`, imports everything into the SQLite database, and renames the legacy files to `*.legacy.json` so you keep a backup.

## Engines

| Engine | Type | GPU | Setup |
|--------|------|-----|-------|
| **PyMuPDF** | Local | None | Built-in — instant pre-parse on upload |
| **DocParser** | Cloud API | None | [DeconBear DocParser](https://your-cloud-parser.com), just needs API key |
| **DocMind** | Cloud API | None | Alibaba Cloud, needs RAM AccessKey |
| **Marker** | Local | Optional | PyTorch + Surya models (~3.5 GB), download on first use |

## Desktop App

- Native Windows window (pywebview + Edge WebView2)
- Auto-starts HTTP server on launch, auto-stops on close
- System tray / taskbar integration with custom icon
- Cycle panel layouts with draggable dividers
- Windows 10: requires [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) (built into Windows 11)

## Project Structure

```
kbase/
├── kb/                     # Knowledge Base app
│   ├── desktop.py           # Desktop entry point (pywebview)
│   ├── serve.py             # HTTP server + API backend
│   ├── index.html           # Single-page frontend
│   ├── engines/             # Pluggable conversion engines
│   │   ├── marker.py         #   Marker (local Surya)
│   │   ├── docparser.py      #   DeconBear DocParser (cloud)
│   │   └── docmind.py        #   Alibaba Cloud DocMind (cloud)
│   ├── db_api.py            # SQLite database layer
│   ├── llm_config.py        # LLM provider configuration
│   ├── library_chat.py      # Cross-library RAG search
│   ├── translate.py         # Background Markdown translation
│   ├── calibrate.py         # LLM-assisted MD calibration
│   ├── document_info.py     # Metadata extraction (PyMuPDF + LLM)
│   ├── articles/            # Parsed documents (one folder per item)
│   │   └── {id}/
│   │       ├── original.pdf
│   │       ├── {id}.md
│   │       └── {id}_marker.md / {id}_docparser.md (per-engine history)
│   ├── notes/               # Note files (.md)
│   │   └── {note_id}.md
│   └── .kbase/              # SQLite database (runtime)
├── marker/                  # Vendored Marker PDF engine
├── kbase.spec               # PyInstaller build configuration
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/articles` | List all articles |
| POST | `/api/upload` | Upload file (multipart) |
| POST | `/api/chat` | LLM proxy (OpenAI-compatible streaming) |
| GET/PUT | `/api/llm-config` | LLM provider configuration |
| POST | `/api/convert/{id}` | Trigger PDF conversion |
| DELETE | `/api/articles/delete` | Delete article |
| PUT | `/api/articles/update` | Update metadata |
| POST | `/api/translate/{id}` | Start background translation |
| POST | `/api/extract-info/{id}` | Extract document metadata |
| POST | `/api/calibrate/{id}` | LLM-assisted OCR calibration |
| POST | `/api/library-chat/ask` | Cross-library RAG question |
| GET/POST | `/api/notes` | List / create notes |
| GET/PUT/DELETE | `/api/notes/{id}` | Read / save / delete note |
| GET | `/api/workspaces` | List workspaces |
| POST | `/api/export` | Export (BibTeX / PDF ZIP / Markdown ZIP) |
| POST | `/api/install-marker-deps` | On-demand Marker engine install (SSE streaming) |

## Keyboard Shortcuts

### Reader View

| Shortcut | Action | Shortcut | Action |
|---|---|---|---|
| `T` | Toggle outline | `N` | Toggle notes sidebar |
| `E` | Toggle edit mode | `L` | Start translation |
| `S` | Save edit | `Esc` | Back to library |

### Note Editor

| Shortcut | Action | Shortcut | Action |
|---|---|---|---|
| `/` | Slash command menu | `@` | Link to another note |
| `Ctrl+S` | Save | `Ctrl+B` | Bold |
| `Ctrl+I` | Italic | `Ctrl+K` | Insert link |
| `Ctrl+M` | Inline math | `Ctrl+Shift+K` | Code block |
| `Ctrl+]` / `Ctrl+[` | Indent / Outdent | `Ctrl+Enter` | Insert line below |

## Credits

Built on top of these open-source projects:

- **[Marker](https://github.com/VikParuchuri/marker)** — PDF → Markdown engine (GPL-3.0)
- **[Surya](https://github.com/VikParuchuri/surya)** — Document OCR / layout models (GPL-3.0)
- **[PyMuPDF](https://pymupdf.readthedocs.io/)** — PDF rendering and fast pre-parsing
- **[KaTeX](https://katex.org/)** — LaTeX rendering
- **[Toast UI Editor](https://ui.toast.com/tui-editor)** — WYSIWYG Markdown editor
- **[marked](https://marked.js.org/)** — Markdown parser
- **[highlight.js](https://highlightjs.org/)** — Code syntax highlighting
- **[Mermaid](https://mermaid.js.org/)** — Diagram rendering
- **[DOMPurify](https://github.com/cure53/DOMPurify)** — Safe HTML sanitization for rendered Markdown

## License

Following Marker's licensing:

- **Code**: [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html)
- **Models**: [OpenRAIL-M](https://www.datalab.to/pricing)

## Android Preview

The `android/` folder contains a first-pass standalone Android app. It does not require the Python backend server. The app calls an OpenAI-compatible `chat/completions` endpoint directly after the user enters an endpoint, API key, and model name on the device.

Current Android scope:

- Direct cloud chat through an OpenAI-compatible API
- Local API key/model/endpoint settings saved on the device
- Optional text/Markdown/JSON/XML file selection as chat context
- No bundled Marker/PDF/OCR pipeline in the APK yet

Build from Android Studio by opening the `android/` folder, or from a shell with Android SDK and JDK 17 configured:

```powershell
cd android
.\gradlew.bat :app:assembleDebug
```

## For AI Agents

This section is for Claude Code, GitHub Copilot, and similar AI coding assistants.

### Project Overview

- A **local personal knowledge base desktop app** — documents, papers, notes, code, data files
- `marker/` is vendored Marker source (from [VikParuchuri/marker](https://github.com/VikParuchuri/marker)), not a git submodule
- `kb/` is the KB web app: `serve.py` backend, `index.html` SPA frontend, `desktop.py` pywebview launcher
- `kb/engines/` contains pluggable conversion engines: `marker.py` (local), `docparser.py` (cloud), `docmind.py` (cloud)
- Notes use ToastUI Editor with `[[title]]` wiki-link syntax for bidirectional linking
- Repo does **NOT** include Surya models (gitignored); Marker engine is optional and installable on-demand

### Setup Steps

1. **Python** — Verify Python 3.10+ (`python --version`)
2. **Install** — `pip install pymupdf pywebview` for basic setup. Marker engine deps (torch, transformers, surya-ocr) are optional and downloadable within the app.
3. **Configure** — Copy `local.env.example` to `local.env`, configure LLM API key. DocParser/DocMind cloud engines need their respective API keys.
4. **Launch** — `python kb/desktop.py` (desktop) or `cd kb && python serve.py` (browser at `http://localhost:8765`)

### Critical Constraints

- `local.env` is gitignored — **never commit or expose its contents**
- `{repo_root}/models/` is gitignored — **never commit model files**
- `kb/articles/`, `kb/notes/`, `.kbase/` are runtime data, gitignored
- Marker local engine is optional — cloud engines (DocParser, DocMind) work without local GPU
- Never write API keys or secrets into `CLAUDE.md`
- Desktop app auto-starts HTTP server on launch, auto-stops on window close
- Add new engines by implementing `run(pdf_path, article_id, log_callback)` in `kb/engines/` and registering in `__init__.py`
