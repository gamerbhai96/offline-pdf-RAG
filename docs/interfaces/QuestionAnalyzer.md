# Interface: QuestionAnalyzer

**Version**: 1.0.0
**Determinism**: DETERMINISTIC (rule-based and lightweight ML components)
**Implemented in Phase**: 6

---

## Purpose

Analyze a raw user question to produce a NormalizedQuery with extracted entities,
detected question type, format hints, and document scope.
This runs BEFORE coreference resolution and BEFORE retrieval.

---

## Input

```
analyze(
  raw_query: string,
  conversation_context: ConversationContext | null
) → NormalizedQuery
```

---

## Output — NormalizedQuery

```
raw_query: string                   -- original user input, unchanged
normalized_query: string            -- lowercased, stripped, punctuation normalized
entities: Entity[]                  -- extracted named entities
  Entity:
    text: string
    type: string                    -- PERSON | ORG | TECH | DATE | NUMBER | ABBREV | OTHER
    start: int
    end: int

question_type: QuestionType
  FACT | DEFINITION | EXPLANATION | LIST | STEPS |
  COMPARISON | SUMMARY | QUOTE | NUMERICAL | TABLE | UNKNOWN

format_hint: FormatHint | null
  BULLETS | NUMBERED | TABLE | PARAGRAPH | QUOTE | null

document_scope: string | "ALL"      -- document_id if detected, else "ALL"
important_terms: string[]           -- key terms beyond entities (technical, rare)
ambiguity_flag: boolean
ambiguity_reason: string | null
```

---

## Question Type Detection Signals

| Signal | Examples | Type |
|---|---|---|
| "what is", "define", "meaning of" | "What is TCP?" | DEFINITION |
| "what are the advantages/benefits" | | LIST |
| "how many", "when", "which year", number words | | NUMERICAL |
| "list", "enumerate", "what are the steps" | | LIST / STEPS |
| "compare", "difference between", "vs" | | COMPARISON |
| "summarize", "overview", "in brief" | | SUMMARY |
| "exact words", "quote", "verbatim" | | QUOTE |
| "give me a table", "in tabular form" | | TABLE (format_hint) |
| "explain in points", "in bullets" | | format_hint = BULLETS |

---

## Format Hint Detection

Explicit user format requests MUST be detected and stored:

| User Input | format_hint |
|---|---|
| "in bullet points", "as bullets" | BULLETS |
| "in a numbered list" | NUMBERED |
| "as a table", "in tabular form" | TABLE |
| "in one paragraph" | PARAGRAPH |
| "exact quote", "verbatim" | QUOTE |

---

## Error States

```
EMPTY_QUERY           -- query is empty or only whitespace
QUERY_TOO_LONG        -- query exceeds 512 tokens (warn, truncate)
ENTITY_EXTRACTION_FAILED -- NER model unavailable; proceed without entities
```

---

## Performance Expectations

| Device Class | Target |
|---|---|
| Desktop | < 20 ms |
| Android (rule-based) | < 10 ms |
| Android (NER model) | < 50 ms |

---

## Notes

- On Android, entity extraction defaults to rule-based (no spaCy) unless an embedded NER model is bundled.
- The QuestionAnalyzer does NOT resolve follow-up references ("its", "they"). That is ConversationResolver's job.
- QuestionType is a signal for routing, not a guarantee of answer format.
