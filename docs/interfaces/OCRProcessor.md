# Interface: OCRProcessor

**Version**: 1.0.0
**Determinism**: NON_DETERMINISTIC (OCR results may vary slightly across engine versions)
**Implemented in Phase**: 1

---

## Purpose

Convert a raster page image (from a scanned PDF or image-only page) into text with bounding boxes.
Abstracts Tesseract (Python/CLI) and ML Kit (Android).

---

## Input

```
page_image: bytes | ImagePath
  Raw image bytes (PNG/JPEG) or path to image file.

page_number: int
  Page number for metadata tracking.

language_hints: string[]
  BCP 47 language codes to hint the OCR engine. e.g. ["en", "fr"]
  Empty list = auto-detect.

dpi: int
  Resolution of the input image. Default: 300.
  Images below 150 DPI produce significantly degraded results.
```

---

## Output

```
result: OCRResult

  page_number: int
  text: string                    -- full page text, reading order best-effort
  confidence: float               -- 0.0–1.0 aggregate confidence
  text_blocks: OCRTextBlock[]

OCRTextBlock:
  text: string
  confidence: float               -- per-block confidence
  bounding_box: BoundingBox       -- x0, y0, x1, y1 in page coordinate space
  block_type: WORD | LINE | PARAGRAPH | BLOCK
```

---

## Required Metadata

```
engine: string                    -- "tesseract-5.x" | "mlkit-v2" | etc.
engine_version: string
language_detected: string | null  -- BCP 47
processing_time_ms: int
```

---

## Error States

```
IMAGE_TOO_SMALL       -- image dimensions below minimum for reliable OCR
LOW_RESOLUTION        -- DPI below 150 (warn, do not fail)
ENGINE_UNAVAILABLE    -- OCR engine not installed or model missing
LANGUAGE_UNSUPPORTED  -- requested language not available
PROCESSING_FAILED     -- OCR engine returned error
EMPTY_RESULT          -- OCR produced no text (not an error — page may be blank)
```

---

## Performance Expectations

| Device Class | Target (single A4 page at 300 DPI) |
|---|---|
| Desktop (Tesseract) | < 3 seconds |
| High-end Android (ML Kit) | < 1 second |
| Mid-range Android (ML Kit) | < 2 seconds |
| Low-end Android (ML Kit) | < 4 seconds |

---

## Notes

- OCR is triggered ONLY when native text extraction yields no text or confidence is below threshold.
- Do not OCR every page of a text PDF — this wastes CPU and degrades accuracy.
- Confidence threshold for OCR trigger: configurable, default 0.5.
- ML Kit on Android is strongly preferred over Tesseract due to speed and model quality.
- OCR results are cached (keyed by page_id + document_hash) to avoid reprocessing.
