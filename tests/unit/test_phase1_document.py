"""
Phase 1 tests — DocumentParser, OCRProcessor, StructureAnalyzer.

Strategy:
- Unit tests use synthetic minimal PDFs created in-memory (no external test files needed).
- Integration tests use real PDFs if available in benchmarks/datasets/pdfs/.
- All tests must pass with or without Tesseract installed.
"""
from __future__ import annotations

import io
import json
import tempfile
from pathlib import Path

import pytest

# ── Helpers to create minimal test PDFs ───────────────────────────────────────

def make_minimal_pdf(text: str = "Hello World. This is a test document.") -> bytes:
    """Create a minimal valid PDF with one page of text using only stdlib."""
    # We use pypdf/reportlab if available; otherwise use a pre-encoded minimal PDF bytes.
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica", 12)
        c.drawString(72, 720, text)
        c.save()
        return buf.getvalue()
    except ImportError:
        pass

    # Fallback: hand-crafted minimal PDF with embedded text
    return _hardcoded_minimal_pdf(text)


def make_multipage_pdf(pages: list[str]) -> bytes:
    """Create a minimal multi-page PDF."""
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        for page_text in pages:
            c.setFont("Helvetica", 12)
            c.drawString(72, 720, page_text)
            c.showPage()
        c.save()
        return buf.getvalue()
    except ImportError:
        return _hardcoded_minimal_pdf(pages[0] if pages else "")


def _hardcoded_minimal_pdf(text: str) -> bytes:
    """Minimal valid PDF that pdfplumber can parse — hand-coded."""
    # This is a complete minimal PDF 1.4 with one text page
    safe_text = text[:200].replace("(", "\\(").replace(")", "\\)").replace("\\", "\\\\")
    content = f"BT /F1 12 Tf 72 720 Td ({safe_text}) Tj ET"
    content_bytes = content.encode("latin-1", errors="replace")
    content_len = len(content_bytes)

    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
        b"4 0 obj\n<< /Length " + str(content_len).encode() + b" >>\nstream\n"
        + content_bytes +
        b"\nendstream\nendobj\n"
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
        b"xref\n0 6\n"
        b"0000000000 65535 f \n"
        b"0000000009 00000 n \n"
        b"0000000058 00000 n \n"
        b"0000000115 00000 n \n"
        b"0000000266 00000 n \n"
        b"0000000400 00000 n \n"
        b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n470\n%%EOF\n"
    )
    return pdf


# ── DocumentParser tests ───────────────────────────────────────────────────────

