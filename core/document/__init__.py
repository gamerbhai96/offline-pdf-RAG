"""Document Engine package."""
from core.document.models import (
    BoundingBox,
    BlockType,
    TextBlock,
    ParsedTable,
    TableCell,
    ParsedPage,
    ParsedDocument,
    PageError,
)
from core.document.parser import DocumentParser, ParseOptions
from core.document.ocr import OCRProcessor, OCRResult
from core.document.structure import StructureAnalyzer, StructuredDocument

__all__ = [
    "BoundingBox",
    "BlockType",
    "TextBlock",
    "ParsedTable",
    "TableCell",
    "ParsedPage",
    "ParsedDocument",
    "PageError",
    "DocumentParser",
    "ParseOptions",
    "OCRProcessor",
    "OCRResult",
    "StructureAnalyzer",
    "StructuredDocument",
]
