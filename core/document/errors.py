"""Document Engine — error hierarchy."""
from __future__ import annotations


class DocumentEngineError(Exception):
    """Base class for all Document Engine errors."""


class DocumentFileNotFoundError(DocumentEngineError):
    """PDF file does not exist at the given path."""


class InvalidPDFError(DocumentEngineError):
    """File exists but is not a valid PDF."""


class CorruptedPageError(DocumentEngineError):
    """A single page could not be parsed. Non-fatal — processing continues."""
    def __init__(self, page_number: int, reason: str):
        self.page_number = page_number
        self.reason = reason
        super().__init__(f"Page {page_number} corrupted: {reason}")


class PasswordRequiredError(DocumentEngineError):
    """PDF is encrypted and no password was provided."""


class WrongPasswordError(DocumentEngineError):
    """Password was provided but is incorrect."""


class UnsupportedEncryptionError(DocumentEngineError):
    """PDF uses an encryption algorithm that cannot be handled."""


class EmptyPDFError(DocumentEngineError):
    """PDF has zero parseable pages."""


class OversizedPDFError(DocumentEngineError):
    """PDF exceeds the configured page limit."""
    def __init__(self, page_count: int, limit: int):
        self.page_count = page_count
        self.limit = limit
        super().__init__(f"PDF has {page_count} pages, exceeds limit of {limit}")


class OCREngineError(DocumentEngineError):
    """OCR engine is unavailable or returned an error."""


class OCRImageTooSmallError(DocumentEngineError):
    """Image is too small for reliable OCR."""


class StructureAnalysisError(DocumentEngineError):
    """Structure analysis failed on the document."""
