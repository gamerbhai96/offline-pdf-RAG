"""
Document Engine — Structure Analyzer

Analyzes ParsedPages to identify document structure:
headings, sections, paragraphs, lists, headers/footers, reading order.

Interface contract: /docs/interfaces/StructureAnalyzer.md

Design rules:
- Heading detection uses font size + boldness heuristics (no ML).
- Header/footer detection: text blocks at fixed y positions across pages.
- Column detection: x-center clustering (2-column heuristic).
- This module does NOT perform chunking — that is Chunker's job.
"""
from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from uuid import uuid4

from core.document.models import BlockType, BoundingBox, ParsedDocument, ParsedPage, TextBlock

log = logging.getLogger(__name__)

# ── Heading detection configuration ───────────────────────────────────────────
HEADING_FONT_SIZE_MULTIPLIER = 1.2   # block font size must be > median * this to be heading
MIN_HEADING_FONT_SIZE = 11.0          # absolute minimum font size for heading
MAX_HEADING_WORDS = 20                # headings are usually short
HEADER_FOOTER_Y_MARGIN_FRACTION = 0.08  # top/bottom 8% of page height = header/footer zone


# ── Output data structures ─────────────────────────────────────────────────────

@dataclass
class DetectedHeading:
    section_id: str
    page_number: int
    text: str
    level: int                     # 1 = largest/primary, 2 = sub, 3 = sub-sub
    bbox: BoundingBox
    font_size: float | None = None
    is_bold: bool = False


@dataclass
class DetectedSection:
    section_id: str
    page_number: int
    heading: str | None
    heading_level: int | None
    text_blocks: list[TextBlock] = field(default_factory=list)
    start_offset: int = 0
    end_offset: int = 0
    parent_section_id: str | None = None
    child_section_ids: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(b.text for b in self.text_blocks if b.text.strip())


@dataclass
class DetectedListBlock:
    list_id: str
    page_number: int
    list_type: str            # "ORDERED" | "UNORDERED"
    items: list[str] = field(default_factory=list)
    section_id: str | None = None


@dataclass
class StructuredDocument:
    """Output of StructureAnalyzer. Input to Chunker."""
    document_id: str
    file_path: str
    pages: list[ParsedPage]           # original pages (with OCR applied if needed)
    sections: list[DetectedSection]
    headings: list[DetectedHeading]
    lists: list[DetectedListBlock]
    header_footer_texts: set[str]     # texts to strip when chunking


# ── Main analyzer ──────────────────────────────────────────────────────────────

