# Knowledge Base — Academic Paper Library

> [中文](README.zh-CN.md)

A local academic paper knowledge base powered by [Marker](https://github.com/VikParuchuri/marker) PDF engine. PDF upload/parsing, Markdown reading, AI chat, translation, summarization — all in a native desktop window.

## Features

- **PDF Parsing** — Multi-engine: Marker (local Surya) / Alibaba Cloud DocMind API
- **Library Management** — Card/board view, search, categories, tags, delete
- **Paper Reader** — Three-panel layout (Markdown + AI Chat + PDF), draggable resizers
- **AI Chat** — Context-aware dialogue based on the full paper (OpenAI-compatible API)
- **Translation** — Segment-by-segment LLM translation, persisted to disk
- **AI Summary** — Auto-extract background, method, findings, contributions
- **Re-parse** — Switch engines, keep version history for comparison
- **Notes** — Full-featured note-taking system (see [Notes](#notes) below)
- **LaTeX Rendering** — KaTeX real-time math rendering

### Notes

A built-in note-taking system accessible via the 📝 button in the top bar.

**Editor**
- WYSIWYG Markdown editor (ToastUI Editor) with live preview
- Slash command menu: type `/` to insert headings, lists, code blocks, tables, math, Mermaid diagrams, and more
- Code syntax highlighting (highlight.js) with language labels
- KaTeX math rendering (inline `$...$` and display `$$...$$`)
- Mermaid diagram rendering (flowcharts, sequence diagrams, etc.)

**Organization**
- Folder-based grouping with collapsible tree sidebar
- Tag system with colored pills
- Full-text search across note titles
- Daily notes: one-click creation dated to today (stored in `daily/` folder)

**Linking**
- Bidirectional links: use `[[note-title]]` syntax to link between notes
- Backlinks panel: automatically shows all notes that reference the current note
- Click a wiki-link to navigate; click a dashed link to create a new note

**Keyboard Shortcuts**

| Shortcut | Action | Shortcut | Action |
|---|---|---|---|
| `/` | Slash command menu | `@` | Link to another note |
| `Ctrl+S` | Save | `Ctrl+D` | Duplicate line |
| `Ctrl+B` | Bold | `Ctrl+I` | Italic |
| `Ctrl+U` | Underline | `Ctrl+Shift+S` | Strikethrough |
| `Ctrl+G` | Inline code | `Alt+D` | Highlight (mark) |
| `Ctrl+K` | Link | `Ctrl+M` | Inline math |
| `Ctrl+Shift+K` | Code block | `Ctrl+Shift+L` | Toggle task checkbox |
| `Ctrl+Shift+H` | Cycle heading level | `Ctrl+Shift+D` | Delete line |
| `Ctrl+Enter` | Insert line below | `Ctrl+Shift+Enter` | Insert line above |
| `Ctrl+]` / `Ctrl+[` | Indent / Outdent | `Tab` / `Shift+Tab` | Indent / Outdent |
| `Ctrl+Shift+T` | Insert date/time | `Escape` | Back to library |

**Reader View Shortcuts**

| Shortcut | Action | Shortcut | Action |
|---|---|---|---|
| `T` | Toggle outline | `N` | Toggle notes |
| `E` | Toggle edit mode | `L` | Start translation |
| `S` | Save edit | | |

Click the ⌨️ button in the notes topbar for the full shortcut reference.

**Data Storage**
- Notes are stored as `.md` files in `kb/notes/`
- Metadata (title, tags, folder) in `kb/notes_index.json`
- Both are gitignored by default

## Requirements

| Dependency | Purpose | Install |
|------------|---------|---------|
| **Python** | 3.10+ (3.13 recommended) | [python.org](https://www.python.org/) |
| **PyTorch** | 2.x + CUDA 12.x (local engine GPU optional) | `pip install torch` |
| **marker-pdf** | Marker local engine | `pip install marker-pdf` |
| **PyMuPDF** | PDF preview / metadata | `pip install pymupdf` |
| **pywebview** | Desktop window | `pip install pywebview` |
| **huggingface_hub** | Model download (optional) | `pip install huggingface_hub` |
| **alibabacloud_docmind_api20220711** | DocMind cloud engine (optional) | `pip install alibabacloud_docmind_api20220711` |

Desktop mode requires system WebView2 (built into Windows 11; Windows 10 needs [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)).

### Engines

| Engine | Type | VRAM/GPU | Notes |
|--------|------|----------|-------|
| **Marker** | Local | Required | Surya models, offline, needs model download |
| **DocMind** | Cloud API | None | Alibaba Cloud, requires RAM AccessKey |

### Marker (Local) VRAM Requirements

> **Only applies** when using the Marker local engine. DocMind cloud engine requires no GPU.

Marker loads 4 Surya models simultaneously. Real-world VRAM usage:

| GPU VRAM | Status | Advice |
|----------|--------|--------|
| 4 GB | ❌ Won't run | Must use CPU mode |
| **6 GB** | ⚠️ Marginal | Close browser PDF previews, desktop apps |
| 8 GB | ✅ Good | Recommended |
| 12 GB+ | ✅ Plenty | Default batch sizes work |

> **Note**: Browser PDF previews consume ~200-500MB GPU VRAM and can cause CUDA OOM or `CUBLAS_STATUS_EXECUTION_FAILED` during parsing. The app includes GPU conflict monitoring and auto-hides PDF during conversion.

### CPU Mode (Marker)

If GPU VRAM is insufficient, switch to CPU mode in Settings. A 10-page paper takes ~5-15 minutes on 32 GB RAM.

## Quick Start

```bash
# 1. Install all dependencies
pip install marker-pdf pymupdf pywebview alibabacloud_docmind_api20220711

# 2. Configure LLM API (OpenAI-compatible)
cp local.env.example local.env
# Edit local.env with your API key, URL, and model

# 3. First run downloads Surya models automatically (~3.5 GB, once)
python kb/desktop.py
```

### DocMind Cloud Engine Setup (optional)

To use Alibaba Cloud DocMind for parsing (no GPU needed), configure RAM credentials in `local.env`:

```env
DOCMIND_ACCESS_KEY_ID=LTAI5t...  # RAM AccessKey ID
DOCMIND_ACCESS_KEY_SECRET=...     # RAM AccessKey Secret
DOCMIND_REGION=cn-hangzhou
```

Setup steps:
1. Activate [Alibaba Cloud Document Intelligence](https://www.aliyun.com/product/ai/docmind)
2. Create an AccessKey in [RAM Console](https://ram.console.aliyun.com/profile/access-keys)
3. Add to `local.env`, restart the app, and select DocMind engine

Models are cached in `models/`. This repo does **not** include model files (`models/` is gitignored).

### Manual Model Download (slow network)

```bash
pip install huggingface_hub
python -c "
from huggingface_hub import snapshot_download
base = 'models'
snapshot_download('vikp/surya_det3', local_dir=f'{base}/vikp--surya_det3')
snapshot_download('vikp/surya_rec3', local_dir=f'{base}/vikp--surya_rec3')
snapshot_download('vikp/surya_layout3', local_dir=f'{base}/vikp--surya_layout3')
snapshot_download('vikp/surya_order2', local_dir=f'{base}/vikp--surya_order2')
snapshot_download('vikp/surya_tablerec', local_dir=f'{base}/vikp--surya_tablerec')
"
```

Custom model path via env:
```bash
$env:MODEL_CACHE_DIR="D:/path/to/models"   # PowerShell
export MODEL_CACHE_DIR=/path/to/models      # Bash
```

### Launch

| Mode | Command | Notes |
|------|---------|-------|
| **Desktop App** | `python kb/desktop.py` | Native window, auto-stops on close (recommended) |
| **Web Server** | `cd kb && python serve.py` | Browser at `http://localhost:8765` |

### Desktop App Features

- Cycle panel layout: `📄💬📑` → `📄📑💬` → `💬📄📑` (click 🔄)
- Drag dividers to resize panels
- Show/hide each panel independently
- Text selection enabled

## Project Structure

```
kbase/
├── kb/                     # Knowledge Base app
│   ├── desktop.py           # Desktop entry point (pywebview)
│   ├── serve.py             # HTTP server (API + conversion dispatch)
│   ├── index.html           # Single-page frontend
│   ├── engines/             # Conversion engines (Marker / DocMind)
│   ├── kb-index.json        # Article index (runtime)
│   ├── low_memory_config.json # User settings (runtime)
│   ├── llm_config.json        # LLM provider settings (runtime)
│   ├── articles/           # Parsed output (one folder per paper)
│   │   └── {id}/
│   │       ├── original.pdf
│   │       ├── {id}.md
│   │       ├── {id}_marker.md   # Per-engine version history
│   │       ├── {id}_docmind.md
│   │       └── {id}_meta.json
│   ├── notes/              # Note files (runtime, gitignored)
│   │   └── {note_id}.md
│   └── notes_index.json    # Notes metadata index (runtime, gitignored)
├── marker/                 # Marker PDF engine source
└── models/                 # Surya model cache (~3.5 GB, gitignored)
```

## CLI Commands

### Single file

```bash
marker_single paper.pdf
```

Outputs to `kb/articles/{pdf_name}/` with `{name}.md`, `{name}_meta.json`, and extracted images.

### Batch

```bash
marker /path/to/pdf_folder
```

### Common Flags

| Flag | Description |
|------|-------------|
| `--output_dir PATH` | Output directory (default: `kb/articles`) |
| `--output_format FORMAT` | `markdown` (default), `json`, `html`, `chunks` |
| `--page_range RANGE` | Page range, e.g. `0,5-10,20` |
| `--debug` | Debug mode |
| `--disable_image_extraction` | Skip image extraction |
| `--config_json PATH` | Extra JSON config file |

Examples:

```bash
# First 5 pages, JSON output
marker_single paper.pdf --page_range 0-4 --output_format json

# Batch with custom output dir
marker ./pdfs --output_dir ./results
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/articles` | List all articles |
| POST | `/api/upload` | Upload PDF (multipart) |
| POST | `/api/chat` | LLM proxy (OpenAI-compatible) |
| GET/PUT | `/api/llm-config` | LLM provider configuration |
| POST | `/api/convert/{id}` | Trigger conversion (body: `{id, engine}`) |
| POST | `/api/articles/delete` | Delete article |
| PUT | `/api/articles/update` | Update metadata |
| PUT | `/save` | Save file content |
| GET | `/api/conversion-status/{id}` | Conversion progress/log |
| GET | `/api/conversion-history/{id}` | Conversion history & versions |
| GET | `/api/notes` | List all notes |
| POST | `/api/notes` | Create note (body: `{title, folder}`) |
| GET | `/api/notes/{id}` | Get note content + metadata |
| PUT | `/api/notes/{id}` | Save note (body: `{content, title, tags, folder}`) |
| DELETE | `/api/notes/{id}` | Delete note |
| PUT | `/api/notes/{id}/rename` | Rename note (body: `{title}`) |

## Credits

Built on top of these open-source projects:

- **[Marker](https://github.com/VikParuchuri/marker)** — PDF → Markdown engine (GPL-3.0)
- **[Surya](https://github.com/VikParuchuri/surya)** — Document OCR / layout / text recognition models (GPL-3.0)
- **[KaTeX](https://katex.org/)** — LaTeX rendering
- **[Toast UI Editor](https://ui.toast.com/tui-editor)** — WYSIWYG Markdown editor
- **[marked](https://marked.js.org/)** — Markdown parser
- **[highlight.js](https://highlightjs.org/)** — Code syntax highlighting
- **[Mermaid](https://mermaid.js.org/)** — Diagram rendering


## License

Following Marker's licensing:

- **Code**: [GNU General Public License v3.0](https://www.gnu.org/licenses/gpl-3.0.html)
- **Models**: [OpenRAIL-M](https://www.datalab.to/pricing)

## For AI Agents

This section is for Claude Code, GitHub Copilot, Codex, and similar AI coding assistants. It helps them understand the project context and assist users with setup.

### Project Overview

- A **local academic paper knowledge base desktop app** powered by Marker PDF engine + DocMind cloud API
- `marker/` is vendored Marker Python source (from [VikParuchuri/marker](https://github.com/VikParuchuri/marker)), not a git submodule
- `kb/` is the KB web app: `serve.py` backend, `index.html` frontend, `desktop.py` pywebview launcher
- `kb/engines/` contains pluggable conversion engines: `marker.py` (local Surya), `docmind.py` (AliCloud API)
- `kb/notes/` stores note `.md` files; `kb/notes_index.json` holds metadata (title, tags, folder) — both gitignored
- Notes use ToastUI Editor (WYSIWYG + Markdown tabs), with wiki-link `[[title]]` syntax for bidirectional linking
- Repo does **NOT** include Surya model files (gitignored); download separately

### Setup Steps

When a user asks for help installing or configuring this project, follow this order:

1. **Python** — Verify Python 3.10+ (`python --version`)
2. **Install** — `pip install marker-pdf pymupdf pywebview alibabacloud_docmind_api20220711`. If user has GPU, verify PyTorch CUDA compatibility. Windows 10 may need [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
3. **Download models** — Only needed for Marker local engine. Must be placed in `{repo_root}/models/`. `serve.py` sets `MODEL_CACHE_DIR` to this path. ~3.5 GB. Options:
   - Auto: first run of `python kb/desktop.py` or `marker_single --help` triggers download
   - Manual (slow network):
     ```bash
     pip install huggingface_hub
     python -c "
     from huggingface_hub import snapshot_download
     base = 'models'
     snapshot_download('vikp/surya_det3', local_dir=f'{base}/vikp--surya_det3')
     snapshot_download('vikp/surya_rec3', local_dir=f'{base}/vikp--surya_rec3')
     snapshot_download('vikp/surya_layout3', local_dir=f'{base}/vikp--surya_layout3')
     snapshot_download('vikp/surya_order2', local_dir=f'{base}/vikp--surya_order2')
     snapshot_download('vikp/surya_tablerec', local_dir=f'{base}/vikp--surya_tablerec')
     "
     ```
4. **Configure** — Copy `local.env.example` to `local.env`, fill in:
   - LLM API (OpenAI-compatible): configure in the app settings or use `LLM_API_KEY`, `LLM_API_URL`, `LLM_MODEL`
   - DocMind cloud engine (optional): `DOCMIND_ACCESS_KEY_ID`, `DOCMIND_ACCESS_KEY_SECRET`, `DOCMIND_REGION` (get from Alibaba Cloud RAM console → Create AccessKey)
5. **Launch** — `python kb/desktop.py` (desktop window) or `cd kb && python serve.py` (web mode, browser at `http://localhost:8765`)

### Critical Constraints

- `local.env` is gitignored — **never commit or expose its contents**
- `{repo_root}/models/` is gitignored — **never commit model files**. Surya models use subdirectory structure `models/vikp--{model_name}/`
- `kb/articles/` is runtime output, gitignored (along with `upload_queue/`, `conversion_temp/`, `kb-index.json`, `low_memory_config.json`, `llm_config.json`, `kb/notes/`, `kb/notes_index.json`)
- **Marker local engine** needs ~6-8 GB GPU VRAM; if insufficient, use CPU mode or switch to DocMind cloud engine
- DocMind cloud engine requires no GPU, uses Alibaba Cloud official SDK, completes in ~3-10 seconds
- Never write API keys or sensitive data into `CLAUDE.md`
- **Desktop app**: `kb/desktop.py` requires system WebView2. Auto-starts HTTP server on launch, auto-stops on window close
- **Engine architecture**: Add new engines by implementing `run(pdf_path, article_id, log_callback)` in `kb/engines/` and registering in `__init__.py`