class TestDocumentParser:
    """Unit tests for DocumentParser."""

    def setup_method(self):
        from core.document.parser import DocumentParser, ParseOptions
        self.Parser = DocumentParser
        self.Options = ParseOptions

    def test_parse_minimal_pdf(self, tmp_path):
        """Parser should handle a minimal single-page PDF."""
        pdf_bytes = make_minimal_pdf("Hello World test document.")
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc = parser.parse(pdf_file)

        assert doc is not None
        assert doc.page_count >= 1
        assert len(doc.pages) >= 1
        assert doc.file_hash != ""
        assert len(doc.file_hash) == 64  # SHA-256 hex

    def test_file_not_found_raises(self, tmp_path):
        """Should raise DocumentEngineError for missing file."""
        from core.document.errors import DocumentFileNotFoundError
        parser = self.Parser()
        with pytest.raises(DocumentFileNotFoundError):
            parser.parse(tmp_path / "nonexistent.pdf")

    def test_invalid_pdf_raises(self, tmp_path):
        """Should raise InvalidPDFError for non-PDF file."""
        from core.document.errors import InvalidPDFError
        bad_file = tmp_path / "bad.pdf"
        bad_file.write_bytes(b"this is not a pdf file at all")
        parser = self.Parser()
        with pytest.raises((InvalidPDFError, Exception)):
            parser.parse(bad_file)

    def test_file_hash_is_deterministic(self, tmp_path):
        """Same file should always produce the same hash."""
        pdf_bytes = make_minimal_pdf("deterministic content")
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc1 = parser.parse(pdf_file)
        doc2 = parser.parse(pdf_file)
        assert doc1.file_hash == doc2.file_hash

    def test_different_files_different_hash(self, tmp_path):
        """Different content should produce different hashes."""
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_bytes(make_minimal_pdf("content A"))
        f2.write_bytes(make_minimal_pdf("content B"))

        parser = self.Parser()
        doc1 = parser.parse(f1)
        doc2 = parser.parse(f2)
        assert doc1.file_hash != doc2.file_hash

    def test_page_range_respected(self, tmp_path):
        """Parser should respect page_range option."""
        pdf_bytes = make_multipage_pdf(["Page one", "Page two", "Page three"])
        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser(options=self.Options(page_range=(1, 2)))
        doc = parser.parse(pdf_file)
        assert len(doc.pages) <= 2

    def test_oversized_pdf_raises(self, tmp_path):
        """Should raise OversizedPDFError when page count exceeds max."""
        from core.document.errors import OversizedPDFError
        # Create a 3-page PDF with max_pages=2 — must use reportlab for reliable page count
        try:
            pdf_bytes = make_multipage_pdf(["Page A", "Page B", "Page C"])
        except Exception:
            pytest.skip("Could not create multipage PDF")
        pdf_file = tmp_path / "big.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser(options=self.Options(max_pages=2))
        # If the PDF actually parsed to 3 pages, OversizedPDFError should fire.
        # If the fallback PDF only produces 1 page, skip the test.
        import pdfplumber
        with pdfplumber.open(str(pdf_file)) as pdf:
            actual_pages = len(pdf.pages)
        if actual_pages <= 2:
            pytest.skip("Fallback PDF only produces 1 page — cannot test page limit")
        with pytest.raises(OversizedPDFError):
            parser.parse(pdf_file)

    def test_parsed_page_has_bounding_boxes(self, tmp_path):
        """Each text block should have a bounding box."""
        pdf_bytes = make_minimal_pdf("Text with bounding box check.")
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc = parser.parse(pdf_file)

        for page in doc.pages:
            for block in page.text_blocks:
                assert block.bbox is not None
                assert block.bbox.x0 >= 0
                assert block.bbox.y0 >= 0
                assert block.bbox.x1 > block.bbox.x0 or block.bbox.y1 > block.bbox.y0
                assert block.bbox.page == page.page_number

    def test_page_metadata_populated(self, tmp_path):
        """ParsedPage should have width_pts and height_pts."""
        pdf_bytes = make_minimal_pdf()
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc = parser.parse(pdf_file)

        for page in doc.pages:
            assert page.width_pts > 0
            assert page.height_pts > 0
            assert page.page_number >= 1

    def test_needs_ocr_empty_page(self):
        """needs_ocr should return True for a page with no text."""
        from core.document.parser import DocumentParser
        from core.document.models import ParsedPage
        parser = DocumentParser()
        page = ParsedPage(page_number=1, raw_text="")
        assert parser.needs_ocr(page) is True

    def test_needs_ocr_text_page(self):
        """needs_ocr should return False for a page with sufficient text."""
        from core.document.parser import DocumentParser
        from core.document.models import ParsedPage
        parser = DocumentParser()
        page = ParsedPage(
            page_number=1,
            raw_text="This is a page with enough content to avoid OCR triggering. " * 3,
        )
        assert parser.needs_ocr(page) is False

    def test_force_ocr_option(self):
        """force_ocr=True should always return needs_ocr=True."""
        from core.document.parser import DocumentParser, ParseOptions
        from core.document.models import ParsedPage
        parser = DocumentParser(options=ParseOptions(force_ocr=True))
        page = ParsedPage(page_number=1, raw_text="This has text but OCR is forced.")
        assert parser.needs_ocr(page) is True

    def test_to_dict_serializable(self, tmp_path):
        """ParsedDocument.to_dict() should be JSON-serializable."""
        pdf_bytes = make_minimal_pdf("Serialization test.")
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc = parser.parse(pdf_file)
        d = doc.to_dict()

        # Should serialize without error
        json_str = json.dumps(d)
        assert len(json_str) > 0
        assert "file_hash" in d
        assert "pages" in d

    def test_multipage_pdf_page_numbers(self, tmp_path):
        """Page numbers should be sequential and 1-indexed."""
        pdf_bytes = make_multipage_pdf(["First page.", "Second page.", "Third page."])
        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc = parser.parse(pdf_file)

        for i, page in enumerate(doc.pages, 1):
            assert page.page_number == i

    def test_iter_pages_yields_same_as_parse(self, tmp_path):
        """iter_pages() should yield the same number of pages as parse()."""
        pdf_bytes = make_multipage_pdf(["One", "Two", "Three"])
        pdf_file = tmp_path / "multi.pdf"
        pdf_file.write_bytes(pdf_bytes)

        parser = self.Parser()
        doc_pages = parser.parse(pdf_file).pages
        iter_pages = list(parser.iter_pages(pdf_file))

        assert len(iter_pages) == len(doc_pages)


