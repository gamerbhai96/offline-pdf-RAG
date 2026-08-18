# Interface: Chunker

**Version**: 1.0.0
**Determinism**: DETERMINISTIC (same input + same strategy/version → same chunks)
**Implemented in Phase**: 2

---

## Purpose

Split a structured document into Chunk objects suitable for embedding and retrieval.
The strategy and version must be stored in IndexMetadata to enable compatibility detection.

---

## Input

```
document: StructuredDocument
  Output from StructureAnalyzer.

options: ChunkOptions
  strategy: ChunkStrategy
    HEADING_AWARE       -- split at headings, merge small paragraphs
    PARAGRAPH           -- split at paragraph boundaries
    FIXED_OVERLAP       -- fixed token window with overlap (baseline only)
    SENTENCE            -- split at sentence boundaries

  max_tokens: int               -- default 256; maximum tokens per chunk
  min_tokens: int               -- default 32; discard chunks below this
  overlap_tokens: int           -- default 32; token overlap between adjacent chunks
  strategy_version: string      -- semver of chunking implementation, e.g. "1.2"
  preserve_tables: boolean      -- default true; tables become single chunks
  preserve_lists: boolean       -- default true; lists stay together when possible
```

---

## Output

```
chunks: Chunk[]                 -- see /docs/schemas/Chunk.json

Each Chunk:
  chunk_id: UUID
  document_id: UUID
  page_id: UUID
  section_id: UUID | null
  text: string                  -- clean, header/footer stripped
  token_count: int
  start_offset: int             -- character offset in page text
  end_offset: int
  bounding_boxes: BoundingBox[] -- may span multiple text blocks
  chunk_index: int              -- sequential index within document
  parent_chunk_id: UUID | null  -- for hierarchical chunking
  chunk_type: TEXT | TABLE | LIST | HEADING
  strategy: string              -- echoed from options
  strategy_version: string      -- echoed from options
```

---

## Required Metadata

```
total_chunks: int
strategy: string
strategy_version: string
avg_token_count: float
min_token_count: int
max_token_count: int
```

---

## Error States

```
EMPTY_DOCUMENT          -- no sections or text to chunk
STRATEGY_UNSUPPORTED    -- requested strategy not implemented
TOKEN_LIMIT_EXCEEDED    -- single paragraph exceeds max_tokens (split forced)
```

---

## Performance Expectations

| Device Class | Target (200-page, ~1000 chunks) |
|---|---|
| Desktop | < 1 second |
| High-end Android | < 3 seconds |
| Mid-range Android | < 8 seconds |

---

## Notes

- The primary strategy for Phase 2 is HEADING_AWARE. Others are benchmarked in Phase 17.
- Tables and lists must not be split across chunks unless they exceed max_tokens.
- Header and footer text (from StructureAnalyzer) must be stripped before chunking.
- A chunk's bounding_boxes must cover ALL text in the chunk for accurate source highlighting.
- Parent-child relationships (parent_chunk_id) support hierarchical retrieval where a section
  summary is the parent and individual paragraphs are children.
- Changing max_tokens, strategy, or strategy_version invalidates the existing index.
