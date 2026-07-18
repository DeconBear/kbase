"""PyMuPDF text-layer parse — free, fast full-document Markdown for digital PDFs."""
from __future__ import annotations

from pathlib import Path


class PyMuPDFEngine:
    """Re-run / promote the text-layer preparse as a first-class engine."""

    name = "pymupdf"

    def run(self, pdf_path: str, article_id: str, log_callback=None, **_kwargs) -> bool:
        def log(msg: str) -> None:
            if log_callback:
                log_callback(msg)
            else:
                print(msg)

        try:
            from document_info import quick_parse_pdf

            log("PyMuPDF: extracting text layer (full document)…")
            result = quick_parse_pdf(article_id, Path(pdf_path), source_filename="original.pdf")
            pages = int(result.get("pages") or 0)
            log(f"PyMuPDF done → {pages} pages (text layer)")
            return True
        except Exception as exc:  # noqa: BLE001
            log(f"ERROR: PyMuPDF failed: {exc}")
            return False
