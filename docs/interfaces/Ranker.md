# Interface: Ranker

**Version**: 1.0.0
**Determinism**: DETERMINISTIC for DeterministicRanker; NON_DETERMINISTIC for LearnedRanker
**Implemented in Phase**: 9

---

## Purpose

Rerank retrieved chunks to improve relevance ordering before answer extraction.
Two implementations with automatic selection based on device capability.

---

## Implementations

### DeterministicRanker
Computes a weighted score from retrieval signals. No ML model required. Used on low-end devices.

### LearnedRanker
Cross-encoder reranker (ms-marco-MiniLM-L-6-v2, ONNX INT8). Used on mid/high-end devices.

---

## Input

```
rerank(
  query: ResolvedQuery,
  chunks: RankedChunk[],
  options: RankOptions
) → RankedChunk[]

RankOptions:
  mode: FAST | BALANCED | HIGH_ACCURACY
    FAST          → DeterministicRanker always
    BALANCED      → DeterministicRanker (low-end), LearnedRanker (mid+)
    HIGH_ACCURACY → LearnedRanker always (may be slow on low-end)

  top_k: int            -- return top_k after reranking. Default: 10
  min_score: float      -- discard chunks below this score. Default: 0.0
```

---

## Output

```
RankedChunk[]         -- same type as input, reordered with updated scores

Additional fields populated after reranking:
  rerank_score: float       -- final score after reranking
  rerank_method: string     -- "deterministic" | "cross_encoder"
  rerank_rank: int          -- rank after reranking (1-indexed)
```

---

## DeterministicRanker Scoring Formula

```
score =
  w1 * dense_score               -- cosine similarity [0, 1]
  + w2 * normalized_bm25         -- BM25 normalized to [0, 1]
  + w3 * query_term_overlap      -- fraction of query terms in chunk [0, 1]
  + w4 * entity_overlap          -- fraction of query entities in chunk [0, 1]
  + w5 * section_heading_match   -- 1 if section heading matches query topic, else 0
  + w6 * page_recency            -- 0.0 (no bias by default)

Default weights (to be calibrated in Phase 17):
  w1 = 0.40
  w2 = 0.25
  w3 = 0.15
  w4 = 0.10
  w5 = 0.10
  w6 = 0.00
```

---

## Error States

```
MODEL_NOT_AVAILABLE   -- LearnedRanker model not loaded; fall back to Deterministic
EMPTY_INPUT           -- no chunks to rerank
INFERENCE_FAILED      -- cross-encoder ONNX error; fall back to Deterministic
```

---

## Performance Expectations

| Mode | Corpus | Desktop | Android Mid | Android Low |
|---|---|---|---|---|
| FAST (Deterministic) | 20 chunks | < 2 ms | < 10 ms | < 20 ms |
| BALANCED (Cross-encoder) | 20 chunks | < 200 ms | < 500 ms | < 1500 ms |

---

## Notes

- On devices with < 3 GB RAM, BALANCED and HIGH_ACCURACY modes fall back to FAST automatically.
- The LearnedRanker model must not be held in memory simultaneously with the EmbeddingEngine.
- DeterministicRanker weights are configurable and calibrated against benchmark data in Phase 17.
- If the cross-encoder fails for any reason, the system MUST fall back to deterministic ranking silently.
