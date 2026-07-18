"""Vision LLM OCR — page-by-page, range/resume aware."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

import fitz  # PyMuPDF

from engines._paths import ARTICLES_DIR, LOW_MEMORY_CONFIG as RUNTIME_CONFIG
from engines.page_ocr_common import (
    ConversionCancelled,
    clear_checkpoint,
    publish_stitched,
    run_page_loop,
)


def image_to_base64(pix):
    png_data = pix.tobytes("png")
    return base64.b64encode(png_data).decode("utf-8")


def _load_settings():
    if not RUNTIME_CONFIG.exists():
        return {}
    try:
        return json.loads(RUNTIME_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _api_endpoint(api_type, api_url):
    api_url = str(api_url or "").strip().rstrip("/")
    if not api_url:
        return ""
    if api_type == "anthropic":
        return api_url if api_url.endswith("/messages") else f"{api_url}/v1/messages"
    if api_url.endswith("/chat/completions"):
        return api_url
    return f"{api_url}/chat/completions"


class VisionOcrEngine:
    name = "vision"

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
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        log("Starting Vision OCR engine")

        settings = _load_settings()
        active_id = settings.get("active_vision_provider", "default")
        providers = settings.get("vision_providers", [])
        active_provider = next((p for p in providers if p.get("id") == active_id), {})
        if not active_provider and providers:
            active_provider = providers[0]

        api_type = active_provider.get("type", "openai")
        api_url = _api_endpoint(api_type, active_provider.get("url", ""))
        api_key = active_provider.get("key", "").strip()
        model_name = active_provider.get("model", "").strip()

        if not api_url or not api_key or not model_name:
            log("ERROR: Vision OCR settings are incomplete. Configure API URL, key, and model in Settings.")
            return False

        doc = None
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            log(f"Total pages to process: {total_pages}")

            prompt = (
                "Please extract all text, formulas, code, and tables from this image "
                "and format it precisely as Markdown. Return only Markdown content."
            )

            def process_page(page_num: int) -> str:
                page = doc.load_page(page_num - 1)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                base64_img = image_to_base64(pix)

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                }

                if api_type == "anthropic":
                    headers["x-api-key"] = api_key
                    headers["anthropic-version"] = "2023-06-01"
                    headers.pop("Authorization", None)
                    payload = {
                        "model": model_name,
                        "max_tokens": 4096,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {
                                    "type": "image",
                                    "source": {
                                        "type": "base64",
                                        "media_type": "image/png",
                                        "data": base64_img,
                                    },
                                },
                                {"type": "text", "text": prompt},
                            ],
                        }],
                    }
                else:
                    payload = {
                        "model": model_name,
                        "messages": [{
                            "role": "user",
                            "content": [
                                {"type": "text", "text": prompt},
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/png;base64,{base64_img}",
                                    },
                                },
                            ],
                        }],
                    }

                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=180) as response:
                    res_json = json.loads(response.read().decode("utf-8"))

                if api_type == "anthropic":
                    page_text = res_json.get("content", [{}])[0].get("text", "")
                else:
                    page_text = (res_json.get("choices") or [{}])[0].get("message", {}).get("content", "")
                return (page_text or "").strip()

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
            )
            if status != "done":
                return False

            output_path = publish_stitched(
                article_id, pdf_path, start, end, total_pages, partial=False,
            )
            clear_checkpoint(article_id, remove_pages=True)

            meta_path = ARTICLES_DIR / article_id / f"{article_id}_meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            meta.update({
                "source": "vision",
                "vision_provider": active_provider.get("name") or active_id,
                "vision_model": model_name,
                "pages": total_pages,
                "ocr_range": f"{start}-{end}",
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"Vision OCR complete: {output_path.name} (pages {start}-{end})")
            return True
        except ConversionCancelled:
            raise
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            log(f"ERROR: Vision OCR request failed (HTTP {e.code}): {body[:500]}")
            return False
        except Exception as e:
            log(f"ERROR: Vision OCR failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