class StructureAnalyzer:
    """
    Analyzes parsed PDF pages to detect document structure.

    Processing order:
    1. Compute font size statistics across all pages.
    2. Detect and strip recurring headers/footers.
    3. Classify each text block (heading, list item, body).
    4. Group blocks into sections based on headings.
    5. Detect list blocks (bulleted / numbered).
    6. Assign heading levels based on relative font sizes.
    """

    def __init__(
        self,
        detect_headings: bool = True,
        detect_lists: bool = True,
        detect_headers_footers: bool = True,
    ):
        self.detect_headings = detect_headings
        self.detect_lists = detect_lists
        self.detect_headers_footers = detect_headers_footers

    def analyze(self, document: ParsedDocument, document_id: str | None = None) -> StructuredDocument:
        """
        Analyze a ParsedDocument and return a StructuredDocument.

        Args:
            document: Output from DocumentParser.
            document_id: Optional UUID; generated if not provided.

        Returns:
            StructuredDocument with sections, headings, and lists.
        """
        doc_id = document_id or str(uuid4())

        # 1. Compute font statistics
        font_stats = self._compute_font_stats(document.pages)

        # 2. Detect recurring header/footer texts
        header_footer_texts: set[str] = set()
        if self.detect_headers_footers:
            header_footer_texts = self._detect_header_footer_texts(document.pages)

        # 3. Classify all text blocks
        for page in document.pages:
            self._classify_blocks(page, font_stats, header_footer_texts)

        # 4. Build sections
        sections, headings = self._build_sections(document.pages, doc_id)

        # 5. Detect lists
        lists: list[DetectedListBlock] = []
        if self.detect_lists:
            lists = self._detect_lists(document.pages, sections)

        return StructuredDocument(
            document_id=doc_id,
            file_path=document.file_path,
            pages=document.pages,
            sections=sections,
            headings=headings,
            lists=lists,
            header_footer_texts=header_footer_texts,
        )

    # ── Font statistics ────────────────────────────────────────────────────────

    def _compute_font_stats(self, pages: list[ParsedPage]) -> dict:
        sizes = []
        for page in pages:
            for block in page.text_blocks:
                if block.font_size and block.font_size > 0:
                    sizes.append(block.font_size)
        if not sizes:
            return {"median": 12.0, "max": 12.0, "threshold": 14.4}
        median = statistics.median(sizes)
        max_size = max(sizes)
        threshold = max(median * HEADING_FONT_SIZE_MULTIPLIER, MIN_HEADING_FONT_SIZE)
        return {"median": median, "max": max_size, "threshold": threshold}

    # ── Header/footer detection ────────────────────────────────────────────────

    def _detect_header_footer_texts(self, pages: list[ParsedPage]) -> set[str]:
        """
        Detect texts that appear in the header or footer zone across multiple pages.
        A text appearing in the same y-zone on 3+ pages is likely a header/footer.
        """
        if len(pages) < 3:
            return set()

        # Collect candidate texts from top/bottom zones
        zone_texts: dict[str, int] = {}

        for page in pages:
            margin_y = page.height_pts * HEADER_FOOTER_Y_MARGIN_FRACTION
            for block in page.text_blocks:
                text = block.text.strip()
                if not text:
                    continue
                in_header = block.bbox.y0 < margin_y
                in_footer = block.bbox.y1 > (page.height_pts - margin_y)
                if in_header or in_footer:
                    # Normalize: remove page numbers before comparing
                    normalized = _normalize_for_header_detection(text)
                    if normalized:
                        zone_texts[normalized] = zone_texts.get(normalized, 0) + 1

        # Text appearing on 3+ pages (or > 30% of pages) = header/footer
        min_occurrences = max(3, len(pages) // 4)
        return {text for text, count in zone_texts.items() if count >= min_occurrences}

    # ── Block classification ───────────────────────────────────────────────────

    def _classify_blocks(
        self,
        page: ParsedPage,
        font_stats: dict,
        header_footer_texts: set[str],
    ) -> None:
        """Classify each text block in place (mutates block.block_type)."""
        margin_y = page.height_pts * HEADER_FOOTER_Y_MARGIN_FRACTION

        for block in page.text_blocks:
            text = block.text.strip()
            if not text:
                continue

            # Header/footer zone
            in_zone = (
                block.bbox.y0 < margin_y or
                block.bbox.y1 > (page.height_pts - margin_y)
            )
            normalized = _normalize_for_header_detection(text)
            if in_zone and normalized in header_footer_texts:
                block.block_type = BlockType.HEADER_FOOTER
                continue

            # Heading detection
            if self.detect_headings and self._is_heading(block, font_stats):
                block.block_type = BlockType.HEADING
                continue

            # List item detection
            if _is_list_item(text):
                block.block_type = BlockType.LIST_ITEM
                continue

            block.block_type = BlockType.TEXT

    def _is_heading(self, block: TextBlock, font_stats: dict) -> bool:
        """
        Determine if a text block is a heading.
        Criteria (any match qualifies):
        1. Font size >= threshold AND text is short (≤ MAX_HEADING_WORDS words).
        2. Text is bold AND short (when no font size info available).
        3. Text matches common heading patterns (ALL CAPS, numbered sections).
        """
        text = block.text.strip()
        word_count = len(text.split())

        if word_count > MAX_HEADING_WORDS:
            return False
        if not text:
            return False

        # Font size criterion
        if block.font_size and block.font_size >= font_stats["threshold"]:
            return True

        # Bold + short
        if block.is_bold and word_count <= 10:
            return True

        # ALL CAPS short line (common in reports)
        if text.isupper() and 2 <= word_count <= 8:
            return True

        return False

    # ── Section building ───────────────────────────────────────────────────────

    def _build_sections(
        self, pages: list[ParsedPage], document_id: str
    ) -> tuple[list[DetectedSection], list[DetectedHeading]]:
        """
        Walk all blocks in reading order, group into sections based on headings.
        Returns (sections, headings).
        """
        sections: list[DetectedSection] = []
        headings: list[DetectedHeading] = []
        heading_levels: list[float] = []  # font sizes for level assignment

        # Collect all heading font sizes first (for level normalization)
        for page in pages:
            for block in page.text_blocks:
                if block.block_type == BlockType.HEADING and block.font_size:
                    heading_levels.append(block.font_size)
        heading_levels = sorted(set(heading_levels), reverse=True)

        # Walk blocks in reading order, group into sections
        current_section: DetectedSection | None = None
        char_offset = 0

        for page in pages:
            sorted_blocks = sorted(page.text_blocks, key=lambda b: b.reading_order)
            for block in sorted_blocks:
                text = block.text.strip()
                if not text:
                    continue
                if block.block_type == BlockType.HEADER_FOOTER:
                    continue  # skip headers/footers

                if block.block_type == BlockType.HEADING:
                    # Close current section
                    if current_section:
                        current_section.end_offset = char_offset
                        sections.append(current_section)

                    # Open new section
                    level = _assign_heading_level(block.font_size, heading_levels)
                    section_id = str(uuid4())
                    current_section = DetectedSection(
                        section_id=section_id,
                        page_number=page.page_number,
                        heading=text,
                        heading_level=level,
                        start_offset=char_offset,
                    )

                    headings.append(DetectedHeading(
                        section_id=section_id,
                        page_number=page.page_number,
                        text=text,
                        level=level,
                        bbox=block.bbox,
                        font_size=block.font_size,
                        is_bold=block.is_bold,
                    ))
                else:
                    # Body block — add to current section (or implicit first section)
                    if current_section is None:
                        current_section = DetectedSection(
                            section_id=str(uuid4()),
                            page_number=page.page_number,
                            heading=None,
                            heading_level=None,
                            start_offset=0,
                        )
                    current_section.text_blocks.append(block)

                char_offset += len(text) + 1  # +1 for newline separator

        # Close last section
        if current_section:
            current_section.end_offset = char_offset
            sections.append(current_section)

        # Assign parent-child relationships based on heading levels
        _assign_parent_children(sections)

        return sections, headings

    # ── List detection ─────────────────────────────────────────────────────────

    def _detect_lists(
        self, pages: list[ParsedPage], sections: list[DetectedSection]
    ) -> list[DetectedListBlock]:
        """
        Detect sequences of list-item blocks within sections.
        """
        lists: list[DetectedListBlock] = []

        for page in pages:
            sorted_blocks = sorted(
                [b for b in page.text_blocks if b.block_type == BlockType.LIST_ITEM],
                key=lambda b: b.reading_order,
            )
            if not sorted_blocks:
                continue

            # Group consecutive list items
            current_items: list[str] = []
            for block in sorted_blocks:
                current_items.append(_strip_list_prefix(block.text.strip()))

            if current_items:
                # Determine list type from first item
                first_raw = sorted_blocks[0].text.strip()
                list_type = "ORDERED" if _is_ordered_list_item(first_raw) else "UNORDERED"
                lists.append(DetectedListBlock(
                    list_id=str(uuid4()),
                    page_number=page.page_number,
                    list_type=list_type,
                    items=current_items,
                ))

        return lists


# ── Helper functions ───────────────────────────────────────────────────────────

# Common list prefixes
_UNORDERED_PATTERN = re.compile(r"^[\u2022\u2023\u25E6\u2043\u2219•◦▪▸\-\*]\s+")
_ORDERED_PATTERN   = re.compile(r"^\d+[\.\)]\s+|^[a-zA-Z][\.\)]\s+|^\([a-zA-Z\d]+\)\s+")

def _is_list_item(text: str) -> bool:
    return bool(_UNORDERED_PATTERN.match(text) or _ORDERED_PATTERN.match(text))

def _is_ordered_list_item(text: str) -> bool:
    return bool(_ORDERED_PATTERN.match(text))

def _strip_list_prefix(text: str) -> str:
    """Remove bullet/number prefix from list item."""
    text = _UNORDERED_PATTERN.sub("", text)
    text = _ORDERED_PATTERN.sub("", text)
    return text.strip()

def _normalize_for_header_detection(text: str) -> str:
    """Remove page numbers and normalize for header/footer deduplication."""
    # Remove standalone numbers (page numbers)
    normalized = re.sub(r"\b\d+\b", "", text).strip()
    return normalized[:80]  # cap length

def _assign_heading_level(font_size: float | None, heading_sizes: list[float]) -> int:
    """
    Assign heading level 1–3 based on font size rank.
    Largest font → level 1, smaller → level 2, smallest → level 3.
    """
    if not heading_sizes or font_size is None:
        return 2  # default mid-level
    if len(heading_sizes) == 1:
        return 1
    if font_size >= heading_sizes[0]:
        return 1
    if len(heading_sizes) >= 2 and font_size >= heading_sizes[1]:
        return 2
    return 3

def _assign_parent_children(sections: list[DetectedSection]) -> None:
    """
    Set parent_section_id and child_section_ids based on heading level hierarchy.
    Level 1 sections contain level 2 sections, etc.
    """
    stack: list[DetectedSection] = []
    for sec in sections:
        if sec.heading_level is None:
            continue
        # Pop stack until we find a parent at lower level number
        while stack and (stack[-1].heading_level is None or
                         stack[-1].heading_level >= sec.heading_level):
            stack.pop()
        if stack:
            parent = stack[-1]
            sec.parent_section_id = parent.section_id
            parent.child_section_ids.append(sec.section_id)
        stack.append(sec)
