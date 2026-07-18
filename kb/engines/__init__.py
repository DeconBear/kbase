"""PDF-to-Markdown conversion engines."""
from importlib import import_module

ENGINES = {
    "pymupdf": "engines.pymupdf_engine.PyMuPDFEngine",
    "marker": "engines.marker.MarkerEngine",
    "docmind": "engines.docmind.DocMindEngine",
    "docparser": "engines.docparser.DocParserEngine",
    "vision": "engines.vision_ocr.VisionOcrEngine",
    "ocr": "engines.ocr.CloudOcrEngine",
    "llm_vision": "engines.llm_vision.LlmVisionEngine",
    "unisound": "engines.unisound_parser.UnisoundParserEngine",
}


def get_engine(name: str):
    target = ENGINES.get(name)
    if not target:
        raise ValueError(f"Unknown engine: {name}. Available: {list(ENGINES.keys())}")
    module_name, class_name = target.rsplit(".", 1)
    module = import_module(module_name)
    cls = getattr(module, class_name)
    return cls()


def check_marker_available():
    try:
        from engines.marker import check_marker_available as _check
        return _check()
    except Exception:
        return False


def install_marker_deps(log_callback=None):
    try:
        from engines.marker import install_marker_deps as _install
        return _install(log_callback=log_callback)
    except Exception as e:
        if log_callback:
            log_callback(f"❌ 无法启动安装: {e}")
        return False
