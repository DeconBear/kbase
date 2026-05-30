"""DocMind engine — Alibaba Cloud document parsing via official SDK."""
import json
import os
import time
from pathlib import Path

from alibabacloud_docmind_api20220711.client import Client
from alibabacloud_docmind_api20220711 import models
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util import models as util_models

ARTICLES_DIR = Path(__file__).parent.parent / "articles"


class DocMindEngine:
    name = "docmind"

    def run(self, pdf_path: str, article_id: str, log_callback=None) -> bool:
        """Submit PDF, poll status, fetch result, save markdown."""
        def log(msg):
            if log_callback:
                log_callback(msg)

        ak_id = os.environ.get("DOCMIND_ACCESS_KEY_ID", "")
        ak_secret = os.environ.get("DOCMIND_ACCESS_KEY_SECRET", "")
        region = os.environ.get("DOCMIND_REGION", "cn-hangzhou")

        if not ak_id or not ak_secret:
            log("ERROR: DOCMIND_ACCESS_KEY_ID or DOCMIND_ACCESS_KEY_SECRET not configured")
            return False

        config = open_api_models.Config(
            access_key_id=ak_id,
            access_key_secret=ak_secret,
            region_id=region,
            endpoint=f"docmind-api.{region}.aliyuncs.com",
        )
        client = Client(config)

        file_name = Path(pdf_path).name

        # Step 1: Submit
        log("Step 1/3: Submitting to DocMind...")
        try:
            with open(pdf_path, "rb") as f:
                advance_req = models.SubmitDocParserJobAdvanceRequest(
                    file_url_object=f,
                    file_name=file_name,
                )
                runtime = util_models.RuntimeOptions(
                    read_timeout=120000,
                    connect_timeout=120000
                )
                resp = client.submit_doc_parser_job_advance(advance_req, runtime)
        except Exception as e:
            log(f"Submit failed: {e}")
            return False

        body = resp.body.to_map() if hasattr(resp.body, 'to_map') else {}
        task_id = body.get("Data", {}).get("Id", "")
        rid = body.get("RequestId", "")
        log(f"  RequestId: {rid}")
        if not task_id:
            log(f"No task ID in response: {json.dumps(body, ensure_ascii=False)[:500]}")
            return False
        log(f"Task ID: {task_id}")

        # Step 2: Poll
        log("Step 2/3: Waiting for completion...")
        for i in range(100):
            time.sleep(3)
            try:
                q_req = models.QueryDocParserStatusRequest(id=task_id)
                q_resp = client.query_doc_parser_status(q_req)
                q_body = q_resp.body.to_map() if hasattr(q_resp.body, 'to_map') else {}
            except Exception as e:
                log(f"Poll {i + 1}/100 error: {e}")
                continue

            status = q_body.get("Data", {}).get("Status", "")
            progress = q_body.get("Data", {}).get("Processing", 0)
            log(f"Poll {i + 1}/100: status={status}, progress={progress}%")

            if status in ("success", "Success"):
                break
            if status in ("Fail", "fail"):
                log(f"DocMind task failed")
                return False
        else:
            log("DocMind poll timeout")
            return False

        # Step 3: Fetch result (paginated)
        log("Step 3/3: Fetching result...")
        md_parts = []
        layout_num = 0
        step_size = 500
        while True:
            try:
                r_req = models.GetDocParserResultRequest(
                    id=task_id,
                    layout_step_size=step_size,
                    layout_num=layout_num,
                )
                r_resp = client.get_doc_parser_result(r_req)
                r_body = r_resp.body.to_map() if hasattr(r_resp.body, 'to_map') else {}
            except Exception as e:
                log(f"Fetch result error: {e}")
                return False

            layouts = r_body.get("Data", {}).get("layouts", [])
            if not layouts and layout_num == 0:
                log(f"No layouts: {json.dumps(r_body, ensure_ascii=False)[:500]}")
                return False
            if not layouts:
                break

            for lo in layouts:
                md = lo.get("markdownContent", "")
                if md:
                    md_parts.append(md)

            log(f"  Fetched layouts {layout_num}-{layout_num + len(layouts) - 1}")
            if len(layouts) < step_size:
                break
            layout_num += step_size

        if not md_parts:
            log("No markdown content in result")
            return False

        full_md = "\n\n".join(md_parts)
        
        # Download images and replace URLs
        import re
        import urllib.request
        import urllib.error
        
        pattern_md = r'!\[([^\]]*)\]\((http[s]?://[^\)]+)\)'
        matches = re.findall(pattern_md, full_md)
        article_dir = ARTICLES_DIR / article_id
        
        for alt_text, url in matches:
            filename = alt_text if alt_text and not "/" in alt_text and not "\\" in alt_text else url.split("?")[0].split("/")[-1]
            if not filename.endswith((".png", ".jpeg", ".jpg", ".gif", ".svg", ".webp")):
                filename += ".png"
            local_path = article_dir / filename
            
            try:
                # Download with a timeout
                with urllib.request.urlopen(url, timeout=30) as response:
                    local_path.write_bytes(response.read())
                log(f"Downloaded image: {filename}")
                # Replace the URL with the local filename
                full_md = full_md.replace(url, filename)
            except urllib.error.URLError as e:
                log(f"Failed to download image {url}: {e}")
            except Exception as e:
                log(f"Unexpected error downloading {url}: {e}")

        md_path = article_dir / f"{article_id}.md"
        md_path.write_text(full_md, encoding="utf-8")
        log(f"Done: {len(md_parts)} layout blocks saved to {md_path}")
        return True
