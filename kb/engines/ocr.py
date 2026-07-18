"""Cloud OCR PDF parser (page-by-page, range/resume aware).

Supports:
  - ``custom`` / ``unisound``: multipart PNG upload to a generic OCR HTTP API
  - ``qwen``: 通义千问 / 百炼 Qwen-VL-OCR via OpenAI-compatible chat completions

By default, pages with a usable PDF text layer skip the cloud call (0 tokens).
Pass ``force_ocr=True`` to OCR every page as an image.
"""
from __future__ import annotations

import base64
import json
import os
import re
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
TEXT_LAYER_MIN_CHARS = 80

_QWEN_PROMPT = (
    "Please extract every readable element from this document page image — "
    "text, headings, lists, tables, formulas, code, captions, footnotes — "
    "and return clean Markdown. Preserve the original language. "
    "Do not summarize. Do not wrap the whole page in a markdown code fence. "
    "Return ONLY Markdown."
)


def _load_config() -> dict:
    ptype = (os.environ.get("OCR_PROVIDER_TYPE") or "custom").strip().lower()
    api_url = os.environ.get("OCR_API_URL", "").strip()
    model = os.environ.get("OCR_MODEL", "").strip()
    provider = os.environ.get("OCR_PROVIDER", "").strip()
    url_l = api_url.lower()
    model_l = model.lower()
    # Auto-detect 千问 / DashScope even when OCR_PROVIDER_TYPE still says custom.
    if (
        ptype in {"qwen", "qwen-vl-ocr", "dashscope"}
        or "dashscope" in url_l
        or "compatible-mode" in url_l
        or "qwen-vl-ocr" in model_l
        or model_l.startswith("qwen")
    ):
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


