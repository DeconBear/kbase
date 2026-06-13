"""Unisound U1 Doc Parser engine.

Asynchronous PDF parser via the Unisound MaaS API. Uploads the file,
submits a parser task, polls for completion, then downloads the
returned Markdown and saves it next to the source article.

Config (in data/local.env, written from the Settings page):
  UNISOUND_API_KEY   — Bearer token. Token Plan key starts with "tp-".
  UNISOUND_BASE_URL  — optional override. Defaults to
                       https://maas-api.hivoice.cn
  UNISOUND_MODEL     — optional model name. Defaults to "u1-ocr".

API contract (see C:/Users/qzx/Downloads/u1-ocr-parser-pro-1.0.3):
  POST {base}/v1/files/upload
       multipart: file=<pdf>, purpose=ocr_async_input
       -> { "file": { "file_id": "..." } }
  POST {base}/v1/files/parser/tasks
       json:     { "file_id": "...", "model": "u1-ocr" }
       -> { "task_id": "..." }
  GET  {base}/v1/files/parser/tasks/{task_id}
       -> { "status": "success|failed|...", "md_file_url": "...", ... }
  GET  {md_file_url}                          -> markdown text
"""
from __future__ import annotations

import json
import mimetypes
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from engines._paths import ARTICLES_DIR


DEFAULT_BASE_URL = "https://maas-api.hivoice.cn"
DEFAULT_MODEL = "u1-ocr"

