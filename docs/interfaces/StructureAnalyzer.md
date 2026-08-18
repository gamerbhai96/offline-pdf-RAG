# Interface: StructureAnalyzer

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 2

---

## Purpose

Analyze raw parsed pages and identify document structure:
headings, sections, paragraphs, lists, tables, headers/footers, and reading order.
Produces a structured Document object consumed by the Chunker.

---

## Input

```
pages: Page[]
  Output from DocumentParser. Must include text_blocks with bounding boxes.

document_id: string (UUID)

options: StructureOptions
  detect_headings: boolean         -- default true
  detect_tables: boolean           -- default true
  detect_lists: boolean            -- default true
  detect_headers_footers: boolean  -- default true
  reading_order_correction: boolean -- default true
```

---

## Output

```
result: StructuredDocument

  document_id: string
  sections: Section[]              -- see /docs/schemas/Section.json
  paragraphs: Paragraph[]
  headings: Heading[]
  lists: ListBlock[]
  tables: TableBlock[]
  page_metadata: PageMetadata[]    -- headers, footers, page numbers detected
```

### Section
```
  section_id: UUID
  document_id: UUID
  page_id: UUID
  heading: string | null
  heading_level: int | null        -- 1=h1, 2=h2, etc.
  start_offset: int                -- character offset within page text
  end_offset: int
  child_section_ids: UUID[]        -- for nested sections
  parent_section_id: UUID | null
```

### Heading
```
  heading_id: UUID
  page_id: UUID
  section_id: UUID
  text: string
  level: int                       -- 1=largest/most important
  bounding_box: BoundingBox
  font_size: float | null
  is_bold: boolean | null
```

### ListBlock
```
  list_id: UUID
  page_id: UUID
  section_id: UUID | null
  list_type: ORDERED | UNORDERED
  items: ListItem[]

  ListItem:
    index: int
    text: string
    bounding_box: BoundingBox
```

---

## Required Metadata

```
heading_detection_method: string   -- "font-size-heuristic" | "bold-heuristic" | "regex"
table_detection_method: string     -- "bounding-box-grid" | "line-detection"
reading_order_method: string       -- "column-cluster" | "natural"
```

---

## Error States

```
INSUFFICIENT_STRUCTURE   -- document has too little structure to analyze (warn, not fail)
NO_TEXT_BLOCKS           -- pages contain no text blocks (OCR may be needed)
MALFORMED_INPUT          -- pages[] is invalid or missing required fields
```

---

## Performance Expectations

| Device Class | Target (200-page document) |
|---|---|
| Desktop | < 2 seconds |
| High-end Android | < 5 seconds |
| Mid-range Android | < 10 seconds |

---

## Notes

- Heading detection uses font size + boldness heuristics. Threshold is configurable.
- Headers and footers (repeated content at page top/bottom) must be detected and excluded from chunk text.
- Multi-column layout is detected via x-coordinate clustering of text block centers.
- This interface does NOT perform chunking — that is the Chunker's responsibility.