def _clean_text_layer(text: str) -> str:
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"[ \t]+\n", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _upload_file(url: str, api_key: str, png_bytes: bytes, page_idx: int,
                 lang: str) -> tuple[str, dict]:
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
        return raw.strip(), {}
    usage = data.get("usage") if isinstance(data, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    if isinstance(data, str):
        return data.strip(), usage
    if "text" in data:
        return str(data["text"]).strip(), usage
    if "pages" in data and isinstance(data["pages"], list):
        return "\n\n".join(str(p.get("text", "")).strip() for p in data["pages"]).strip(), usage
    if "data" in data and isinstance(data["data"], dict):
        return str(data["data"].get("text", "")).strip(), usage
    return json.dumps(data, ensure_ascii=False, indent=2), usage


def _qwen_ocr_page(url: str, api_key: str, model: str, png_bytes: bytes) -> tuple[str, dict]:
    """Call DashScope / 千问 OpenAI-compatible Qwen-VL-OCR. Returns (text, usage)."""
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
    text = ((data.get("choices") or [{}])[0].get("message", {}).get("content") or "").strip()
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return text, usage


def _accumulate_usage(state: dict, usage: dict | None) -> None:
    if not usage:
        return
    for src, dst in (
        ("prompt_tokens", "prompt_tokens"),
        ("completion_tokens", "completion_tokens"),
        ("total_tokens", "total_tokens"),
        ("input_tokens", "prompt_tokens"),
        ("output_tokens", "completion_tokens"),
    ):
        if src in usage and usage[src] is not None:
            try:
                state[dst] = int(state.get(dst) or 0) + int(usage[src])
            except (TypeError, ValueError):
                pass
    if not state.get("total_tokens"):
        state["total_tokens"] = int(state.get("prompt_tokens") or 0) + int(
            state.get("completion_tokens") or 0
        )


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
        force_ocr: bool = False,
        **_kwargs,
    ):
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        cfg = _load_config()
        # Text-layer-only path still works without API key.
        need_api = True  # may become False if all pages use text layer; check lazily
        if force_ocr and not cfg["api_key"]:
            log("ERROR: cloud OCR not configured. Set OCR_API_KEY (and OCR_API_URL if needed) in Settings.")
            return False
        if cfg["provider_type"] != "qwen" and force_ocr and not cfg["api_url"]:
            log("ERROR: OCR_API_URL is required for custom/unisound OCR.")
            return False

        label = cfg["provider"] or cfg["provider_type"]
        if force_ocr:
            log("Mode: force cloud OCR (ignore text layer)")
        else:
            log("Mode: text-layer first · cloud OCR only for sparse/empty pages")
        if cfg["provider_type"] == "qwen":
            log(f"Cloud OCR: 千问 Qwen-VL-OCR ({cfg['model']}) → {_normalize_chat_url(cfg['api_url'])}")
        else:
            log(f"Cloud OCR: {label} → {cfg['api_url'] or '(text-layer only if no URL)'}")

        usage_state: dict = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "pages_text": 0,
            "pages_ocr": 0,
        }
        doc = None
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            log(f"Total pages: {total_pages}")

            def process_page(page_num: int) -> str:
                nonlocal need_api
                page = doc.load_page(page_num - 1)
                if not force_ocr:
                    layer = _clean_text_layer(page.get_text("text"))
                    if len(layer) >= TEXT_LAYER_MIN_CHARS:
                        usage_state["pages_text"] = int(usage_state.get("pages_text") or 0) + 1
                        return layer

                if not cfg["api_key"]:
                    usage_state["pages_text"] = int(usage_state.get("pages_text") or 0) + 1
                    layer = _clean_text_layer(page.get_text("text"))
                    return layer or f"<!-- empty page {page_num} · no OCR key -->"

                need_api = True
                if cfg["provider_type"] != "qwen" and not cfg["api_url"]:
                    raise RuntimeError("OCR_API_URL is required for custom/unisound OCR.")

                layer = _clean_text_layer(page.get_text("text"))
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                png_bytes = pix.tobytes("png")
                try:
                    if cfg["provider_type"] == "qwen":
                        text, usage = _qwen_ocr_page(
                            cfg["api_url"], cfg["api_key"], cfg["model"], png_bytes,
                        )
                    else:
                        text, usage = _upload_file(
                            cfg["api_url"], cfg["api_key"], png_bytes,
                            page_num, cfg["lang"],
                        )
                except Exception as exc:  # noqa: BLE001
                    # Don't abort the whole book on one bad page / API glitch.
                    log(f"WARN: cloud OCR page {page_num} failed ({exc}); using text layer fallback")
                    usage_state["pages_text"] = int(usage_state.get("pages_text") or 0) + 1
                    return layer or f"<!-- empty page {page_num} · OCR failed -->"
                _accumulate_usage(usage_state, usage)
                usage_state["pages_ocr"] = int(usage_state.get("pages_ocr") or 0) + 1
                return text or layer

            def publish_partial(start: int, page_num: int) -> None:
                publish_stitched(
                    article_id, pdf_path, start, page_num, total_pages, partial=True,
                )

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
                publish_partial=publish_partial,
                usage_state=usage_state,
            )
            if status != "done":
                return False

            publish_stitched(article_id, pdf_path, start, end, total_pages, partial=False)
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
                "force_ocr": bool(force_ocr),
                "pages_text": usage_state.get("pages_text"),
                "pages_ocr": usage_state.get("pages_ocr"),
                "prompt_tokens": usage_state.get("prompt_tokens"),
                "completion_tokens": usage_state.get("completion_tokens"),
                "total_tokens": usage_state.get("total_tokens"),
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(
                f"Cloud OCR done → pages {start}-{end} / {total_pages}"
                f" · text-layer {usage_state.get('pages_text', 0)}"
                f" · cloud {usage_state.get('pages_ocr', 0)}"
                f" · tokens {usage_state.get('total_tokens', 0)}"
                f" (in {usage_state.get('prompt_tokens', 0)} / out {usage_state.get('completion_tokens', 0)})"
            )
            return True
        except ConversionCancelled:
            raise
        except Exception as e:
            log(f"ERROR: Cloud OCR failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