_POLL_INTERVAL_SEC = 3
_TIMEOUT_SEC = 600            # 10 min — large PDFs need a while
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB ceiling (skill doesn't state one)


def _base_url() -> str:
    return os.environ.get("UNISOUND_BASE_URL", DEFAULT_BASE_URL).rstrip("/") or DEFAULT_BASE_URL


def _api_key() -> str:
    return os.environ.get("UNISOUND_API_KEY", "").strip()


def _model() -> str:
    return os.environ.get("UNISOUND_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# HTTP helpers (no `requests` dependency)
# ---------------------------------------------------------------------------


def _http_json(url: str, method: str, headers: dict, body: bytes | None = None,
               timeout: int = 60) -> dict:
    req = urllib.request.Request(url=url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _http_text(url: str, timeout: int = 60) -> str:
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} for {url}: {detail[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Request failed for {url}: {exc}") from exc


def _build_multipart(file_path: Path, purpose: str) -> tuple[bytes, str]:
    boundary = f"----KBaseUnisound{uuid.uuid4().hex}"
    mime = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts: list[bytes] = []
    # form field: purpose
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(f'Content-Disposition: form-data; name="purpose"\r\n\r\n'.encode())
    parts.append(purpose.encode("utf-8"))
    parts.append(b"\r\n")
    # file
    parts.append(f"--{boundary}\r\n".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{file_path.name}"\r\n'
        f"Content-Type: {mime}\r\n\r\n".encode()
    )
    parts.append(file_path.read_bytes())
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------------------
# Unisound API calls
# ---------------------------------------------------------------------------


def _upload(api_key: str, file_path: Path) -> str:
    base = _base_url()
    url = f"{base}/v1/files/upload"
    body, ctype = _build_multipart(file_path, purpose="ocr_async_input")
    resp = _http_json(
        url,
        "POST",
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": ctype,
        },
        body,
        timeout=180,
    )
    file_info = resp.get("file") or {}
    file_id = file_info.get("file_id")
    if not file_id:
        raise RuntimeError(
            f"Upload succeeded but no file_id: {json.dumps(resp, ensure_ascii=False)[:500]}"
        )
    return str(file_id)


def _create_task(api_key: str, file_id: str, model: str) -> str:
    base = _base_url()
    url = f"{base}/v1/files/parser/tasks"
    payload = json.dumps({"file_id": file_id, "model": model}).encode("utf-8")
    resp = _http_json(
        url,
        "POST",
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        payload,
        timeout=60,
    )
    for key in ("task_id", "id"):
        if resp.get(key):
            return str(resp[key])
    task_info = resp.get("task") or {}
    for key in ("task_id", "id"):
        if task_info.get(key):
            return str(task_info[key])
    raise RuntimeError(
        f"Task created but no task_id: {json.dumps(resp, ensure_ascii=False)[:500]}"
    )


def _detect_status(resp: dict) -> str:
    for candidate in (
        resp.get("status"),
        (resp.get("task") or {}).get("status"),
        (resp.get("data") or {}).get("status"),
    ):
        if candidate:
            return str(candidate).lower()
    return "unknown"


def _is_success(status: str) -> bool:
    return status in {"success", "succeeded", "completed", "done", "finished"}


def _is_failure(status: str) -> bool:
    return status in {"failed", "error", "cancelled", "canceled"}


def _poll(api_key: str, task_id: str, log) -> dict:
    base = _base_url()
    url = f"{base}/v1/files/parser/tasks/{urllib.parse.quote(str(task_id))}"
    deadline = time.time() + _TIMEOUT_SEC
    last: dict = {}
    poll_idx = 0
    while time.time() < deadline:
        last = _http_json(
            url,
            "GET",
            {"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        status = _detect_status(last)
        poll_idx += 1
        if _is_success(status):
            log(f"  Poll {poll_idx}: success")
            return last
        if _is_failure(status):
            raise RuntimeError(
                f"Task ended in failure state '{status}': "
                f"{json.dumps(last, ensure_ascii=False)[:500]}"
            )
        if poll_idx % 10 == 1:
            log(f"  Poll {poll_idx}: {status}...")
        time.sleep(_POLL_INTERVAL_SEC)
    raise RuntimeError(
        f"Polling timed out after {_TIMEOUT_SEC}s. Last response: "
        f"{json.dumps(last, ensure_ascii=False)[:500]}"
    )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class UnisoundParserEngine:
    """Uploads a PDF to Unisound MaaS, polls the parser task, saves .md."""
    name = "unisound"

    def run(self, pdf_path: str, article_id: str, log_callback=None, **kwargs) -> bool:
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        api_key = _api_key()
        if not api_key:
            log("ERROR: UNISOUND_API_KEY not configured. Set it in local.env or Settings.")
            return False

        pdf = Path(pdf_path)
        if not pdf.exists() or not pdf.is_file():
            log(f"ERROR: PDF not found: {pdf}")
            return False
        size_mb = pdf.stat().st_size / (1024 * 1024)
        if pdf.stat().st_size > _MAX_UPLOAD_BYTES:
            log(f"ERROR: PDF is {size_mb:.1f} MB, exceeds 100 MB limit")
            return False

        model = _model()
        log(f"Unisound U1 parser: model={model}, base={_base_url()}")
        log(f"File: {pdf.name} ({size_mb:.1f} MB)")

        try:
            # 1. Upload
            log("Step 1/4: uploading PDF...")
            file_id = _upload(api_key, pdf)
            log(f"  file_id = {file_id}")

            # 2. Create parser task
            log("Step 2/4: creating parser task...")
            task_id = _create_task(api_key, file_id, model)
            log(f"  task_id = {task_id}")

            # 3. Poll
            log("Step 3/4: waiting for parser...")
            final = _poll(api_key, task_id, log)

            # 4. Fetch result markdown
            md_url = final.get("md_file_url")
            if not md_url:
                raise RuntimeError(
                    f"Task succeeded but no md_file_url: "
                    f"{json.dumps(final, ensure_ascii=False)[:500]}"
                )
            log("Step 4/4: downloading markdown...")
            markdown = _http_text(md_url, timeout=180)
            if not markdown or not markdown.strip():
                log("ERROR: empty markdown returned")
                return False

            article_dir = ARTICLES_DIR / article_id
            article_dir.mkdir(parents=True, exist_ok=True)
            md_path = article_dir / f"{article_id}.md"
            md_path.write_text(markdown, encoding="utf-8")
            log(f"Saved: {md_path.name} ({len(markdown)} chars)")

            # meta
            meta_path = article_dir / f"{article_id}_meta.json"
            meta: dict = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            data_info = final.get("data_info") or {}
            meta.update({
                "source": "unisound",
                "unisound_model": model,
                "unisound_file_id": file_id,
                "unisound_task_id": task_id,
                "unisound_pages": data_info.get("num_pages"),
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR: Unisound parser failed: {exc}")
            return False
