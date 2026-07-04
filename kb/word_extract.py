"""Extract plain text from Word .docx files (stdlib only)."""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml_bytes = archive.read("word/document.xml")
    root = ET.fromstring(xml_bytes)
    paragraphs: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        parts = [node.text for node in para.iter(f"{_W_NS}t") if node.text]
        if parts:
            paragraphs.append("".join(parts))
    return "\n\n".join(paragraphs).strip()


def docx_to_markdown(path: Path, *, title: str | None = None) -> str:
    text = extract_docx_text(path)
    heading = title or path.stem.replace("_", " ")
    if not text:
        return f"# {heading}\n\n> Word 文档暂无可提取正文。\n"
    blocks = [f"# {heading}", ""]
    for para in text.split("\n\n"):
        para = para.strip()
        if para:
            blocks.append(para)
            blocks.append("")
    return "\n".join(blocks).strip() + "\n"
