"""LLM-based PDF parser — page-by-page, range/resume aware."""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request

import fitz  # PyMuPDF

from engines.page_ocr_common import (
    ConversionCancelled,
    article_dir,
    clear_checkpoint,
    publish_stitched,
    run_page_loop,
)
from llm_config import load_llm_config, resolve_llm_settings


_PROMPT = (
    "Extract every readable element from this PDF page — text, headings, "
    "lists, tables, formulas, code, captions, footnotes — and return the "
    "result as clean Markdown. Preserve the original language. "
    "Do not summarize. Do not omit anything visible. Return ONLY Markdown."
)


def _page_to_base64_png(page, zoom: float = 2.0) -> tuple[str, int, int]:
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    return (
        base64.b64encode(pix.tobytes("png")).decode("ascii"),
        pix.width,
        pix.height,
    )


def _build_payload(api_type: str, model: str, base64_img: str, width: int, height: int) -> dict:
    if api_type == "anthropic":
        return {
            "model": model,
            "max_tokens": 4096,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": "image/png", "data": base64_img}},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        }
    return {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{base64_img}", "detail": "high"}},
            ],
        }],
    }


def _extract_text(api_type: str, res_json: dict) -> str:
    if api_type == "anthropic":
        parts = res_json.get("content") or []
        return "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return (res_json.get("choices") or [{}])[0].get("message", {}).get("content", "")


class LlmVisionEngine:
    name = "llm_vision"

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

        api_type = "openai"
        api_url = ""
        api_key = ""
        model_name = ""
        provider_label = ""
        try:
            cfg = load_llm_config()
            providers = cfg.get("providers") or []
            active_id = cfg.get("active_provider")
            chosen = next((p for p in providers if p.get("id") == active_id), None) \
                     or (providers[0] if providers else None)
            if chosen:
                api_type = chosen.get("type", "openai") or "openai"
                api_url = (chosen.get("api_url") or "").strip()
                api_key = (chosen.get("api_key") or "").strip()
                model_name = (chosen.get("model") or "").strip()
                provider_label = chosen.get("name") or chosen.get("id") or "configured"
        except Exception as e:
            log(f"WARN: failed to read llm_config: {e}")

        if not (api_url and api_key and model_name):
            try:
                env = resolve_llm_settings()
                if env.get("api_url") and env.get("api_key") and env.get("model"):
                    api_url = api_url or env["api_url"]
                    api_key = api_key or env["api_key"]
                    model_name = model_name or env["model"]
                    provider_label = provider_label or env.get("provider_name") or "env"
            except Exception as e:
                log(f"WARN: env-fallback failed: {e}")

        api_url = (api_url or "").strip().rstrip("/")
        if api_type == "anthropic" and not api_url.endswith("/messages"):
            api_url = f"{api_url}/v1/messages" if api_url else ""
        elif api_url and not api_url.endswith("/chat/completions"):
            api_url = f"{api_url}/chat/completions"

        if not (api_url and api_key and model_name):
            log("ERROR: LLM vision parser needs a configured LLM provider (set one in Settings → LLM).")
            return False
        log(f"LLM vision parser using provider '{provider_label}' / model '{model_name}'")

        doc = None
        try:
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            log(f"Total pages: {total_pages}")

            headers = {"Content-Type": "application/json"}
            if api_type == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {api_key}"

            def process_page(page_num: int) -> str:
                page = doc.load_page(page_num - 1)
                b64, w, h = _page_to_base64_png(page)
                payload = _build_payload(api_type, model_name, b64, w, h)
                req = urllib.request.Request(
                    api_url,
                    data=json.dumps(payload).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(req, timeout=180) as resp:
                        res = json.loads(resp.read().decode("utf-8"))
                except urllib.error.HTTPError as e:
                    body = ""
                    try:
                        body = e.read().decode("utf-8", errors="replace")
                    except Exception:
                        pass
                    raise RuntimeError(f"LLM HTTP {e.code}: {body[:300]}") from e
                text = _extract_text(api_type, res).strip()
                if not text:
                    log(f"WARN: page {page_num} returned empty content")
                return text

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

            publish_stitched(article_id, pdf_path, start, end, total_pages, partial=False)
            clear_checkpoint(article_id, remove_pages=True)

            meta_path = article_dir(article_id) / f"{article_id}_meta.json"
            meta = {}
            if meta_path.exists():
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            meta.update({
                "source": "llm_vision",
                "vision_provider": provider_label,
                "vision_model": model_name,
                "pages": total_pages,
                "ocr_range": f"{start}-{end}",
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"LLM vision parsing done → pages {start}-{end} / {total_pages}")
            return True
        except ConversionCancelled:
            raise
        except Exception as e:
            log(f"ERROR: LLM vision parsing failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
