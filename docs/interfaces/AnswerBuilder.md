# Interface: AnswerBuilder

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 10 / 12

---

## Purpose

Assemble a structured draft answer from validated evidence, using the
route-appropriate extraction strategy. The answer must be entirely grounded
in the provided evidence. No content may be invented.

Four sub-strategies, selected by QuestionRouter:

1. **FACT_QA** — wrap an ExtractiveQA span
2. **LIST** — rank evidence sentences into a bullet/numbered list
3. **SUMMARY** — hierarchical sentence selection across sections
4. **TABLE** — extract or reconstruct a table from evidence

---

## Input

```
build(
  query: ResolvedQuery,
  evidence: ValidatedEvidence[],
  route: RouteDecision,
  qa_spans: AnswerSpan[] | null    -- required for FACT_QA route
) → AnswerDraft
```

---

## Output — AnswerDraft

```
AnswerDraft:
  route: Route
  answer_format: AnswerFormat
  sections: AnswerSection[]
  evidence_ids: UUID[]            -- all evidence chunks used
  is_complete: boolean            -- false if evidence was insufficient for full answer
  incompleteness_note: string | null

AnswerSection:
  heading: string | null
  points: AnswerPoint[]
  evidence_ids: UUID[]

AnswerPoint:
  text: string                    -- exact or lightly formatted source text
  evidence_ids: UUID[]            -- which evidence unit(s) support this point
  confidence: float
  is_exact_span: boolean          -- true if text is character-exact from source
```

---

## Strategy Details

### FACT_QA
```
- Take highest-confidence AnswerSpan from ExtractiveQA
- Wrap in a single AnswerPoint with is_exact_span = true
- Section heading: null (short fact) or query topic
```

### LIST
```
1. Split evidence chunks into sentences
2. Score each sentence for query relevance (term overlap + entity overlap)
3. Select top-N sentences (N from format_hint or default 5)
4. Remove near-duplicate sentences (token overlap > 0.8)
5. Order by: source document order (not score order, to preserve narrative)
6. Each sentence becomes an AnswerPoint with is_exact_span = true
```

### SUMMARY
```
1. Group evidence by section heading
2. For each section, select representative sentences (MMR or greedy)
3. Order sections by document order
4. Create one AnswerSection per heading with ranked sentences
5. Apply global deduplication across sections
```

### TABLE
```
If source table exists in evidence:
  - Extract table cells directly (is_exact_span = true for all cells)
  - Represent as AnswerSection with structured key-value or grid format

If no source table but comparison evidence exists:
  - Create two AnswerSections (one per entity being compared)
  - Do NOT fabricate a table grid — use sections instead
  - Set is_complete = false if information is asymmetric
```

---

## Performance Expectations

| Route | Input (5 evidence chunks) | Target (Desktop) | Target (Android Mid) |
|---|---|---|---|
| FACT_QA | span wrap | < 1 ms | < 2 ms |
| LIST | sentence scoring | < 10 ms | < 30 ms |
| SUMMARY | section grouping | < 20 ms | < 60 ms |
| TABLE | cell extraction | < 5 ms | < 15 ms |

---

## Error States

```
NO_EVIDENCE            -- validated evidence list is empty
ROUTE_NOT_SUPPORTED    -- route type not implemented (fallback to FACT_QA)
SPAN_MISSING           -- FACT_QA route called but qa_spans is null or empty
TABLE_NOT_FOUND        -- TABLE route but no table in evidence; switch to SUMMARY
```

---

## Notes

- Every AnswerPoint.text must be traceable to a specific evidence_id.
- Sentence splitting for LIST/SUMMARY uses a simple rule-based splitter (NLTK punkt or regex).
- Sentences must not be merged or rewritten. Preserve source wording.
- The is_complete flag allows the UI to inform the user when the answer may be partial.
