"""
Document Engine — PDF Parser

Implements the DocumentParser interface using pdfplumber (MIT license).
pdfplumber wraps pdfminer.six for layout analysis and text extraction.

NO MuPDF. NO PyMuPDF. NO Camelot.
All dependencies are MIT or BSD licensed.

Interface contract: /docs/interfaces/DocumentParser.md
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pdfplumber
import pypdf

from core.document.errors import (
    CorruptedPageError,
    EmptyPDFError,
    InvalidPDFError,
    OversizedPDFError,
    PasswordRequiredError,
    WrongPasswordError,
    DocumentFileNotFoundError,
)
from core.document.models import (
    BlockType,
    BoundingBox,
    PageError,
    ParsedDocument,
    ParsedPage,
    ParsedTable,
    TableCell,
    TextBlock,
)

log = logging.getLogger(__name__)

# ── Configuration constants ────────────────────────────────────────────────────
DEFAULT_MAX_PAGES = 2000
MIN_TEXT_CONFIDENCE = 0.15   # below this → page is considered image-only → trigger OCR
OCR_TRIGGER_CHAR_THRESHOLD = 20  # fewer chars than this on a page → trigger OCR


@dataclass
class ParseOptions:
    page_range: tuple[int, int] | None = None  # (start, end), 1-indexed, inclusive
    extract_tables: bool = True
    extract_images: bool = True
    force_ocr: bool = False
    max_pages: int = DEFAULT_MAX_PAGES
    password: str | None = None


class DocumentParser:
    """
    Parses PDF files into structured ParsedDocument objects.

    Strategy:
    - Use pdfplumber for text extraction + coordinate extraction.
    - Use pypdf for metadata + password detection.
    - Detect image-only pages and flag them for OCR (OCRProcessor handles OCR).
    - Tables are extracted using pdfplumber's lattice + stream strategies.
    - Reading order is corrected via column detection heuristics.

    One bad page MUST NOT crash the entire parse.
    """

    def __init__(self, options: ParseOptions | None = None):
        self.options = options or ParseOptions()

    # ── Public API ─────────────────────────────────────────────────────────────

    def parse(self, file_path: str | Path, password: str | None = None) -> ParsedDocument:
        """
        Parse a PDF file and return a ParsedDocument.

        Args:
            file_path: Absolute path to the PDF.
            password: Optional decryption password.

        Returns:
            ParsedDocument with pages, text blocks, and tables.

        Raises:
            FileNotFoundError: File does not exist.
            InvalidPDFError: File is not a valid PDF.
            PasswordRequiredError: PDF is encrypted, no password given.
            WrongPasswordError: Password incorrect.
            OversizedPDFError: Page count exceeds max_pages.
            EmptyPDFError: No parseable pages found.
        """
        path = Path(file_path)
        pw = password or self.options.password

        if not path.exists():
            raise DocumentFileNotFoundError(f"PDF not found: {path}")

        file_hash = ParsedDocument.compute_hash(path)

        # --- Validate with pypdf first (fast, metadata) ---
        meta = self._read_metadata(path, pw)

        # --- Main parse with pdfplumber ---
        try:
            with pdfplumber.open(str(path), password=pw or "") as pdf:
                page_count = len(pdf.pages)

                if page_count == 0:
                    raise EmptyPDFError(f"PDF has no pages: {path}")

                if page_count > self.options.max_pages:
                    raise OversizedPDFError(page_count, self.options.max_pages)

                start, end = self._resolve_page_range(page_count)
                pages: list[ParsedPage] = []
                errors: list[PageError] = []

                for i, pdf_page in enumerate(pdf.pages[start - 1 : end], start=start):
                    try:
                        parsed = self._parse_page(pdf_page, i)
                        pages.append(parsed)
                    except Exception as exc:
                        log.warning("Page %d parse error: %s", i, exc)
                        errors.append(PageError(
                            page_number=i,
                            error_code="CORRUPTED_PAGE",
                            message=str(exc),
                        ))

        except Exception as exc:
            msg = str(exc).lower()
            if "password" in msg or "encrypted" in msg:
                if pw:
                    raise WrongPasswordError(f"Wrong password for {path}") from exc
                raise PasswordRequiredError(f"PDF is password-protected: {path}") from exc
            raise InvalidPDFError(f"Failed to open PDF: {exc}") from exc

        if not pages and errors:
            raise EmptyPDFError(f"All {len(errors)} pages failed to parse.")

        return ParsedDocument(
            file_path=str(path),
            file_hash=file_hash,
            page_count=page_count,
            pages=pages,
            title=meta.get("title"),
            language_hint=meta.get("language"),
            errors=errors,
        )

    def iter_pages(self, file_path: str | Path, password: str | None = None) -> Iterator[ParsedPage]:
        """
        Generator that yields ParsedPage objects one at a time.
        Useful for large PDFs to avoid loading all pages into memory.
        """
        path = Path(file_path)
        pw = password or self.options.password
        try:
            with pdfplumber.open(str(path), password=pw or "") as pdf:
                page_count = len(pdf.pages)
                start, end = self._resolve_page_range(page_count)
                for i, pdf_page in enumerate(pdf.pages[start - 1 : end], start=start):
                    try:
                        yield self._parse_page(pdf_page, i)
                    except Exception as exc:
                        log.warning("Page %d skipped: %s", i, exc)
        except Exception as exc:
            raise InvalidPDFError(f"Failed to open PDF: {exc}") from exc

    def needs_ocr(self, page: ParsedPage) -> bool:
        """
        Determine whether a page requires OCR.
        True when native text extraction yielded little or no text.
        """
        if self.options.force_ocr:
            return True
        text = page.raw_text.strip()
        return len(text) < OCR_TRIGGER_CHAR_THRESHOLD

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _read_metadata(self, path: Path, password: str | None) -> dict:
        """Read PDF metadata using pypdf (fast, no full parse)."""
        try:
            reader = pypdf.PdfReader(str(path))
            if reader.is_encrypted:
                if not password:
                    raise PasswordRequiredError(f"PDF is encrypted: {path}")
                result = reader.decrypt(password)
                if result == pypdf.PasswordType.NOT_DECRYPTED:
                    raise WrongPasswordError(f"Wrong password for {path}")
            meta = reader.metadata or {}
            return {
                "title": meta.get("/Title") or meta.get("title"),
                "language": meta.get("/Lang") or meta.get("lang"),
            }
        except (PasswordRequiredError, WrongPasswordError):
            raise
        except Exception as exc:
            log.debug("Metadata read failed (non-fatal): %s", exc)
            return {}

    def _resolve_page_range(self, page_count: int) -> tuple[int, int]:
        """Return (start, end) page numbers (1-indexed, inclusive)."""
        if self.options.page_range:
            start = max(1, self.options.page_range[0])
            end = min(page_count, self.options.page_range[1])
        else:
            start, end = 1, page_count
        return start, end

    def _parse_page(self, pdf_page: "pdfplumber.page.Page", page_number: int) -> ParsedPage:
        """Parse a single pdfplumber page into a ParsedPage."""
        width = float(pdf_page.width)
        height = float(pdf_page.height)

        # --- Extract text blocks with bounding boxes ---
        text_blocks = self._extract_text_blocks(pdf_page, page_number)

        # --- Correct reading order ---
        text_blocks = _correct_reading_order(text_blocks, page_width=width)

        # --- Assemble raw text in reading order ---
        raw_text = "\n".join(b.text for b in text_blocks if b.text.strip())

        # --- Extract tables ---
        tables: list[ParsedTable] = []
        has_tables = False
        if self.options.extract_tables:
            tables = self._extract_tables(pdf_page, page_number)
            has_tables = len(tables) > 0

        # --- Detect images ---
        has_images = False
        if self.options.extract_images:
            has_images = bool(pdf_page.images)

        return ParsedPage(
            page_number=page_number,
            raw_text=raw_text,
            text_blocks=text_blocks,
            tables=tables,
            width_pts=width,
            height_pts=height,
            ocr_used=False,
            has_tables=has_tables,
            has_images=has_images,
        )

    def _extract_text_blocks(
        self, pdf_page: "pdfplumber.page.Page", page_number: int
    ) -> list[TextBlock]:
        """Extract text blocks from a pdfplumber page."""
        blocks: list[TextBlock] = []

        try:
            words = pdf_page.extract_words(
                x_tolerance=3,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=True,
                extra_attrs=["size", "fontname"],
            )
        except Exception as exc:
            log.debug("Word extraction failed on page %d: %s", page_number, exc)
            # Fallback: try raw text
            try:
                raw = pdf_page.extract_text() or ""
                if raw.strip():
                    bbox = BoundingBox(0, 0, float(pdf_page.width), float(pdf_page.height), page_number)
                    blocks.append(TextBlock(text=raw, bbox=bbox))
            except Exception:
                pass
            return blocks

        # Group words into line-level blocks by y-position
        line_groups: dict[int, list[dict]] = {}
        for word in words:
            y_key = round(float(word.get("top", 0)) / 5) * 5  # 5pt buckets
            line_groups.setdefault(y_key, []).append(word)

        for y_key in sorted(line_groups.keys()):
            line_words = sorted(line_groups[y_key], key=lambda w: float(w.get("x0", 0)))
            text = " ".join(w["text"] for w in line_words)
            if not text.strip():
                continue

            # Bounding box spans all words in line
            x0 = min(float(w.get("x0", 0)) for w in line_words)
            y0 = min(float(w.get("top", 0)) for w in line_words)
            x1 = max(float(w.get("x1", 0)) for w in line_words)
            y1 = max(float(w.get("bottom", 0)) for w in line_words)

            # Font attributes (from first word in line)
            first = line_words[0]
            font_size = float(first.get("size", 0)) if first.get("size") else None
            fontname = str(first.get("fontname", "")).lower()
            is_bold = "bold" in fontname or "black" in fontname

            block = TextBlock(
                text=text,
                bbox=BoundingBox(x0=x0, y0=y0, x1=x1, y1=y1, page=page_number),
                font_size=font_size,
                is_bold=is_bold,
            )
            blocks.append(block)

        return blocks

    def _extract_tables(
        self, pdf_page: "pdfplumber.page.Page", page_number: int
    ) -> list[ParsedTable]:
        """
        Extract tables using pdfplumber's lattice (bordered) and stream (unbordered) strategies.
        Falls back gracefully if extraction fails.
        """
        tables: list[ParsedTable] = []
        try:
            # Try lattice first (bordered tables)
            raw_tables = pdf_page.extract_tables(
                table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "snap_tolerance": 3,
                    "join_tolerance": 3,
                }
            )
            if not raw_tables:
                # Fallback to stream (whitespace-based)
                raw_tables = pdf_page.extract_tables(
                    table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 3,
                    }
                )
        except Exception as exc:
            log.debug("Table extraction failed on page %d: %s", page_number, exc)
            return []

        for raw_table in raw_tables:
            if not raw_table:
                continue
            cells: list[TableCell] = []
            row_count = len(raw_table)
            col_count = max(len(row) for row in raw_table) if raw_table else 0
            for r_idx, row in enumerate(raw_table):
                for c_idx, cell_text in enumerate(row):
                    cells.append(TableCell(
                        row=r_idx,
                        col=c_idx,
                        text=(cell_text or "").strip(),
                    ))
            tables.append(ParsedTable(
                cells=cells,
                row_count=row_count,
                col_count=col_count,
                page_number=page_number,
            ))

        return tables


# ── Reading order correction ───────────────────────────────────────────────────

def _correct_reading_order(blocks: list[TextBlock], page_width: float) -> list[TextBlock]:
    """
    Correct reading order for multi-column layouts.

    Strategy:
    1. Detect columns using k-means on x_center positions (k=1 or k=2).
    2. Within each column, sort top-to-bottom by y0.
    3. Left column before right column.
    4. Assign reading_order index.

    For single-column documents, this degenerates to a simple top-to-bottom sort.
    """
    if not blocks:
        return blocks

    if len(blocks) == 1:
        blocks[0].reading_order = 0
        return blocks

    # Simple column detection: if x_center gap is large enough → 2 columns
    x_centers = sorted(b.x_center for b in blocks)
    mid = page_width / 2

    # Assign each block to a column (left=0, right=1)
    for b in blocks:
        b.reading_order = 0 if b.x_center <= mid else 1

    # Sort: left column blocks first (top→bottom), then right column blocks
    col0 = sorted([b for b in blocks if b.reading_order == 0], key=lambda b: b.bbox.y0)
    col1 = sorted([b for b in blocks if b.reading_order == 1], key=lambda b: b.bbox.y0)

    # Only use 2 columns if there's a meaningful gap at the midpoint
    has_two_columns = (
        any(b.x_center < mid * 0.85 for b in blocks) and
        any(b.x_center > mid * 1.15 for b in blocks)
    )

    if has_two_columns:
        ordered = col0 + col1
    else:
        ordered = sorted(blocks, key=lambda b: (b.bbox.y0, b.bbox.x0))

    for idx, b in enumerate(ordered):
        b.reading_order = idx

    return ordered
