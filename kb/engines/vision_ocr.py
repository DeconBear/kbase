import os
import json
import base64
import urllib.request
import urllib.error
import fitz  # PyMuPDF
from pathlib import Path

def image_to_base64(pix):
    png_data = pix.tobytes("png")
    return base64.b64encode(png_data).decode("utf-8")

class VisionOcrEngine:
    def __init__(self):
        pass

    def run(self, pdf_path: str, article_id: str, log_callback=None):
        def log(msg):
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        log("🚀 Starting Vision OCR Engine")
        
        # Load settings
        settings_path = Path("low_memory_config.json")
        if settings_path.exists():
            with open(settings_path, "r", encoding="utf-8") as f:
                settings = json.load(f)
        else:
            settings = {}

        active_id = settings.get("active_vision_provider", "default")
        providers = settings.get("vision_providers", [])
        active_provider = next((p for p in providers if p.get("id") == active_id), {})

        if not active_provider and providers:
            active_provider = providers[0]

        api_type = active_provider.get("type", "openai")
        api_url = active_provider.get("url", "").strip()
        api_key = active_provider.get("key", "").strip()
        model_name = active_provider.get("model", "").strip()

        if not api_url or not api_key or not model_name:
            log("❌ Error: Vision OCR Settings (API URL, Key, Model Name) are incomplete. Please configure them in Settings.")
            return False

        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            log(f"❌ Error opening PDF with PyMuPDF: {e}")
            return False

        total_pages = len(doc)
        log(f"📄 Total pages to process: {total_pages}")

        prompt = "Please extract all text, formulas, code, and tables from this image and format it precisely as Markdown. Return ONLY the Markdown content without any conversational filler."

        markdown_pages = []

        for page_num in range(total_pages):
            log(f"🔄 Processing page {page_num + 1}/{total_pages}...")
            try:
                page = doc.load_page(page_num)
                # Render to image at 200 DPI
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                base64_img = image_to_base64(pix)

                headers = {
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}"
                }
                
                payload = {}
                
                if api_type == "anthropic":
                    headers["x-api-key"] = api_key
                    headers["anthropic-version"] = "2023-06-01"
                    if "Authorization" in headers:
                        del headers["Authorization"]
                    payload = {
                        "model": model_name,
                        "max_tokens": 4096,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image",
                                        "source": {
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": base64_img
                                        }
                                    },
                                    {
                                        "type": "text",
                                        "text": prompt
                                    }
                                ]
                            }
                        ]
                    }
                elif api_type == "minimax":
                    # Minimax generally supports OpenAI format for v1/chat/completions now,
                    # but we use their specific structure if using custom endpoints.
                    # We will treat minimax as openai format since they fully support it for vision as well.
                    payload = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{base64_img}"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                else: # openai compatible
                    payload = {
                        "model": model_name,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": prompt
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": f"data:image/png;base64,{base64_img}"
                                        }
                                    }
                                ]
                            }
                        ]
                    }

                req = urllib.request.Request(api_url, data=json.dumps(payload).encode('utf-8'), headers=headers, method='POST')
                with urllib.request.urlopen(req) as response:
                    res_body = response.read().decode('utf-8')
                    res_json = json.loads(res_body)
                    
                    if api_type == "anthropic":
                        page_text = res_json.get("content", [{}])[0].get("text", "")
                    else:
                        page_text = res_json.get("choices", [{}])[0].get("message", {}).get("content", "")
                        
                    markdown_pages.append(page_text.strip())
                    log(f"✅ Page {page_num + 1} processed successfully.")
            except Exception as e:
                log(f"❌ Error processing page {page_num + 1}: {e}")
                if hasattr(e, 'read'):
                    log(f"Response: {e.read().decode('utf-8')}")
                return False

        doc.close()

        # Combine markdown pages
        final_markdown = "\\n\\n---\\n\\n".join(markdown_pages)

        # Save to output directory
        engine_dir = Path(f"kb/.kbase/articles/{article_id}/engines/vision")
        engine_dir.mkdir(parents=True, exist_ok=True)
        
        output_path = engine_dir / "output.md"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(final_markdown)
            
        log("🎉 Vision OCR processing complete!")
        return True
