# Interface: ConfidenceEngine

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 11

---

## Purpose

Compute an aggregate confidence score for a validated answer.
Combines signals from retrieval, ranking, QA, validation, and evidence quality.
Produces a discrete confidence level (HIGH/MEDIUM/LOW/NO_ANSWER) and a numeric score.

---

## Input

```
score(
  query: ResolvedQuery,
  evidence: ValidatedEvidence[],
  answer: AnswerDraft,
  validation_result: AnswerValidationResult,
  qa_spans: AnswerSpan[] | null
) → ConfidenceResult
```

---

## Output

```
ConfidenceResult:
  level: ConfidenceLevel
    HIGH       -- ≥ 0.75
    MEDIUM     -- ≥ 0.50
    LOW        -- ≥ 0.30
    NO_ANSWER  -- < 0.30 or validation failed

  score: float             -- 0.0–1.0 aggregate
  signals: ConfidenceSignals
  explanation: string      -- human-readable (for CLI debug and UI tooltip)

ConfidenceSignals:
  top_dense_score: float
  top_bm25_score: float
  retrieval_agreement: float     -- fraction of top-k chunks from both dense and BM25
  evidence_count: int
  evidence_diversity: float      -- fraction from distinct pages/sections
  qa_confidence: float | null    -- QA model confidence if FACT_QA route
  validation_coverage: float     -- from AnswerValidationResult
  answer_completeness: float     -- is_complete flag → 1.0 or 0.5
  citation_coverage: float       -- fraction of answer points with citations
```

---

## Scoring Formula

```
score =
  w1 * top_dense_score           (0.20)
  + w2 * top_bm25_normalized     (0.10)
  + w3 * retrieval_agreement     (0.15)
  + w4 * evidence_diversity      (0.10)
  + w5 * qa_confidence           (0.20, 0.0 if not FACT_QA)
  + w6 * validation_coverage     (0.15)
  + w7 * answer_completeness     (0.10)

Weights sum to 1.0.
Calibrate thresholds and weights against benchmark in Phase 17.
```

---

## Confidence Levels (Initial Thresholds — Calibrate in Phase 17)

| Level | Score Range | Meaning |
|---|---|---|
| HIGH | ≥ 0.75 | Strong evidence, validated, high QA confidence |
| MEDIUM | 0.50–0.74 | Reasonable evidence, some uncertainty |
| LOW | 0.30–0.49 | Weak evidence; answer shown with explicit caveat |
| NO_ANSWER | < 0.30 | Insufficient evidence; safe fallback returned |

---

## Performance Expectations

| Input Size | Target |
|---|---|
| 10 evidence chunks, 5 answer points | < 2 ms |
| 50 evidence chunks, 20 answer points | < 10 ms |

---

## Error States

```
VALIDATION_FAILED       -- AnswerValidationResult.passed = false → NO_ANSWER always
EMPTY_EVIDENCE          -- auto → NO_ANSWER
```

---

## Notes

- If AnswerValidationResult.passed = false, the result is ALWAYS NO_ANSWER regardless of signal scores.
- Thresholds are not arbitrary; they must be calibrated using the benchmark no-answer dataset in Phase 17.
- The explanation field is surfaced in CLI debug mode and optionally as a UI tooltip.
- LOW confidence answers must be shown with a visible disclaimer in the UI.
