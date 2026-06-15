# AGENTS.md

Instructions for AI coding agents working in this repository. For the full codegraph, module reference, API map, and storage schema, see [CLAUDE.md](./CLAUDE.md).

## Rules of engagement

### Before any change
1. Read the **Codegraph** section in CLAUDE.md — it maps module dependencies, function signatures, API routes, and storage schema.
2. Check the **Things that will bite you** section in CLAUDE.md for common footguns.
3. Use `Grepping` to find existing patterns, never guess.

### After any non-trivial change
1. **Update the Codegraph** in CLAUDE.md if you added/modified: modules, endpoints, engines, function signatures, storage schema.
2. Verify `python -c "import py_compile; py_compile.compile('<file>', doraise=True)"` on every `.py` file touched.

### Never (unless explicitly asked)
- `git push` — local commits only. Wait for the user to say "push" or "发布".
- Create a release (`gh release create`, tagging).
- Add a test suite.
- Add a linter/formatter/type-checker config.
- Modify `data/` or `plan/` contents in git (both are gitignored).

### Always
- Keep `CLAUDE.md` up to date — it's the single source of truth for the codebase structure.
- Use `abortableFetch` in the frontend for navigation-sensitive requests.
- Use atomic writes (`os.replace` via tmp file) for any file the user could be reading.
- Validate user-controlled paths through `validate_article_id`/`validate_note_id` before `Path(...)`.
- Use the defensive LLM response pattern: `(data.get("choices") or [{}])[0].get("message", {}).get("content") or ""`.
- Add new engines via the `engines/<name>.py` + `ENGINES` dict pattern.
- Follow the `from __future__ import annotations` + type hints convention.

## Project identity

- **Name**: KBase
- **What**: AI-powered, local-first knowledge management desktop app
- **Tech**: Python backend (stdlib HTTP server) + vanilla JS frontend (SPA) + pywebview desktop shell
- **Platforms**: Windows (primary, NSIS installer + portable zip), Linux/macOS (headless mode via Start-KBase.sh)
- **Git**: `DeconBear/kbase`, master branch, protected (PR required, 1 review, stale-dismiss, no force push)
- **CI**: GitHub Actions `build-release.yml`, triggered on `v*` tag, builds PyInstaller + NSIS, creates draft release
- **Version**: see `kb/version.py`
