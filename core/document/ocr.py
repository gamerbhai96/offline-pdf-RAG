"""
Document Engine — OCR Processor

Implements the OCRProcessor interface using pytesseract + Tesseract (Apache 2.0).
On Android, ML Kit would be used instead — this module is the Python/CLI implementation.

Interface contract: /docs/interfaces/OCRProcessor.md

Key rules:
- OCR is triggered ONLY when native text is absent or below confidence threshold.
- Results are cached by (document_hash + page_number).
- Low DPI images trigger a warning, not a failure.
- Confidence < MIN_OCR_CONFIDENCE means OCR result is flagged as low quality.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import NamedTuple

log = logging.getLogger(__name__)

# Tesseract is an optional dependency — fail gracefully if not installed.
try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    log.warning("pytesseract/Pillow not installed. OCR will not be available.")

# Minimum DPI for reliable OCR (warn below this, don't fail)
MIN_DPI_WARNING = 150
DEFAULT_DPI = 300
MIN_OCR_CONFIDENCE = 0.0   # Tesseract aggregate confidence threshold


@dataclass
class OCRTextBlock:
    """A single text block from OCR output with bounding box."""
    text: str
    confidence: float          # 0.0–1.0 per-block
    x0: float
    y0: float
    x1: float
    y1: float
    block_type: str = "line"   # "word" | "line" | "paragraph" | "block"

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "confidence": self.confidence,
            "bbox": {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1},
            "block_type": self.block_type,
        }


@dataclass
class OCRResult:
    """Result from OCRProcessor for a single page."""
    page_number: int
    text: str                        # full page text
    confidence: float                # 0.0–1.0 aggregate
    text_blocks: list[OCRTextBlock]
    engine: str                      # "tesseract-5.x" | "mlkit"
    engine_version: str
    language_detected: str | None
    processing_time_ms: float

    def to_dict(self) -> dict:
        return {
            "page_number": self.page_number,
            "text": self.text,
            "confidence": self.confidence,
            "text_blocks": [b.to_dict() for b in self.text_blocks],
            "engine": self.engine,
            "engine_version": self.engine_version,
            "language_detected": self.language_detected,
            "processing_time_ms": self.processing_time_ms,
        }


class OCRProcessor:
    """
    Converts page images to text using Tesseract OCR.

    Usage:
        processor = OCRProcessor()
        result = processor.process_image(image_bytes, page_number=5)

    The caller (DocumentParser pipeline) is responsible for:
    1. Rendering the PDF page to an image (using pdfplumber or pypdf rendering).
    2. Passing image bytes here.
    3. Merging OCR result back into the ParsedPage.
    """

    def __init__(
        self,
        language_hints: list[str] | None = None,
        dpi: int = DEFAULT_DPI,
        cache_dir: Path | None = None,
    ):
        self.language_hints = language_hints or ["eng"]
        self.dpi = dpi
        self.cache_dir = cache_dir
        self._engine_version = self._detect_version()

    def is_available(self) -> bool:
        return TESSERACT_AVAILABLE and self._engine_version != "unavailable"

    def process_image(
        self,
        image_input: bytes | Path | "Image.Image",
        page_number: int,
        document_hash: str | None = None,
    ) -> OCRResult:
        """
        Run OCR on a page image.

        Args:
            image_input: Raw image bytes, path to image file, or PIL Image.
            page_number: Page number for metadata.
            document_hash: Used for cache keying.

        Returns:
            OCRResult with text, confidence, and text blocks.

        Raises:
            RuntimeError: If Tesseract is not available.
        """
        if not self.is_available():
            raise RuntimeError(
                "Tesseract OCR is not available. "
                "Install pytesseract and Tesseract 5.x, or use ML Kit on Android."
            )

        # Check cache
        cache_key = self._cache_key(document_hash, page_number) if document_hash else None
        if cache_key:
            cached = self._load_cache(cache_key)
            if cached:
                return cached

        t0 = time.perf_counter()
        image = self._load_image(image_input)

        # Warn on low DPI
        if hasattr(image, "info") and image.info.get("dpi"):
            actual_dpi = image.info["dpi"][0]
            if actual_dpi < MIN_DPI_WARNING:
                log.warning(
                    "Page %d image DPI (%d) is below %d — OCR quality may be degraded.",
                    page_number, actual_dpi, MIN_DPI_WARNING
                )

        # Run Tesseract with per-word bounding boxes + confidence
        lang = "+".join(self.language_hints)
        try:
            data = pytesseract.image_to_data(
                image,
                lang=lang,
                output_type=pytesseract.Output.DICT,
                config="--psm 3",   # automatic page segmentation
            )
        except pytesseract.TesseractError as exc:
            log.error("Tesseract error on page %d: %s", page_number, exc)
            # Return empty result rather than crashing
            elapsed = (time.perf_counter() - t0) * 1000
            return OCRResult(
                page_number=page_number,
                text="",
                confidence=0.0,
                text_blocks=[],
                engine=f"tesseract-{self._engine_version}",
                engine_version=self._engine_version,
                language_detected=None,
                processing_time_ms=elapsed,
            )

        # Parse Tesseract output
        blocks, full_lines, confidences = self._parse_tesseract_data(data, page_number)
        text = "\n".join(full_lines)
        aggregate_conf = sum(confidences) / len(confidences) if confidences else 0.0

        elapsed = (time.perf_counter() - t0) * 1000
        result = OCRResult(
            page_number=page_number,
            text=text,
            confidence=aggregate_conf,
            text_blocks=blocks,
            engine=f"tesseract-{self._engine_version}",
            engine_version=self._engine_version,
            language_detected=None,   # Tesseract doesn't reliably report this
            processing_time_ms=elapsed,
        )

        # Save to cache
        if cache_key:
            self._save_cache(cache_key, result)

        return result

    def process_pdf_page(
        self,
        pdf_path: str | Path,
        page_number: int,
        document_hash: str | None = None,
        dpi: int | None = None,
    ) -> OCRResult:
        """
        Render a PDF page to image and run OCR.
        Uses pdfplumber's page rendering capabilities.
        """
        try:
            import pdfplumber
            with pdfplumber.open(str(pdf_path)) as pdf:
                if page_number < 1 or page_number > len(pdf.pages):
                    raise ValueError(f"Page {page_number} out of range")
                page = pdf.pages[page_number - 1]
                # Render page to image
                resolution = dpi or self.dpi
                img = page.to_image(resolution=resolution).original
                return self.process_image(img, page_number, document_hash)
        except ImportError:
            raise RuntimeError("pdfplumber required for PDF page rendering")

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _load_image(self, image_input) -> "Image.Image":
        """Normalize image input to PIL Image."""
        if isinstance(image_input, bytes):
            return Image.open(BytesIO(image_input)).convert("RGB")
        elif isinstance(image_input, Path) or isinstance(image_input, str):
            return Image.open(str(image_input)).convert("RGB")
        else:
            # Assume PIL Image already
            return image_input.convert("RGB") if hasattr(image_input, "convert") else image_input

    def _parse_tesseract_data(
        self, data: dict, page_number: int
    ) -> tuple[list[OCRTextBlock], list[str], list[float]]:
        """Parse Tesseract image_to_data output into OCRTextBlock objects."""
        blocks: list[OCRTextBlock] = []
        # Group by block_num + par_num + line_num for line-level aggregation
        lines: dict[tuple, list] = {}
        n = len(data["text"])

        for i in range(n):
            text = data["text"][i].strip()
            if not text:
                continue
            conf_raw = int(data["conf"][i])
            if conf_raw < 0:
                continue  # -1 = no confidence (non-text region)
            conf = conf_raw / 100.0

            key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
            lines.setdefault(key, []).append({
                "text": text,
                "conf": conf,
                "left": data["left"][i],
                "top": data["top"][i],
                "width": data["width"][i],
                "height": data["height"][i],
            })

        full_lines: list[str] = []
        confidences: list[float] = []

        for key in sorted(lines.keys()):
            words = lines[key]
            line_text = " ".join(w["text"] for w in words)
            line_conf = sum(w["conf"] for w in words) / len(words)

            x0 = min(w["left"] for w in words)
            y0 = min(w["top"] for w in words)
            x1 = max(w["left"] + w["width"] for w in words)
            y1 = max(w["top"] + w["height"] for w in words)

            blocks.append(OCRTextBlock(
                text=line_text,
                confidence=line_conf,
                x0=float(x0), y0=float(y0),
                x1=float(x1), y1=float(y1),
                block_type="line",
            ))
            full_lines.append(line_text)
            confidences.append(line_conf)

        return blocks, full_lines, confidences

    def _detect_version(self) -> str:
        if not TESSERACT_AVAILABLE:
            return "unavailable"
        try:
            ver = pytesseract.get_tesseract_version()
            return str(ver)
        except Exception:
            return "unavailable"

    def _cache_key(self, document_hash: str, page_number: int) -> str:
        return hashlib.sha256(f"{document_hash}:{page_number}".encode()).hexdigest()[:16]

    def _cache_path(self, key: str) -> Path | None:
        if not self.cache_dir:
            return None
        return self.cache_dir / f"ocr_{key}.json"

    def _load_cache(self, key: str) -> OCRResult | None:
        path = self._cache_path(key)
        if not path or not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            blocks = [
                OCRTextBlock(
                    text=b["text"],
                    confidence=b["confidence"],
                    x0=b["bbox"]["x0"], y0=b["bbox"]["y0"],
                    x1=b["bbox"]["x1"], y1=b["bbox"]["y1"],
                )
                for b in data.get("text_blocks", [])
            ]
            return OCRResult(
                page_number=data["page_number"],
                text=data["text"],
                confidence=data["confidence"],
                text_blocks=blocks,
                engine=data["engine"],
                engine_version=data["engine_version"],
                language_detected=data.get("language_detected"),
                processing_time_ms=data.get("processing_time_ms", 0.0),
            )
        except Exception as exc:
            log.debug("Cache load failed (%s): %s", key, exc)
            return None

    def _save_cache(self, key: str, result: OCRResult) -> None:
        path = self._cache_path(key)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(result.to_dict(), ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            log.debug("Cache save failed (%s): %s", key, exc)