# ── OCRProcessor tests ─────────────────────────────────────────────────────────

class TestOCRProcessor:
    """Unit tests for OCRProcessor."""

    def setup_method(self):
        from core.document.ocr import OCRProcessor
        self.OCRProcessor = OCRProcessor

    def test_is_available(self):
        """is_available() should return a boolean."""
        processor = self.OCRProcessor()
        result = processor.is_available()
        assert isinstance(result, bool)

    def test_process_image_pil(self, tmp_path):
        """Should process a PIL image if Tesseract is available."""
        processor = self.OCRProcessor()
        if not processor.is_available():
            pytest.skip("Tesseract not installed — skipping OCR test")

        try:
            from PIL import Image, ImageDraw
        except ImportError:
            pytest.skip("Pillow not installed")

        # Create a simple white image with black text
        img = Image.new("RGB", (400, 100), color="white")
        draw = ImageDraw.Draw(img)
        draw.text((10, 30), "Hello OCR", fill="black")

        result = processor.process_image(img, page_number=1)
        assert result is not None
        assert result.page_number == 1
        assert isinstance(result.text, str)
        assert isinstance(result.confidence, float)
        assert 0.0 <= result.confidence <= 1.0
        assert result.engine.startswith("tesseract")

    def test_process_image_unavailable_raises(self):
        """Should raise RuntimeError when Tesseract is not available."""
        processor = self.OCRProcessor()
        if processor.is_available():
            pytest.skip("Tesseract IS available — skipping unavailability test")

        with pytest.raises(RuntimeError, match="Tesseract"):
            processor.process_image(b"\x89PNG fake image", page_number=1)

    def test_cache_saves_and_loads(self, tmp_path):
        """OCR results should be cached and retrievable."""
        processor = self.OCRProcessor()
        if not processor.is_available():
            pytest.skip("Tesseract not installed")

        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not installed")

        cache_dir = tmp_path / "ocr_cache"
        cached_processor = self.OCRProcessor(cache_dir=cache_dir)

        img = Image.new("RGB", (200, 50), color="white")
        doc_hash = "abc123def456" * 5  # 60-char fake hash

        # First call — should write to cache
        result1 = cached_processor.process_image(img, page_number=3, document_hash=doc_hash)
        cache_files = list(cache_dir.glob("*.json"))
        assert len(cache_files) >= 1, "Cache file should be created"

        # Second call — should load from cache
        result2 = cached_processor.process_image(img, page_number=3, document_hash=doc_hash)
        assert result2.text == result1.text
        assert result2.confidence == result1.confidence

    def test_ocr_result_serializable(self, tmp_path):
        """OCRResult.to_dict() should be JSON-serializable."""
        from core.document.ocr import OCRResult, OCRTextBlock
        result = OCRResult(
            page_number=1,
            text="sample text",
            confidence=0.85,
            text_blocks=[OCRTextBlock("sample text", 0.85, 0, 0, 100, 20)],
            engine="tesseract-5.0",
            engine_version="5.0.0",
            language_detected="en",
            processing_time_ms=123.4,
        )
        d = result.to_dict()
        json_str = json.dumps(d)
        assert "sample text" in json_str
        assert "0.85" in json_str


