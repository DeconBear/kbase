"""DeconBear DocParser engine — cloud GPU-accelerated PDF parsing via REST API."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

ARTICLES_DIR = Path(__file__).parent.parent / "articles"

API_BASE = os.environ.get("DOCPARSER_API_URL", "https://docparser.deconbear.cn")


def _api_key():
    return os.environ.get("DOCPARSER_API_KEY", "")


def _headers():
    return {
        "X-API-Key": _api_key(),
    }


def _request(method, path, *, data=None, files=None, timeout=60):
    """Make an HTTP request to the DocParser API."""
    url = f"{API_BASE}{path}"
    headers = _headers()

    if files:
        # Multipart upload
        import io
        boundary = f"----KBase{int(time.time() * 1000)}"
        body = io.BytesIO()
        for key, (filename, file_data, content_type) in files.items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode())
            body.write(f"Content-Type: {content_type}\r\n\r\n".encode())
            body.write(file_data)
            body.write(b"\r\n")
        for key, value in (data or {}).items():
            body.write(f"--{boundary}\r\n".encode())
            body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            body.write(str(value).encode())
            body.write(b"\r\n")
        body.write(f"--{boundary}--\r\n".encode())
        body_bytes = body.getvalue()

        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    elif data:
        body_bytes = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    else:
        req = urllib.request.Request(url, headers=headers, method=method)

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


class DocParserEngine:
    name = "docparser"

    def run(self, pdf_path: str, article_id: str, log_callback=None, engine=None) -> bool:
        """Submit PDF to DeconBear DocParser, poll for result, save markdown.

        Args:
            pdf_path: Path to the PDF file.
            article_id: KBase article identifier.
            log_callback: Optional callable for progress messages.
            engine: Sub-engine to use — "struct" (academic papers) or "polyglot" (Chinese/multilingual).
        """
        def log(msg):
            if log_callback:
                log_callback(msg)

        if engine is None:
            engine = os.environ.get("DOCPARSER_ENGINE", "struct").strip() or "struct"

        api_key = _api_key()
        if not api_key:
            log("ERROR: DOCPARSER_API_KEY not configured. Set it in local.env or environment.")
            return False

        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            log(f"ERROR: PDF not found: {pdf_path}")
            return False

        pdf_size_mb = pdf_path.stat().st_size / (1024 * 1024)
        if pdf_size_mb > 100:
            log(f"ERROR: PDF is {pdf_size_mb:.1f} MB, exceeds 100 MB limit")
            return False

        # Step 1: Submit PDF
        log(f"Step 1/3: Submitting PDF to DocParser ({engine} engine)...")
        log(f"  File: {pdf_path.name} ({pdf_size_mb:.1f} MB)")

        try:
            result = _request(
                "POST",
                "/parse",
                files={"file": (pdf_path.name, pdf_path.read_bytes(), "application/pdf")},
                data={"engine": engine},
                timeout=120,
            )
        except urllib.error.HTTPError as e:
            error_body = ""
            try:
                error_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                pass
            log(f"ERROR: Submit failed (HTTP {e.code}): {error_body}")
            return False
        except Exception as e:
            log(f"ERROR: Submit failed: {e}")
            return False

        task_id = result.get("task_id", "")
        if not task_id:
            log(f"ERROR: No task_id in response: {json.dumps(result, ensure_ascii=False)[:300]}")
            return False
        log(f"  Task ID: {task_id}")

        # Step 2: Poll status
        log("Step 2/3: Waiting for parsing to complete...")
        poll_interval = 5
        max_polls = 600  # 5s * 600 = 50 min max
        for i in range(max_polls):
            time.sleep(poll_interval)
            try:
                status_resp = _request("GET", f"/status/{task_id}", timeout=30)
            except Exception as e:
                log(f"  Poll {i + 1}: error — {e}")
                continue

            status = status_resp.get("status", "")
            if status == "success":
                log(f"  Poll {i + 1}: success!")
                break
            if status == "failure":
                error_msg = status_resp.get("error", "unknown error")
                log(f"  Poll {i + 1}: FAILED — {error_msg}")
                return False
            if status in ("queued", "started", "retrying"):
                if i % 6 == 0:  # Log every ~30s
                    log(f"  Poll {i + 1}: {status}")
            else:
                log(f"  Poll {i + 1}: {status}")
        else:
            log("ERROR: Polling timed out after 50 minutes")
            return False

        # Step 3: Fetch result
        log("Step 3/3: Fetching parsed markdown...")
        try:
            result_resp = _request("GET", f"/result/{task_id}", timeout=120)
        except urllib.error.HTTPError as e:
            if e.code == 409:
                log("ERROR: Result not ready yet (409)")
            else:
                log(f"ERROR: Failed to fetch result (HTTP {e.code})")
            return False
        except Exception as e:
            log(f"ERROR: Failed to fetch result: {e}")
            return False

        markdown = result_resp.get("markdown", "")
        parse_time = result_resp.get("parse_time_s", 0)
        images = result_resp.get("images", {})
        if not markdown:
            log("ERROR: Empty markdown in response")
            return False

        log(f"  Parsed in {parse_time:.1f}s, {len(markdown)} chars")

        # Save result
        article_dir = ARTICLES_DIR / article_id
        article_dir.mkdir(parents=True, exist_ok=True)

        md_path = article_dir / f"{article_id}.md"
        md_path.write_text(markdown, encoding="utf-8")
        log(f"Saved: {md_path.name} ({len(markdown)} chars)")

        if images:
            import base64
            images_dir = article_dir
            for img_name, img_b64 in images.items():
                try:
                    img_data = base64.b64decode(img_b64)
                    img_path = images_dir / img_name
                    img_path.parent.mkdir(parents=True, exist_ok=True)
                    img_path.write_bytes(img_data)
                except Exception as e:
                    log(f"  ERROR saving image {img_name}: {e}")
            log(f"Saved {len(images)} images.")

        # Save meta
        meta_path = article_dir / f"{article_id}_meta.json"
        meta = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        meta.update({
            "source": "docparser",
            "docparser_engine": engine,
            "docparser_task_id": task_id,
            "docparser_parse_time_s": parse_time,
        })
        meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

        return True
