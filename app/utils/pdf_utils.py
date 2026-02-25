"""PDF utility functions: title extraction and page rendering."""

import io
from pathlib import Path

import pypdf
import pypdfium2


_MAX_TITLE_LENGTH = 255


def _clean_title(raw: str) -> str:
    """Strip, collapse internal whitespace, cap at 255 chars."""
    return " ".join(raw.split())[:_MAX_TITLE_LENGTH]


def extract_suggested_title(content: bytes, original_filename: str) -> str:
    """
    Derive a title suggestion from the file.

    For PDFs: reads the /Title metadata field first; falls back to filename stem.
    For images: cleaned filename stem.

    PDF metadata is sanitized — many tools embed empty, path-like, or auto-generated
    /Title values that are useless as display names. We skip those.
    """
    ext = Path(original_filename).suffix.lower()
    if ext == ".pdf":
        try:
            reader = pypdf.PdfReader(io.BytesIO(content))
            meta = reader.metadata
            if meta:
                raw = str(meta.get("/Title") or "").strip()
                # Reject: empty, too short, looks like a file path, or contains nulls
                if raw and len(raw) >= 3 and "/" not in raw and "\x00" not in raw:
                    return _clean_title(raw)
        except Exception:
            pass

    stem = Path(original_filename).stem
    fallback = stem.replace("_", " ").replace("-", " ").strip()
    return _clean_title(fallback) or "Untitled"


def render_pdf_to_png(content: bytes, scale: float = 2.0) -> bytes:
    """
    Render the first page of a PDF to PNG bytes.

    scale=1.5  → preview thumbnail
    scale=3.0  → high-quality image for popups / standalone display
    """
    doc = pypdfium2.PdfDocument(content)
    page = doc[0]
    bitmap = page.render(scale=scale)
    pil_image = bitmap.to_pil()
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    return buf.getvalue()
