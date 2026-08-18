"""
Document Engine — Data Models

All models conform to /docs/schemas/ JSON Schema definitions.
Dataclasses are used for in-memory representation; JSON serialization
maps to the canonical schema fields.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Primitive geometry
# ---------------------------------------------------------------------------

@dataclass
class BoundingBox:
    """Bounding box in PDF point coordinates (origin bottom-left for PDF,
    converted to top-left origin for consistency with pdfplumber)."""
    x0: float
    y0: float
    x1: float
    y1: float
    page: int  # 1-indexed page number

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def to_dict(self) -> dict[str, Any]:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1, "page": self.page}

    @classmethod
    def from_dict(cls, d: dict) -> "BoundingBox":
        return cls(x0=d["x0"], y0=d["y0"], x1=d["x1"], y1=d["y1"], page=d["page"])

    def merge(self, other: "BoundingBox") -> "BoundingBox":
        """Return the smallest box that contains both boxes (same page assumed)."""
        return BoundingBox(
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
            page=self.page,
        )


# ---------------------------------------------------------------------------
# Text blocks (from PDF layout engine)
# ---------------------------------------------------------------------------

class BlockType(str, Enum):
    TEXT = "text"
    TABLE = "table"
    IMAGE = "image"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    HEADER_FOOTER = "header_footer"
    OTHER = "other"


@dataclass
class TextBlock:
    """A single layout block from the PDF parser with its bounding box."""
    text: str
    bbox: BoundingBox
    block_type: BlockType = BlockType.TEXT
    font_size: float | None = None
    is_bold: bool = False
    reading_order: int = 0  # position in corrected reading order

    @property
    def x_center(self) -> float:
        return (self.bbox.x0 + self.bbox.x1) / 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "bbox": self.bbox.to_dict(),
            "block_type": self.block_type.value,
            "font_size": self.font_size,
            "is_bold": self.is_bold,
            "reading_order": self.reading_order,
        }


# ---------------------------------------------------------------------------
# Table structures
# ---------------------------------------------------------------------------

@dataclass
class TableCell:
    row: int
    col: int
    text: str
    bbox: BoundingBox | None = None

    def to_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "text": self.text,
            "bbox": self.bbox.to_dict() if self.bbox else None,
        }


@dataclass
class ParsedTable:
    """A table extracted from a PDF page."""
    cells: list[TableCell]
    row_count: int
    col_count: int
    bbox: BoundingBox | None = None
    page_number: int = 0

    def to_text(self) -> str:
        """Convert table to a tab-separated text representation."""
        grid: dict[tuple[int, int], str] = {(c.row, c.col): c.text for c in self.cells}
        rows = []
        for r in range(self.row_count):
            row = "\t".join(grid.get((r, c), "") for c in range(self.col_count))
            rows.append(row)
        return "\n".join(rows)

    def to_dict(self) -> dict:
        return {
            "cells": [c.to_dict() for c in self.cells],
            "row_count": self.row_count,
            "col_count": self.col_count,
            "bbox": self.bbox.to_dict() if self.bbox else None,
            "page_number": self.page_number,
        }


# ---------------------------------------------------------------------------
# Per-page errors (non-fatal)
# ---------------------------------------------------------------------------

@dataclass
class PageError:
    page_number: int
    error_code: str
    message: str


# ---------------------------------------------------------------------------
# Parsed page — output of DocumentParser
# ---------------------------------------------------------------------------

@dataclass
class ParsedPage:
    """
    Output of DocumentParser for a single page.
    Conforms to /docs/schemas/Page.json
    """
    page_number: int          # 1-indexed
    raw_text: str             # full text in reading order
    text_blocks: list[TextBlock] = field(default_factory=list)
    tables: list[ParsedTable] = field(default_factory=list)
    width_pts: float = 0.0
    height_pts: float = 0.0
    ocr_used: bool = False
    ocr_confidence: float | None = None
    has_tables: bool = False
    has_images: bool = False

    @property
    def has_text(self) -> bool:
        return bool(self.raw_text.strip())

    @property
    def text_confidence(self) -> float:
        """Heuristic: fraction of text blocks with non-empty text."""
        if not self.text_blocks:
            return 0.0
        non_empty = sum(1 for b in self.text_blocks if b.text.strip())
        return non_empty / len(self.text_blocks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "page_number": self.page_number,
            "raw_text": self.raw_text,
            "ocr_used": self.ocr_used,
            "ocr_confidence": self.ocr_confidence,
            "has_tables": self.has_tables,
            "has_images": self.has_images,
            "width_pts": self.width_pts,
            "height_pts": self.height_pts,
            "text_blocks": [b.to_dict() for b in self.text_blocks],
            "tables": [t.to_dict() for t in self.tables],
        }


# ---------------------------------------------------------------------------
# Full parsed document — output of DocumentParser
# ---------------------------------------------------------------------------

@dataclass
class ParsedDocument:
    """
    Complete output of DocumentParser.
    Conforms to /docs/schemas/Document.json
    """
    file_path: str
    file_hash: str              # SHA-256 of file bytes
    page_count: int
    pages: list[ParsedPage] = field(default_factory=list)
    title: str | None = None
    language_hint: str | None = None
    errors: list[PageError] = field(default_factory=list)

    @staticmethod
    def compute_hash(path: str | Path) -> str:
        """Compute SHA-256 of the file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    @property
    def successful_pages(self) -> list[ParsedPage]:
        return [p for p in self.pages if p.has_text]

    @property
    def ocr_page_count(self) -> int:
        return sum(1 for p in self.pages if p.ocr_used)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "file_hash": self.file_hash,
            "page_count": self.page_count,
            "title": self.title,
            "language_hint": self.language_hint,
            "pages": [p.to_dict() for p in self.pages],
            "errors": [
                {"page_number": e.page_number, "error_code": e.error_code, "message": e.message}
                for e in self.errors
            ],
        }
