"""PDF text extraction and chunking utilities.

Uses pdfplumber for reliable text extraction from PDFs.
Produces fixed-size chunks (~500 tokens / ~2000 chars) with a 50-token
(~200 char) overlap so that sentence boundaries are never hard-cut.

Usage::

    from src.ingestion.pdf_extractor import extract_text_from_bytes, chunk_text

    raw_bytes = Path("resume.pdf").read_bytes()
    full_text = extract_text_from_bytes(raw_bytes)
    chunks    = chunk_text(full_text)          # list[str]
"""

from __future__ import annotations

import io
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chunking constants (character-based approximation of token counts)
# nomic-embed-text context window: 8192 tokens; we stay well inside it.
# ~4 chars per token → 500 tok ≈ 2000 chars; 50 tok overlap ≈ 200 chars.
# ---------------------------------------------------------------------------
_CHUNK_SIZE: int = 2_000      # characters per chunk
_OVERLAP: int = 200           # trailing overlap carried into next chunk
_MIN_CHUNK_LEN: int = 50      # discard chunks shorter than this (headers, etc.)


def extract_text_from_bytes(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF given its raw bytes.

    Args:
        pdf_bytes: Raw binary content of the PDF file.

    Returns:
        Concatenated plain-text extracted from every page, or an empty
        string if pdfplumber cannot read the file.

    Raises:
        ImportError: If pdfplumber is not installed.
        ValueError:  If pdf_bytes is empty.
    """
    if not pdf_bytes:
        raise ValueError("pdf_bytes must not be empty")

    try:
        import pdfplumber  # noqa: PLC0415 — lazy import
    except ModuleNotFoundError as exc:
        raise ImportError(
            "pdfplumber is required for PDF extraction. "
            "Install it with: pip install pdfplumber"
        ) from exc

    pages: list[str] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""
                if text.strip():
                    pages.append(text)
                else:
                    logger.debug("Page %d yielded no text (image-only or blank)", page_num)
    except Exception:
        logger.exception("pdfplumber failed to open PDF — returning empty string")
        return ""

    full_text = "\n\n".join(pages)
    logger.info(
        "PDF extraction complete: %d page(s), %d characters",
        len(pages),
        len(full_text),
    )
    return full_text


def extract_text_from_path(pdf_path: str) -> str:
    """Extract text from a PDF file on disk.

    Convenience wrapper around :func:`extract_text_from_bytes`.

    Args:
        pdf_path: Absolute or relative file-system path to a PDF file.

    Returns:
        Extracted plain text.
    """
    from pathlib import Path  # noqa: PLC0415

    data = Path(pdf_path).read_bytes()
    return extract_text_from_bytes(data)


def _clean_text(text: str) -> str:
    """Normalize whitespace and strip control characters from extracted text."""
    # Collapse multiple blank lines to a single blank line
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces / tabs to a single space (preserve newlines)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def chunk_text(
    text: str,
    chunk_size: int = _CHUNK_SIZE,
    overlap: int = _OVERLAP,
    min_chunk_len: int = _MIN_CHUNK_LEN,
) -> list[str]:
    """Split *text* into overlapping fixed-size chunks suitable for embedding.

    The splitter tries to break at paragraph or sentence boundaries first
    (double-newline → single-newline → period+space), falling back to a
    hard character split only when no natural boundary exists.

    Args:
        text:          The full document text to split.
        chunk_size:    Target maximum character count per chunk.
        overlap:       Number of trailing characters carried into the next chunk.
        min_chunk_len: Chunks shorter than this are discarded (page headers, etc.)

    Returns:
        Ordered list of non-empty text chunks.

    Example::

        >>> chunks = chunk_text("Long document text ...", chunk_size=2000, overlap=200)
        >>> len(chunks[0]) <= 2000
        True
    """
    if not text or not text.strip():
        return []

    text = _clean_text(text)
    chunks: list[str] = []
    start: int = 0
    doc_len: int = len(text)

    while start < doc_len:
        end = min(start + chunk_size, doc_len)

        # Try to snap end to a natural boundary if we're not at the document end
        if end < doc_len:
            # Priority 1: paragraph break (\n\n)
            para_break = text.rfind("\n\n", start, end)
            if para_break != -1 and para_break > start + overlap:
                end = para_break + 2  # include the double-newline
            else:
                # Priority 2: sentence end (". ")
                sent_break = text.rfind(". ", start, end)
                if sent_break != -1 and sent_break > start + overlap:
                    end = sent_break + 2  # include ". "

        chunk = text[start:end].strip()
        if len(chunk) >= min_chunk_len:
            chunks.append(chunk)

        # Advance with overlap
        start = end - overlap if end < doc_len else doc_len

    logger.debug(
        "chunk_text: %d chars → %d chunks (size=%d, overlap=%d)",
        doc_len,
        len(chunks),
        chunk_size,
        overlap,
    )
    return chunks
