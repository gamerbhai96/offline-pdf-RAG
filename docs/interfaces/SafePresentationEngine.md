# Interface: SafePresentationEngine

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 12

---

## Purpose

Format a validated answer into a user-presentable structure.
Source structure takes priority over generated structure.
This engine does NOT generate new content. It only formats what evidence provides.

Core principle: SOURCE STRUCTURE > GENERATED STRUCTURE

---

## Input

```
format(
  answer: AnswerDraft,
  route: RouteDecision,
  citations: Citation[],
  confidence: ConfidenceResult
) → PresentableAnswer
```

---

## Output — PresentableAnswer

```
PresentableAnswer:
  title: string | null             -- derived from query topic, not generated
  sections: PresentableSection[]
  confidence_level: ConfidenceLevel
  confidence_note: string | null   -- shown when level is LOW
  completeness_note: string | null -- shown when is_complete = false
  citations: Citation[]
  format_used: AnswerFormat
  format_source: string            -- "user_explicit" | "inferred" | "fallback"
  fallback_used: boolean

PresentableSection:
  heading: string | null
  content_type: FACT | BULLETS | NUMBERED | TABLE | PARAGRAPH | QUOTE
  items: PresentableItem[]

PresentableItem:
  text: string                     -- exact or source-formatted
  citation_ids: UUID[]
  is_exact_span: boolean
```

---

## Format Rules

```
FACT (short answer):
  - Display single sentence or phrase
  - Show confidence badge
  - Attach citation inline

BULLETS:
  - Each AnswerPoint becomes a bullet
  - Max items: respect format_hint count if specified
  - Do not rewrite sentences

NUMBERED:
  - Same as BULLETS but numbered (for STEPS type)
  - Preserve source order for procedural content

TABLE:
  - If source table: render as-is
  - If constructed from parallel evidence: render as sections, not fabricated table
  - Cells must contain exact source text only

PARAGRAPH:
  - Join selected sentences with minimal formatting
  - No added connectives that change meaning
  - Preserve sentence boundaries

QUOTE:
  - Render in blockquote format
  - Must be is_exact_span = true
  - Full citation mandatory
```

---

## Fallback Behavior

```
If requested format cannot be safely produced:
  1. Select next safest format
  2. Set fallback_used = true
  3. Set completeness_note explaining why

Example:
  User requests TABLE
  Evidence has no table structure
  → Fall back to BULLETS
  → completeness_note: "A table could not be constructed from the available evidence.
                        Showing relevant points instead."
```

---

## Performance Expectations

| Answer Size | Target |
|---|---|
| 5 points, 2 sections | < 2 ms |
| 20 points, 5 sections | < 10 ms |

---

## Error States

```
EMPTY_ANSWER          -- AnswerDraft has no points
VALIDATION_FAILED     -- AnswerDraft was not validated (should not reach here)
FORMAT_NOT_SUPPORTED  -- Unknown format type
```

---

## Notes

- This engine DOES NOT call any ML model.
- It DOES NOT rewrite, paraphrase, or synthesize content.
- It only applies deterministic structural formatting to already-extracted evidence.
- LOW confidence answers must include a visible disclaimer, not just a badge.
