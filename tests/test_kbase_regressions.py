import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KB_DIR = ROOT / "kb"
if str(KB_DIR) not in sys.path:
    sys.path.insert(0, str(KB_DIR))

import db_api
import db_index
import serve


def _patch_db(monkeypatch, tmp_path):
    kb_dir = tmp_path / "kb"
    db_dir = kb_dir / ".kbase"
    db_path = db_dir / "index.db"
    monkeypatch.setattr(db_index, "KB_DIR", kb_dir)
    monkeypatch.setattr(db_index, "DB_DIR", db_dir)
    monkeypatch.setattr(db_index, "DB_PATH", db_path)
    monkeypatch.setattr(db_api, "KB_DIR", kb_dir)
    monkeypatch.setattr(db_api, "DB_DIR", db_dir)
    monkeypatch.setattr(db_api, "DB_PATH", db_path)
    return kb_dir


def test_static_serving_blocks_runtime_files():
    handler = object.__new__(serve.KBHandler)
    handler.directory = str(serve.DIR)

    assert handler._static_target_for_request("/index.html") == serve.DIR / "index.html"
    assert handler._static_target_for_request("/llm_config.json") is None
    assert handler._static_target_for_request("/low_memory_config.json") is None
    assert handler._static_target_for_request("/.kbase/index.db") is None
    assert handler._static_target_for_request("/../local.env") is None


def test_runtime_settings_masks_and_preserves_keys(monkeypatch, tmp_path):
    config_path = tmp_path / "low_memory_config.json"
    monkeypatch.setattr(serve, "RUNTIME_CONFIG_FILE", config_path)

    serve.save_runtime_config_from_public({
        "vision_providers": [{
            "id": "default",
            "name": "Vision",
            "type": "openai",
            "url": "https://example.test/v1",
            "model": "vision-model",
            "key": "secret-key-1234",
        }],
        "active_vision_provider": "default",
    })
    public = serve.public_runtime_config()
    provider = public["vision_providers"][0]
    assert "key" not in provider
    assert provider["key_set"] is True

    serve.save_runtime_config_from_public({
        "vision_providers": [{
            "id": "default",
            "name": "Vision",
            "type": "openai",
            "url": "https://example.test/v1",
            "model": "other-model",
            "keep_key": True,
        }],
        "active_vision_provider": "default",
    })
    private = serve.load_runtime_config()
    assert private["vision_providers"][0]["key"] == "secret-key-1234"
    assert private["vision_providers"][0]["model"] == "other-model"


def test_db_initializes_and_persists_extended_article_fields(monkeypatch, tmp_path):
    kb_dir = _patch_db(monkeypatch, tmp_path)
    article_dir = kb_dir / "articles" / "paper1"
    article_dir.mkdir(parents=True)

    db_api.add_article({
        "id": "paper1",
        "title": "Paper One",
        "pages": 7,
        "md_available": False,
        "pdf_available": True,
        "kind": "paper",
        "source_filename": "paper1.pdf",
        "parser": "pymupdf",
        "metadata_extracted": True,
        "tags": ["rag"],
    })

    assert not (article_dir / "paper1.md").exists()

    db_api.update_article("paper1", {"converting": True, "preparse_error": "failed"})
    article = db_api.get_all_articles()["articles"][0]
    assert article["pages"] == 7
    assert article["converting"] is True
    assert article["metadata_extracted"] is True
    assert article["source_filename"] == "paper1.pdf"
    assert article["parser"] == "pymupdf"
    assert article["preparse_error"] == "failed"
    assert article["tags"] == ["rag"]
