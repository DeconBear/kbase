#!/usr/bin/env python3
"""Standalone smoke test for the Unisound U1 parser engine.

Run from the repo root:

    python scripts/test_unisound_parse.py path/to/file.pdf

The script:
  1. Loads the local .env / .env.local (gitignored) and the unisound
     Token Plan key from there, OR prompts you to paste it.
  2. Uploads the PDF to the Unisound MaaS endpoint.
  3. Submits a parser task, polls until done, downloads the markdown.
  4. Writes the markdown next to the source PDF with a `.out.md` suffix
     and prints a short summary (title, first lines, char count, etc.).

The engine is the same one used inside KBase (``kb.engines.unisound_parser``),
so a successful run here proves the wiring works end to end.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
KB_DIR = REPO_ROOT / "kb"


def _bootstrap_env() -> None:
    """Source repo-root .env / .env.local without requiring the user to
    install python-dotenv (it's already a project dep, but we keep this
    script dependency-free on the stdlib side)."""
    for name in (".env", ".env.local"):
        path = REPO_ROOT / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            v = val.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
                v = v[1:-1]
            os.environ.setdefault(key.strip(), v)


def _ensure_api_key() -> str:
    api_key = os.environ.get("UNISOUND_API_KEY", "").strip()
    if api_key:
        return api_key
    if not sys.stdin.isatty():
        sys.exit("UNISOUND_API_KEY not set; aborting")
    try:
        entered = input("UNISOUND_API_KEY (Token Plan, starts with 'tp-'): ").strip()
    except EOFError:
        sys.exit("UNISOUND_API_KEY not provided")
    if not entered:
        sys.exit("Empty key, aborting")
    os.environ["UNISOUND_API_KEY"] = entered
    return entered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", help="Path to a PDF file to parse")
    parser.add_argument("--model", default=os.environ.get("UNISOUND_MODEL", "u1-ocr"),
                        help="Parser model (default: u1-ocr)")
    parser.add_argument("--poll-interval", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--start-page", type=int, default=None)
    parser.add_argument("--end-page", type=int, default=None)
    parser.add_argument("--out", default=None,
                        help="Where to save the markdown; default: <pdf>.out.md")
    args = parser.parse_args()

    _bootstrap_env()
    _ensure_api_key()
    if args.model:
        os.environ["UNISOUND_MODEL"] = args.model

    pdf = Path(args.pdf).expanduser().resolve()
    if not pdf.exists():
        sys.exit(f"PDF not found: {pdf}")
    if not pdf.is_file():
        sys.exit(f"Not a regular file: {pdf}")
    size_mb = pdf.stat().st_size / (1024 * 1024)
    print(f"PDF: {pdf}  ({size_mb:.2f} MB)")

    # Import the engine after env is set so UNISOUND_* envs are visible.
    sys.path.insert(0, str(KB_DIR))
    from engines.unisound_parser import (  # type: ignore
        UnisoundParserEngine,
        _base_url,
        _api_key,
        _model,
        _upload,
        _create_task,
        _poll,
        _http_text,
    )

    print(f"Base URL: {_base_url()}")
    print(f"Model:    {_model()}")
    print(f"Key:      {_api_key()[:6]}…{_api_key()[-4:]}  (length={len(_api_key())})")
    print()

    print("Step 1/4: uploading PDF…")
    file_id = _upload(_api_key(), pdf)
    print(f"  file_id = {file_id}")

    print("Step 2/4: creating parser task…")
    task_id = _create_task(_api_key(), file_id, _model())
    print(f"  task_id = {task_id}")

    print("Step 3/4: polling…")
    def log(msg: str) -> None:
        print(f"  {msg}")
    final = _poll(_api_key(), task_id, log)
    print(f"  status   = {final.get('status')}")
    data_info = final.get("data_info") or {}
    if data_info.get("num_pages"):
        print(f"  pages    = {data_info['num_pages']}")

    md_url = final.get("md_file_url")
    if not md_url:
        sys.exit(f"Task succeeded but no md_file_url: {final}")

    print("Step 4/4: downloading markdown…")
    markdown = _http_text(md_url)
    out_path = Path(args.out) if args.out else pdf.with_suffix(".out.md")
    out_path.write_text(markdown, encoding="utf-8")

    lines = [line for line in markdown.splitlines() if line.strip()]
    title = ""
    for line in lines:
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            break
    if not title and lines:
        title = lines[0]

    print()
    print(f"Wrote: {out_path}  ({len(markdown)} chars, {len(lines)} non-blank lines)")
    if title:
        print(f"Title: {title}")
    preview = " ".join(lines[:3]).strip()
    if preview:
        print(f"Head:  {preview[:160]}{'…' if len(preview) > 160 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
