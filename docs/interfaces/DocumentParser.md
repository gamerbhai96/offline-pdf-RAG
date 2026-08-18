# Interface: DocumentParser

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 1

---

## Purpose

Parse a PDF file into structured pages containing text, coordinates, tables, and images.
This interface abstracts the underlying PDF library (pdfplumber on Python, PDFium on Android).

---

## Input

```
file_path: string
  Absolute path to the PDF file on local storage.

password: string | null
  Optional decryption password for password-protected PDFs.

options: ParseOptions
  page_range: [start: int, end: int] | null   -- null = all pages
  extract_tables: boolean                      -- default true
  extract_images: boolean                      -- default true
  force_ocr: boolean                           -- default false (auto-detect)
```

---

## Output

```
result: ParseResult

  document_id: string (UUID, assigned by caller or generated)
  file_path: string
  file_hash: string (SHA-256 of file bytes)
  page_count: int
  title: string | null
  language_hint: string | null    -- BCP 47, detected from metadata
  pages: Page[]                   -- see /docs/schemas/Page.json
  errors: PageError[]             -- per-page errors (non-fatal)
```

---

## Required Metadata (on each Page)

```
page_id: UUID
document_id: UUID
page_number: int (1-indexed)
raw_text: string
ocr_used: boolean
ocr_confidence: float | null      -- 0.0–1.0, null if ocr_used=false
has_tables: boolean
has_images: boolean
width_pts: float
height_pts: float
tables: Table[]                   -- may be empty
text_blocks: TextBlock[]          -- bounding box + text units
reading_order: int[]              -- indices into text_blocks, corrected order
```

---

## Error States

```
FILE_NOT_FOUND         -- file_path does not exist
INVALID_PDF            -- file is not a valid PDF
CORRUPTED_PAGE         -- individual page cannot be parsed (non-fatal, continue)
PASSWORD_REQUIRED      -- PDF is encrypted and no password provided
WRONG_PASSWORD         -- Password provided but incorrect
UNSUPPORTED_ENCRYPTION -- Encryption algorithm not supported
EMPTY_PDF              -- PDF has zero parseable pages
OVERSIZED_PDF          -- PDF exceeds configured page limit
IO_ERROR               -- Disk read error
```

---

## Performance Expectations

| Device Class | Target (200-page PDF) |
|---|---|
| Desktop (Python) | < 5 seconds |
| High-end Android | < 15 seconds |
| Mid-range Android | < 30 seconds |
| Low-end Android | < 60 seconds |

---

## Notes

- One bad page MUST NOT crash the entire parse. Errors are collected in `errors[]`.
- Text extraction is preferred over OCR. OCR is triggered only when native text is absent or confidence is below threshold.
- Reading order correction must handle multi-column layouts via x-coordinate clustering.
- Tables are represented as a grid of cells with row/column indices and bounding boxes.
