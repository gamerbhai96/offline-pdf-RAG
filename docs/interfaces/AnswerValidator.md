# Interface: AnswerValidator

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 11

---

## Purpose

GATE 2 — Validate the COMPLETE assembled answer against the source evidence.
Checks that EVERY sentence, bullet point, table cell, and extracted span
in the final answer is traceable to and supported by at least one validated evidence unit.

This is intentionally different from EvidenceValidator (Gate 1):
- Gate 1 validates individual evidence chunks
- Gate 2 validates the complete assembled answer

If validation fails, the ENTIRE formatted answer is DISCARDED.
The system returns a safe fallback response.

---

## Input

```
validate(
  answer_draft: AnswerDraft,
  evidence: ValidatedEvidence[]
) → AnswerValidationResult
```

---

## Output

```
AnswerValidationResult:
  passed: boolean
  validated_answer: AnswerDraft | null   -- non-null only if passed = true
  failure_reason: string | null
  unsupported_points: UnsupportedPoint[] -- points that failed validation
  coverage_score: float                  -- fraction of answer points with evidence support

UnsupportedPoint:
  point_text: string
  point_evidence_ids: UUID[]
  reason: string                         -- "no_evidence_link" | "low_similarity" | "missing"
```

---

## Validation Checks

```
For each AnswerPoint in AnswerDraft:

  CHECK 1 — Evidence Link
    AnswerPoint.evidence_ids must be non-empty
    Each evidence_id must exist in the provided ValidatedEvidence[]
    → Fail: "no_evidence_link"

  CHECK 2 — Text Traceability
    If is_exact_span = true:
      AnswerPoint.text must be an exact substring of at least one linked evidence chunk
      → Fail: "text_not_in_evidence"

    If is_exact_span = false (formatted/selected sentences):
      Token overlap between AnswerPoint.text and linked evidence must be ≥ 0.7
      → Fail: "low_similarity"

  CHECK 3 — Coverage
    coverage_score = (passing_points / total_points)
    If coverage_score < COVERAGE_THRESHOLD (default 0.85):
      passed = false
      → Fail: "insufficient_coverage"
```

---

## On Failure

```
If passed = false:
  - validated_answer = null
  - The caller MUST NOT display the failed answer
  - Return safe fallback:
    "I couldn't find enough evidence in the document to provide a reliable answer."
  - Optionally return closest relevant passages as citations
```

---

## Error States

```
EMPTY_DRAFT            -- answer_draft has no sections or points
EMPTY_EVIDENCE         -- evidence list is empty (auto-fail)
COVERAGE_BELOW_THRESHOLD -- too many unsupported points
```

---

## Performance Expectations

| Answer Size | Target |
|---|---|
| 5 points | < 5 ms |
| 20 points | < 20 ms |

---

## Notes

- This gate is non-negotiable. No answer may bypass it.
- The difference from Gate 1 is scope: Gate 1 is per-chunk, Gate 2 is per-answer.
- Partial answers that pass Gate 2 (some points validated) should set is_complete = false.
- Full answers where ALL points are validated and coverage ≥ threshold → passed = true.
