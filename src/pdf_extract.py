"""PDF-to-text extraction for downloaded CCI order documents.

Uses PyMuPDF only - no OCR in V1. If a PDF has no meaningful extractable
text layer (i.e. it looks like a scanned image), that is flagged rather
than silently returning empty text, so a human knows the order needs OCR
or manual entry.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

MIN_CHARS_FOR_TEXT_LAYER = 200  # below this, treat the PDF as effectively textless/scanned


def extract_text(pdf_path: str) -> dict:
    """Extract text from a PDF, page by page.

    Returns:
        {"text": str, "pages": int, "page_texts": list[str], "chars_per_page": list[int],
         "likely_scanned": bool, "error": str | None}

    `page_texts` (raw text per page) lets a caller locate which page a
    snippet of extracted text came from - used for the penalty_source_page
    provenance field. Never raises - a missing or corrupt PDF comes back
    with an "error" message and empty text instead of crashing the pipeline.
    """
    path = Path(pdf_path)
    if not path.exists():
        return {
            "text": "", "pages": 0, "page_texts": [], "chars_per_page": [],
            "likely_scanned": False, "error": f"file not found: {pdf_path}",
        }

    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF can raise several exception types on a corrupt/unreadable file
        return {
            "text": "", "pages": 0, "page_texts": [], "chars_per_page": [],
            "likely_scanned": False, "error": str(exc),
        }

    page_texts = [page.get_text() for page in doc]
    doc.close()

    full_text = "\n".join(page_texts)
    chars_per_page = [len(t.strip()) for t in page_texts]
    likely_scanned = len(full_text.strip()) < MIN_CHARS_FOR_TEXT_LAYER

    return {
        "text": full_text,
        "pages": len(page_texts),
        "page_texts": page_texts,
        "chars_per_page": chars_per_page,
        "likely_scanned": likely_scanned,
        "error": None,
    }


def save_text(text: str, order_id: str, out_dir: str = "data/text") -> str:
    """Save extracted text to '<out_dir>/<order_id>.txt'. Returns the path written."""
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    dest = out_path / f"{order_id}.txt"
    dest.write_text(text, encoding="utf-8")
    return str(dest)
