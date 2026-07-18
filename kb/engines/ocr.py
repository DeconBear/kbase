"""Cloud OCR PDF parser (page-by-page, range/resume aware).

Supports:
  - ``custom`` / ``unisound``: multipart PNG upload to a generic OCR HTTP API
  - ``qwen``: 通义千问 / 百炼 Qwen-VL-OCR via OpenAI-compatible chat completions
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

import fitz  # PyMuPDF

from engines._paths import ARTICLES_DIR
from engines.page_ocr_common import (
    ConversionCancelled,
    clear_checkpoint,
    publish_stitched,
    run_page_loop,
)

QWEN_DEFAULT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
QWEN_DEFAULT_MODEL = "qwen-vl-ocr-latest"

_QWEN_PROMPT = (
    "Please extract every readable element from this document page image — "
    "text, headings, lists, tables, formulas, code, captions, footnotes — "
    "and return clean Markdown. Preserve the original language. "
    "Do not summarize. Return ONLY Markdown."
)


def _load_config() -> dict:
    ptype = (os.environ.get("OCR_PROVIDER_TYPE") or "custom").strip().lower()
    api_url = os.environ.get("OCR_API_URL", "").strip()
    model = os.environ.get("OCR_MODEL", "").strip()
    provider = os.environ.get("OCR_PROVIDER", "").strip()
    if ptype in {"qwen", "qwen-vl-ocr", "dashscope"}:
        ptype = "qwen"
        api_url = api_url or QWEN_DEFAULT_URL
        model = model or QWEN_DEFAULT_MODEL
        provider = provider or "qwen"
    return {
        "provider_type": ptype,
        "api_url": api_url,
        "api_key": os.environ.get("OCR_API_KEY", "").strip(),
        "provider": provider,
        "model": model,
        "lang": os.environ.get("OCR_LANG", "zh-CN+en").strip(),
    }


def _normalize_chat_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        return QWEN_DEFAULT_URL
    if u.endswith("/chat/completions"):
        return u
    if u.endswith("/compatible-mode/v1"):
        return u + "/chat/completions"
    if u.endswith("/v1"):
        return u + "/chat/completions"
    return u


def _upload_file(url: str, api_key: str, png_bytes: bytes, page_idx: int,
                 lang: str) -> str:
    import uuid
    boundary = "----kb-ocr-" + uuid.uuid4().hex
    body = []
    body.append(f"--{boundary}\r\n".encode("utf-8"))
    body.append(
        f'Content-Disposition: form-data; name="file"; filename="page-{page_idx}.png"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode("utf-8")
    )
    body.append(png_bytes)
    body.append(b"\r\n")
    if lang:
        body.append(f"--{boundary}\r\n".encode("utf-8"))
        body.append(f'Content-Disposition: form-data; name="lang"\r\n\r\n'.encode("utf-8"))
        body.append(lang.encode("utf-8"))
        body.append(b"\r\n")
    body.append(f"--{boundary}--\r\n".encode("utf-8"))
    payload = b"".join(body)

    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw.strip()
    if isinstance(data, str):
        return data.strip()
    if "text" in data:
        return str(data["text"]).strip()
    if "pages" in data and isinstance(data["pages"], list):
        return "\n\n".join(str(p.get("text", "")).strip() for p in data["pages"]).strip()
    if "data" in data and isinstance(data["data"], dict):
        return str(data["data"].get("text", "")).strip()
    return json.dumps(data, ensure_ascii=False, indent=2)


def _qwen_ocr_page(url: str, api_key: str, model: str, png_bytes: bytes) -> str:
    """Call DashScope / 千问 OpenAI-compatible Qwen-VL-OCR."""
    endpoint = _normalize_chat_url(url)
    b64 = base64.b64encode(png_bytes).decode("ascii")
    payload = {
        "model": model or QWEN_DEFAULT_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
                {"type": "text", "text": _QWEN_PROMPT},
            ],
        }],
    }
    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8", errors="replace"))
    return ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()


class CloudOcrEngine:
    """Render each PDF page to PNG and OCR via configured cloud provider."""
    name = "ocr"

    def run(
        self,
        pdf_path: str,
        article_id: str,
        log_callback=None,
        *,
        page_from: int | None = None,
        page_to: int | None = None,
        resume: bool = False,
        progress_callback=None,
        should_cancel=None,
        **_kwargs,
    ):
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        cfg = _load_config()
        if not cfg["api_key"]:
            log("ERROR: cloud OCR not configured. Set OCR_API_KEY (and OCR_API_URL if needed) in Settings.")
            return False
        if cfg["provider_type"] != "qwen" and not cfg["api_url"]:
            log("ERROR: OCR_API_URL is required for custom/unisound OCR.")
            return False

        label = cfg["provider"] or cfg["provider_type"]
        if cfg["provider_type"] == "qwen":
            log(f"Cloud OCR: 千问 Qwen-VL-OCR ({cfg['model']}) → {_normalize_chat_url(cfg['api_url'])}")
        else:
            log(f"Cloud OCR: {label} → {cfg['api_url']}")

        doc = None
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            log(f"Total pages: {total_pages}")

            def process_page(page_num: int) -> str:
                page = doc.load_page(page_num - 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 0), alpha=False)
                png_bytes = pix.tobytes("png")
                try:
                    if cfg["provider_type"] == "qwen":
                        return _qwen_ocr_page(
                            cfg["api_url"], cfg["api_key"], cfg["model"], png_bytes,
                        )
                    return _upload_file(
                        cfg["api_url"], cfg["api_key"], png_bytes,
                        page_num, cfg["lang"],
                    )
                except urllib.error.HTTPError as e:
                    body = ""
                    try:
                        body = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    raise RuntimeError(f"OCR HTTP {e.code}: {body[:300]}") from e

            status, start, end = run_page_loop(
                article_id=article_id,
                engine=self.name,
                total_pages=total_pages,
                page_from=page_from,
                page_to=page_to,
                resume=resume,
                should_cancel=should_cancel,
                progress_callback=progress_callback,
                process_page=process_page,
                log=log,
            )
            if status != "done":
                return False

            publish_stitched(article_id, pdf_path, start, end, total_pages)
            clear_checkpoint(article_id, remove_pages=True)

            meta_path = ARTICLES_DIR / article_id / f"{article_id}_meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            meta.update({
                "source": "ocr",
                "ocr_provider_type": cfg["provider_type"],
                "ocr_provider": label,
                "ocr_model": cfg["model"],
                "ocr_lang": cfg["lang"],
                "pages": total_pages,
                "ocr_range": f"{start}-{end}",
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"Cloud OCR done → pages {start}-{end} / {total_pages}")
            return True
        except ConversionCancelled:
            raise
        except Exception as e:
            log(f"ERROR: Cloud OCR failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
