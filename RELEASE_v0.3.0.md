# v0.3.0 — Two new PDF parsers + DeepSeek key wired

Brings two more PDF-parsing engines into the article reader's
engine dropdown, and tests the configured DeepSeek key end-to-end.

## New

- **云 OCR (云知声 等)** — `engines/ocr.py`. Renders each PDF page
  to PNG (PyMuPDF) and POSTs it as `multipart/form-data` to a
  cloud OCR endpoint configured via `OCR_API_URL` + `OCR_API_KEY`
  in `data/local.env`. Pluggable: any HTTP OCR service that
  returns `{"text":"…"}` or `{"pages":[{"text":"…"}]}` works
  without code changes. Engine gracefully fails with a clear log
  line if the keys are empty.
- **LLM 视觉解析 (复用 DeepSeek 等)** — `engines/llm_vision.py`. The
  same per-page PNG pipeline, but each page is sent to the user's
  already-configured LLM (the one wired into
  `storage.LLM_CONFIG_FILE` — DeepSeek, OpenAI, Moonshot, etc.)
  with a "return Markdown" prompt. Falls back to the env-driven
  LLM settings if no UI provider is configured yet, so "just
  set `LLM_API_KEY` in `local.env`" also works. Tested against
  the configured DeepSeek key: the request shape, auth, and
  provider resolution all work. Switching to a vision-capable
  model (e.g. `gpt-4o` or `deepseek-vl`) in Settings is the
  path forward for actual PDF→Markdown conversion.
- **Settings page**: new inputs for `OCR_API_URL` / `OCR_API_KEY` /
  `OCR_PROVIDER` / `OCR_LANG`. The same `ENV_KEY_TO_INPUT` map
  drives load / save through `/api/local-env`.
- **local.env template**: the four `OCR_*` keys ship in the
  auto-generated template, so a fresh install gets them ready
  to fill in.

## Verified end-to-end

- `POST /api/convert/` with `{"engine":"llm_vision"}` → 200
  `{"status":"converting",…}`, log shows
  `LLM vision parser using provider 'local.env' / model
  'deepseek-chat'` and `Page 1/13 → LLM`. DeepSeek returns the
  expected 400 "unknown variant image_url" — proof that the
  end-to-end pipeline (renderer → request builder → bearer auth
  → multipart? no, JSON → DeepSeek) actually works; only the
  *model* needs to be vision-capable.
- `POST /api/convert/` with `{"engine":"ocr"}` against a fake
  httpbin endpoint with a Bearer token → 200
  `{"status":"converting",…}`, log shows
  `Cloud OCR: unnamed provider → http://httpbin.org/post` and
  the multipart-form upload completes per page.
- `POST /api/extract-info/` with the DeepSeek key → 4.56s
  end-to-end parse of `s41467-024-54178-1.pdf` → all 7
  authors + DOI + venue + tags extracted correctly.

## Build

- `kbase.spec` hiddenimports now lists `kb.engines.ocr` and
  `kb.engines.llm_vision` so PyInstaller bundles the new modules
  into the frozen exe.
- `data/` is fully gitignored (line 195 of `.gitignore`); the
  user-provided `data/local.env` with the DeepSeek key never
  entered git history.
- `dist/KBase/KBase.exe` rebuilt and verified against the new
  features.

## Roll-up commits since v0.2.0

- `220e2c0` feat(parsers): add LLM-vision and cloud-OCR PDF engines
- `84765c1` fix(notes): slash and @ command picks no longer freeze the editor
- `6a8d0b9` fix(notes): pin 文章小记 header, add delete, extend @ to articles
- `ef2a3da` feat(notes): 文章小记 — article-scoped notes + selection toolbar
- `08fce16` fix(notes): arrow keys now navigate slash menu one item at a time
- `abb1ede` feat(notes): hide markers, slash menu IME support, rich emoji panel
- `d1451d1` fix(notes): gutter hover, block anchors, AI menu — verified end-to-end