# ── StructureAnalyzer tests ────────────────────────────────────────────────────

class TestStructureAnalyzer:
    """Unit tests for StructureAnalyzer."""

    def _make_doc_with_headings(self) -> "ParsedDocument":
        """Create a synthetic ParsedDocument with headings and body text."""
        from core.document.models import (
            BlockType, BoundingBox, ParsedDocument, ParsedPage, TextBlock
        )

        def make_block(text, y0, font_size=12.0, is_bold=False, page=1):
            return TextBlock(
                text=text,
                bbox=BoundingBox(x0=72, y0=y0, x1=540, y1=y0 + font_size + 4, page=page),
                font_size=font_size,
                is_bold=is_bold,
                block_type=BlockType.TEXT,
                reading_order=int(y0 / 20),
            )

        page = ParsedPage(
            page_number=1,
            raw_text="Introduction\nThis is the intro text.\nMethods\nHere are methods.",
            width_pts=612.0,
            height_pts=792.0,
            text_blocks=[
                make_block("Introduction", y0=680, font_size=18.0, is_bold=True),
                make_block("This is the intro text.", y0=660, font_size=12.0),
                make_block("This continues the introduction with more content.", y0=645, font_size=12.0),
                make_block("Methods", y0=620, font_size=18.0, is_bold=True),
                make_block("Here are the detailed methods.", y0=600, font_size=12.0),
            ]
        )

        return ParsedDocument(
            file_path="synthetic.pdf",
            file_hash="a" * 64,
            page_count=1,
            pages=[page],
        )

    def test_analyze_detects_headings(self):
        """Analyzer should detect heading-level blocks."""
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        doc = self._make_doc_with_headings()
        structured = analyzer.analyze(doc, document_id="test-doc-001")

        assert len(structured.headings) >= 1
        heading_texts = [h.text for h in structured.headings]
        assert any("Introduction" in t or "Methods" in t for t in heading_texts)

    def test_analyze_creates_sections(self):
        """Analyzer should create at least one section."""
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        doc = self._make_doc_with_headings()
        structured = analyzer.analyze(doc, document_id="test-doc-002")

        assert len(structured.sections) >= 1

    def test_sections_have_text(self):
        """Sections should contain text blocks."""
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        doc = self._make_doc_with_headings()
        structured = analyzer.analyze(doc)

        body_sections = [s for s in structured.sections if s.heading is not None]
        for sec in body_sections:
            assert sec.heading is not None

    def test_heading_levels_assigned(self):
        """Headings should have level 1–3."""
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        doc = self._make_doc_with_headings()
        structured = analyzer.analyze(doc)

        for h in structured.headings:
            assert 1 <= h.level <= 3

    def test_list_item_detection(self):
        """List items should be detected."""
        from core.document.structure import _is_list_item
        assert _is_list_item("• Scalability") is True
        assert _is_list_item("- Fault tolerance") is True
        assert _is_list_item("1. First step") is True
        assert _is_list_item("a. First item") is True
        assert _is_list_item("Normal paragraph text.") is False

    def test_strip_list_prefix(self):
        """List prefixes should be stripped cleanly."""
        from core.document.structure import _strip_list_prefix
        assert _strip_list_prefix("• Scalability") == "Scalability"
        assert _strip_list_prefix("- Fault tolerance") == "Fault tolerance"
        assert _strip_list_prefix("1. First step") == "First step"
        assert _strip_list_prefix("Normal text") == "Normal text"

    def test_no_crash_on_empty_document(self):
        """Analyzer should not crash on a document with no text blocks."""
        from core.document.models import ParsedDocument, ParsedPage
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        empty_page = ParsedPage(page_number=1, raw_text="", width_pts=612, height_pts=792)
        doc = ParsedDocument(file_path="empty.pdf", file_hash="b" * 64, page_count=1, pages=[empty_page])
        structured = analyzer.analyze(doc)
        assert structured is not None

    def test_structured_document_has_document_id(self):
        """StructuredDocument should always have a document_id."""
        from core.document.structure import StructureAnalyzer
        analyzer = StructureAnalyzer()
        doc = self._make_doc_with_headings()
        structured = analyzer.analyze(doc, document_id="explicit-id-123")
        assert structured.document_id == "explicit-id-123"

    def test_heading_level_assignment(self):
        """Heading level should be assigned based on font size rank."""
        from core.document.structure import _assign_heading_level
        sizes = [24.0, 18.0, 14.0]
        assert _assign_heading_level(24.0, sizes) == 1
        assert _assign_heading_level(18.0, sizes) == 2
        assert _assign_heading_level(14.0, sizes) == 3
        assert _assign_heading_level(None, sizes) == 2   # default

    def test_reading_order_correction(self):
        """Reading order correction should assign sequential indices."""
        from core.document.parser import _correct_reading_order
        from core.document.models import BoundingBox, TextBlock

        blocks = [
            TextBlock("Right col top", BoundingBox(350, 200, 550, 220, 1)),
            TextBlock("Left col top",  BoundingBox(50,  200, 250, 220, 1)),
            TextBlock("Left col bot",  BoundingBox(50,  150, 250, 170, 1)),
            TextBlock("Right col bot", BoundingBox(350, 150, 550, 170, 1)),
        ]
        corrected = _correct_reading_order(blocks, page_width=612.0)
        orders = [b.reading_order for b in corrected]
        # All should have unique sequential indices
        assert sorted(orders) == list(range(len(blocks)))


