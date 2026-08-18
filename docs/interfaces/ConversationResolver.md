# Interface: ConversationResolver

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 6

---

## Purpose

Resolve follow-up references (pronouns, ellipsis, implicit topics) in user queries
using conversation history and a lightweight entity stack.
Produces a ResolvedQuery ready for retrieval.

If resolution confidence is insufficient, sets ambiguity_flag = true
and does NOT guess.

---

## Input

```
resolve(
  query: NormalizedQuery,
  context: ConversationContext
) → ResolvedQuery
```

---

## Output — ResolvedQuery

Extends NormalizedQuery with:
```
resolved_query: string            -- query with references substituted
  Example: "What are its advantages?" → "What are the advantages of TCP?"

coreference_resolved: boolean     -- true if any substitution was made
resolution_confidence: float      -- 0.0–1.0
ambiguity_flag: boolean           -- true if resolution is uncertain
ambiguity_reason: string | null   -- explanation for user if flag is set
resolution_trace: ResolutionStep[] -- audit trail of substitutions made

ResolutionStep:
  original_span: string          -- e.g. "its"
  resolved_to: string            -- e.g. "TCP"
  resolution_method: string      -- "pronoun-entity-stack" | "recency" | "explicit"
  confidence: float
```

---

## Resolution Strategy (Rule-Based)

```
Priority order:
1. Explicit reference ("about TCP" in same query) → direct
2. Pronoun heuristics:
   - "it", "its", "this" → most recent singular entity in entity_stack
   - "they", "their", "these" → most recent plural entity
   - "he/she/his/her" → most recent person entity
3. Recency fallback → last mentioned topic regardless of type
4. If no candidate: ambiguity_flag = true, do NOT substitute

Confidence thresholds:
  > 0.8 → resolve and proceed
  0.5–0.8 → resolve but flag low confidence
  < 0.5 → ambiguity_flag = true, ask for clarification
```

---

## ConversationContext

```
session_id: UUID
turn_index: int
resolved_topics: string[]           -- topics confirmed in conversation
entity_stack: EntityEntry[]
  EntityEntry:
    text: string
    type: string
    turn: int                       -- turn index when first mentioned
    mention_count: int

last_document_scope: string | "ALL"
last_format_preference: string | null
updated_at: ISO 8601 string
```

---

## Error States

```
NO_CONTEXT            -- context is null; return query unchanged (first turn)
AMBIGUOUS_REFERENCE   -- reference cannot be resolved confidently
ENTITY_STACK_EMPTY    -- reference detected but no candidates in history
```

---

## Performance Expectations

| Device Class | Target |
|---|---|
| Desktop | < 5 ms |
| Android | < 5 ms |

---

## Notes

- This is entirely rule-based. No generative model is used.
- The ABSOLUTE RULE: if the system cannot confidently resolve a reference, it MUST set ambiguity_flag = true.
- The caller (QuestionRouter or UI) must handle ambiguity_flag by prompting the user for clarification.
- Entity stack should be bounded to last N turns (default N = 10) to prevent stale references.
