# Interface: CitationEngine

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 13

---

## Purpose

Generate citations for every factual point in a validated answer.
Map each AnswerPoint back through the evidence chain to its source page and bounding box.
Enable PDF navigation and source text highlighting.

---

## Input

```
generate(
  answer: AnswerDraft,
  evidence: ValidatedEvidence[],
  chunks: Chunk[]
) → Citation[]
```

---

## Output — Citation

```
Citation:
  citation_id: UUID
  answer_point_text: string        -- the answer point this citation supports
  evidence_id: UUID
  chunk_id: UUID
  document_id: UUID
  document_title: string | null
  page_number: int
  section_heading: string | null
  highlighted_text: string         -- exact source text to highlight
  bounding_boxes: BoundingBox[]    -- coordinates for highlight overlay
  navigation_action: NavigationAction

NavigationAction:
  document_id: UUID
  page_number: int
  scroll_target: BoundingBox | null  -- primary box to scroll to
  highlight_boxes: BoundingBox[]     -- all boxes to highlight
```

---

## Bounding Box Chain

The chain must be maintained throughout processing:

```
PDF text extraction
    ↓ text_block.bounding_box
StructureAnalyzer
    ↓ section/paragraph bbox
Chunker
    ↓ chunk.bounding_boxes
EvidenceValidator
    ↓ evidence.bounding_boxes
ExtractiveQA
    ↓ span.bounding_boxes (derived from char offsets within chunk bbox)
CitationEngine
    ↓ citation.bounding_boxes
UI / PDF Viewer
    ↓ highlight overlay on PDF page
```

If bounding boxes are missing at any stage (e.g., OCR-only page with no coordinates):
- Citation is still generated without highlight_boxes
- Navigation still opens the correct page
- UI shows page-level citation only, no text highlight

---

## Error States

```
MISSING_BOUNDING_BOXES    -- chunk has no bounding boxes; citation degrades gracefully
EVIDENCE_NOT_FOUND        -- evidence_id in answer does not match evidence list
CHUNK_NOT_FOUND           -- chunk_id not in chunks list
INVALID_PAGE_NUMBER       -- page_number out of document range
```

---

## Performance Expectations

| Points | Target |
|---|---|
| 10 citations | < 5 ms |
| 50 citations | < 20 ms |

---

## Notes

- Every visible factual statement in the answer must have at least one Citation.
- Citations must be deduplicated: if two answer points reference the same chunk, generate one citation.
- The highlighted_text must be an exact substring of the source chunk text.
- Clicking a citation must open the PDF at the correct page and scroll to the highlighted region.
