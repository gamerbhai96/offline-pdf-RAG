# Interface: EvidenceValidator

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 9

---

## Purpose

GATE 1 — Validate that each individual evidence chunk actually supports the user's question.
Filter out irrelevant or weakly relevant chunks before they reach the answer assembly stage.
This is independent of and precedes AnswerValidator (Gate 2).

---

## Input

```
validate(
  query: ResolvedQuery,
  chunks: RankedChunk[]
) → EvidenceValidationResult
```

---

## Output

```
EvidenceValidationResult:
  validated: ValidatedEvidence[]    -- chunks that passed validation
  rejected: RejectedEvidence[]      -- chunks that failed, with reason

ValidatedEvidence:
  chunk_id: UUID
  document_id: UUID
  page_number: int
  text: string
  dense_score: float
  bm25_score: float
  fusion_score: float
  validation_score: float           -- aggregate Gate 1 score
  validation_passed: boolean        -- always true here
  bounding_boxes: BoundingBox[]
  signals: ValidationSignals

ValidationSignals:
  dense_similarity: float
  bm25_score: float
  query_term_overlap: float         -- fraction of normalized query terms in chunk
  entity_overlap: float             -- fraction of query entities found in chunk
  section_relevance: float          -- heading match score [0, 1]
  min_threshold_passed: boolean

RejectedEvidence:
  chunk_id: UUID
  rejection_reason: string          -- "low_score" | "no_entity_overlap" | "below_threshold"
  validation_score: float
```

---

## Validation Scoring

```
validation_score =
  w1 * dense_similarity        (default w1 = 0.40)
  + w2 * normalized_bm25       (default w2 = 0.20)
  + w3 * query_term_overlap    (default w3 = 0.20)
  + w4 * entity_overlap        (default w4 = 0.15)
  + w5 * section_relevance     (default w5 = 0.05)

PASS threshold: validation_score ≥ 0.35 (calibrated in Phase 17)
```

---

## Error States

```
NO_CHUNKS             -- chunks list is empty; return empty validated list
THRESHOLD_MISMATCH    -- all chunks rejected; EvidenceValidator should return empty,
                       -- not an error. Caller handles NO_ANSWER path.
```

---

## Performance Expectations

| Chunks | Target |
|---|---|
| 10 chunks | < 5 ms |
| 50 chunks | < 20 ms |

---

## Notes

- This gate exists to prevent low-quality evidence from contaminating the answer assembly.
- If ALL chunks are rejected, the system takes the NO_ANSWER path.
- Thresholds are calibrated against the benchmark dataset in Phase 17.
- The difference from AnswerValidator (Gate 2): this operates on INDIVIDUAL chunks;
  Gate 2 operates on the COMPLETE assembled answer.
