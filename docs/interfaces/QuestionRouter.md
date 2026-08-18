# Interface: QuestionRouter

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 7

---

## Purpose

Route a resolved query to the appropriate answer extraction strategy.
This component sits between QuestionUnderstanding and AnswerExtraction.
It is the mandatory gatekeeper that prevents the ExtractiveQA model from
being called on question types it cannot handle well.

---

## Architecture Position

```
QuestionAnalyzer
    ↓
ConversationResolver
    ↓
QuestionRouter    ← This interface
    ↓
    ┌──────────────┬──────────────┬──────────────────┬──────────────┐
    ▼              ▼              ▼                  ▼              ▼
 FACT_QA         LIST          SUMMARY            TABLE        NO_ANSWER
    ↓              ↓              ↓                  ↓
 ExtractiveQA   Evidence       Sentence           Table
 (span model)   Extraction     Ranking            Extraction
```

---

## Input

```
route(
  query: ResolvedQuery,
  retrieval_preview: RetrievalPreview | null
) → RouteDecision

RetrievalPreview (optional, from a lightweight pre-retrieval probe):
  has_table_evidence: boolean     -- does top evidence contain tables?
  evidence_count: int             -- how many chunks retrieved?
  top_score: float                -- fusion score of top result
```

---

## Output — RouteDecision

```
route: Route
  FACT_QA       -- extractive span from a single passage
  LIST          -- ranked evidence sentences into bullet/numbered list
  SUMMARY       -- hierarchical sentence selection (multi-section)
  TABLE         -- extract or reconstruct a table from evidence
  NO_ANSWER     -- insufficient or ambiguous evidence

answer_format: AnswerFormat
  SHORT_FACT | EXTRACTED_PARAGRAPH | BULLET_LIST | NUMBERED_LIST |
  TABLE | SECTION_SUMMARY | EXACT_QUOTE | KEY_VALUE | NO_ANSWER

format_source: string
  "user_explicit"     -- user directly requested this format
  "question_type"     -- inferred from question type
  "evidence_structure" -- chosen because evidence contains matching structure
  "fallback"          -- default for route

confidence: float               -- routing confidence 0.0–1.0
routing_reason: string          -- human-readable explanation for debug
```

---

## Routing Decision Logic

```
1. If query.ambiguity_flag = true AND resolution_confidence < 0.5:
   → route = NO_ANSWER (prompt user for clarification)

2. If retrieval_preview.evidence_count = 0 OR top_score < MIN_SCORE_THRESHOLD:
   → route = NO_ANSWER

3. If query.format_hint = TABLE:
   → route = TABLE (if has_table_evidence) else SUMMARY with note

4. If query.format_hint = BULLETS or NUMBERED:
   → route = LIST with requested format

5. If query.format_hint = QUOTE:
   → route = FACT_QA with answer_format = EXACT_QUOTE

6. If query.format_hint = PARAGRAPH:
   → route = SUMMARY with answer_format = EXTRACTED_PARAGRAPH

7. If question_type in {FACT, DEFINITION, NUMERICAL, QUOTE}:
   → route = FACT_QA

8. If question_type in {LIST, STEPS}:
   → route = LIST

9. If question_type in {COMPARISON} AND has_table_evidence:
   → route = TABLE
   else → route = LIST (side-by-side sections)

10. If question_type in {SUMMARY, EXPLANATION}:
    → route = SUMMARY

11. Default:
    → route = FACT_QA (safest extractive fallback)
```

---

## Error States

```
AMBIGUOUS_QUERY       -- ambiguity_flag is true; caller should prompt user
INSUFFICIENT_EVIDENCE -- evidence_count is 0 or scores too low
ROUTING_CONFLICT      -- format_hint conflicts with evidence structure (log, use format_hint)
```

---

## Performance Expectations

| Device Class | Target |
|---|---|
| Desktop | < 2 ms |
| Android | < 2 ms |

---

## Notes

- This is a pure routing/decision component. No ML inference occurs here.
- Explicit user format requests (format_hint) always take precedence over automatic routing.
- If the requested format cannot be produced (e.g., TABLE requested but no table evidence),
  route to the next safest option and populate routing_reason with the explanation.
- The routing_reason field is surfaced in CLI debug mode and used to explain fallbacks to users.
