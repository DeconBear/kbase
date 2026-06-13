"""Cloud OCR PDF parser.

Sends each PDF page (rendered to PNG) to a configured cloud OCR
provider (云知声 etc.) and stitches the per-page Markdown /
plain-text responses together.

Config (in data/local.env):
  OCR_API_URL  — endpoint that accepts multipart/form-data with
                 a `file` field (the page PNG) and returns either
                 `{ "text": "...markdown..." }` or a `{"pages":[{"text":...}]}`
                 envelope. We accept both.
  OCR_API_KEY  — bearer token; sent as `Authorization: Bearer ...`.
  OCR_PROVIDER — display name only (e.g. "yunzhisheng", "tencent-ocr").
                 Not used for routing — just persisted into the
                 _meta.json so the user can see which engine ran.
  OCR_LANG     — optional language hint (default: "zh-CN+en").

The endpoint contract is intentionally small so any HTTP OCR service
that can return text from an image works without code changes.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

from engines._paths import ARTICLES_DIR


def _load_config() -> dict:
    return {
        "api_url": os.environ.get("OCR_API_URL", "").strip(),
        "api_key": os.environ.get("OCR_API_KEY", "").strip(),
        "provider": os.environ.get("OCR_PROVIDER", "").strip(),
        "lang": os.environ.get("OCR_LANG", "zh-CN+en").strip(),
    }


def _upload_file(url: str, api_key: str, png_bytes: bytes, page_idx: int,
                 lang: str, log) -> str:
    """POST a single page PNG to the OCR endpoint. Returns the
    recognized text/markdown for that page."""
    # Build multipart/form-data by hand so we don't add a dependency
    # on requests. Boundary is unique per call.
    import uuid
    boundary = "----kb-ocr-" + uuid.uuid4().hex
    body = []
    # File part
    body.append(f"--{boundary}\r\n".encode("utf-8"))
    body.append(
        f'Content-Disposition: form-data; name="file"; filename="page-{page_idx}.png"\r\n'
        f"Content-Type: image/png\r\n\r\n".encode("utf-8")
    )
    body.append(png_bytes)
    body.append(b"\r\n")
    # Optional language field
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
    # The endpoint can return a bare markdown string or JSON.
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
    # Fallback: dump the whole object as a string.
    return json.dumps(data, ensure_ascii=False, indent=2)


class CloudOcrEngine:
    """Render each PDF page to PNG and POST to a cloud OCR provider."""
    name = "ocr"

    def run(self, pdf_path: str, article_id: str, log_callback=None):
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        cfg = _load_config()
        if not (cfg["api_url"] and cfg["api_key"]):
            log("ERROR: cloud OCR not configured. Set OCR_API_URL and OCR_API_KEY in local.env.")
            return False
        log(f"Cloud OCR: {cfg['provider'] or 'unnamed provider'} → {cfg['api_url']}")

        doc = None
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            log(f"Total pages: {total_pages}")

            pages_md: list[str] = []
            for page_idx in range(total_pages):
                log(f"OCR page {page_idx + 1}/{total_pages}...")
                page = doc.load_page(page_idx)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 0), alpha=False)
                png_bytes = pix.tobytes("png")
                try:
                    text = _upload_file(
                        cfg["api_url"], cfg["api_key"], png_bytes,
                        page_idx + 1, cfg["lang"], log,
                    )
                except urllib.error.HTTPError as e:
                    body = ""
                    try: body = e.read().decode("utf-8", errors="replace")
                    except Exception: pass
                    log(f"ERROR: OCR returned HTTP {e.code}: {body[:300]}")
                    return False
                except Exception as e:
                    log(f"ERROR: OCR page {page_idx + 1} failed: {e}")
                    return False
                if not text:
                    text = f"<!-- empty page {page_idx + 1} -->"
                pages_md.append(text)

            final = "\n\n---\n\n".join(pages_md).strip() + "\n"
            article_dir = ARTICLES_DIR / article_id
            article_dir.mkdir(parents=True, exist_ok=True)
            (article_dir / f"{article_id}.md").write_text(final, encoding="utf-8")

            meta_path = article_dir / f"{article_id}_meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            meta.update({
                "source": "ocr",
                "ocr_provider": cfg["provider"] or "unnamed",
                "ocr_lang": cfg["lang"],
                "pages": total_pages,
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"Cloud OCR done → {article_id}.md ({len(final)} chars)")
            return True
        except Exception as e:
            log(f"ERROR: Cloud OCR failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
