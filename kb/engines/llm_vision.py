"""LLM-based PDF parser.

Renders each PDF page to PNG via PyMuPDF and asks the user's already-
configured LLM (the one wired into /api/llm-config — DeepSeek, OpenAI,
Moonshot, etc.) to extract the page contents as Markdown.

Unlike the older VisionOcrEngine which reads a separate vision_providers
list out of low_memory_config.json, this engine reuses the SAME
provider / model / api_key the user has already configured for chat.
This means enabling "LLM 视觉解析" requires zero extra config: the
provider that's already in llm_config.json is used.
"""
from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

from engines._paths import ARTICLES_DIR
from llm_config import load_llm_config, public_llm_config, resolve_llm_settings
import storage


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
    # OpenAI-compatible (DeepSeek, Moonshot, OpenAI, SiliconFlow, custom)
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
    """Parse a PDF by sending each page as an image to the configured
    LLM (DeepSeek, OpenAI, etc.) and stitching the per-page Markdown
    responses together.

    Config:
      - Reads the *active* provider from storage.LLM_CONFIG_FILE
        (the same one /api/llm-config and the chat use).
      - Falls back to env-derived LLM settings if the config file
        has no providers yet (so the engine still works on a fresh
        install where the user only set LLM_API_KEY in local.env).
    """
    name = "llm_vision"

    def run(self, pdf_path: str, article_id: str, log_callback=None):
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        # Resolve the active provider.
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

        # If the UI config is empty (no provider, no key) fall back to
        # env-driven settings. This is what makes "just set
        # LLM_API_KEY in local.env" work.
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

        # Normalize the endpoint URL
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

            headers = {
                "Content-Type": "application/json",
            }
            if api_type == "anthropic":
                headers["x-api-key"] = api_key
                headers["anthropic-version"] = "2023-06-01"
            else:
                headers["Authorization"] = f"Bearer {api_key}"

            markdown_pages: list[str] = []
            for page_num in range(total_pages):
                log(f"Page {page_num + 1}/{total_pages} → LLM")
                page = doc.load_page(page_num)
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
                    try: body = e.read().decode("utf-8", errors="replace")
                    except Exception: pass
                    log(f"ERROR: LLM returned HTTP {e.code}: {body[:300]}")
                    return False
                text = _extract_text(api_type, res).strip()
                if not text:
                    log(f"WARN: page {page_num + 1} returned empty content")
                    text = f"<!-- empty page {page_num + 1} -->"
                markdown_pages.append(text)

            from workspace_paths import publish_engine_markdown

            final = "\n\n---\n\n".join(markdown_pages).strip() + "\n"
            article_dir = ARTICLES_DIR / article_id
            article_dir.mkdir(parents=True, exist_ok=True)
            publish_engine_markdown(article_dir, article_id, pdf_path, md_text=final)

            # Update parser metadata.
            meta_path = article_dir / f"{article_id}_meta.json"
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
            })
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
            log(f"LLM vision parsing done → {article_id}.md ({len(final)} chars)")
            return True
        except Exception as e:
            log(f"ERROR: LLM vision parsing failed: {e}")
            return False
        finally:
            if doc is not None:
                doc.close()
