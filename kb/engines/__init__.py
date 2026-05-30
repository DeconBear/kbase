"""PDF-to-Markdown conversion engines."""
from importlib import import_module

ENGINES = {
    "marker": "engines.marker.MarkerEngine",
    "docmind": "engines.docmind.DocMindEngine",
}


def get_engine(name: str):
    target = ENGINES.get(name)
    if not target:
        raise ValueError(f"Unknown engine: {name}. Available: {list(ENGINES.keys())}")
    module_name, class_name = target.rsplit(".", 1)
    module = import_module(module_name)
    cls = getattr(module, class_name)
    return cls()