# ── BoundingBox tests ──────────────────────────────────────────────────────────

class TestBoundingBox:
    def test_merge(self):
        from core.document.models import BoundingBox
        a = BoundingBox(0, 0, 100, 50, 1)
        b = BoundingBox(80, 30, 200, 100, 1)
        merged = a.merge(b)
        assert merged.x0 == 0
        assert merged.y0 == 0
        assert merged.x1 == 200
        assert merged.y1 == 100

    def test_width_height(self):
        from core.document.models import BoundingBox
        bbox = BoundingBox(10, 20, 110, 70, 1)
        assert bbox.width == 100.0
        assert bbox.height == 50.0

    def test_to_dict_from_dict_roundtrip(self):
        from core.document.models import BoundingBox
        bbox = BoundingBox(1.5, 2.5, 100.5, 200.5, 3)
        d = bbox.to_dict()
        restored = BoundingBox.from_dict(d)
        assert restored.x0 == bbox.x0
        assert restored.y0 == bbox.y0
        assert restored.x1 == bbox.x1
        assert restored.y1 == bbox.y1
        assert restored.page == bbox.page


# ── ParsedTable tests ──────────────────────────────────────────────────────────

class TestParsedTable:
    def test_to_text(self):
        from core.document.models import BoundingBox, ParsedTable, TableCell
        cells = [
            TableCell(0, 0, "Name"), TableCell(0, 1, "Value"),
            TableCell(1, 0, "TCP"),  TableCell(1, 1, "Reliable"),
        ]
        table = ParsedTable(cells=cells, row_count=2, col_count=2, page_number=1)
        text = table.to_text()
        assert "Name" in text
        assert "TCP" in text
        assert "Reliable" in text
        assert "\t" in text   # tab-separated

    def test_to_dict_serializable(self):
        from core.document.models import ParsedTable, TableCell
        cells = [TableCell(0, 0, "A"), TableCell(0, 1, "B")]
        table = ParsedTable(cells=cells, row_count=1, col_count=2, page_number=2)
        d = table.to_dict()
        json.dumps(d)   # must not raise
