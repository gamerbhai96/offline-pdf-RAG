# Interface: HybridRetriever

**Version**: 1.0.0
**Determinism**: DETERMINISTIC (given same indexes and same RRF parameters)
**Implemented in Phase**: 8

---

## Purpose

Combine dense vector retrieval and BM25 lexical retrieval using Reciprocal Rank Fusion (RRF).
Apply optional metadata filtering. Return a ranked list of evidence chunks.

---

## Input

```
retrieve(
  query: ResolvedQuery,
  options: RetrievalOptions
) → RetrievalResult

RetrievalOptions:
  top_k_dense: int          -- candidates from dense retrieval. Default: 20
  top_k_bm25: int           -- candidates from BM25. Default: 20
  top_k_final: int          -- final merged results returned. Default: 10
  doc_filter: UUID[] | null -- restrict to specific documents. null = all
  page_filter: int[] | null -- restrict to page range
  rrf_k: float              -- RRF smoothing constant. Default: 60
  dense_weight: float       -- RRF weight for dense results. Default: 1.0
  bm25_weight: float        -- RRF weight for BM25 results. Default: 1.0
  mode: DENSE_ONLY | BM25_ONLY | HYBRID   -- for ablation/debug
```

---

## Output

```
RetrievalResult:
  chunks: RankedChunk[]
  retrieval_mode: string     -- "hybrid" | "dense_only" | "bm25_only"
  dense_candidates: int
  bm25_candidates: int
  total_retrieved: int
  fusion_method: string      -- "rrf"

RankedChunk:
  chunk_id: UUID
  document_id: UUID
  page_number: int
  text: string
  dense_score: float         -- cosine similarity from dense retrieval
  bm25_score: float          -- BM25 score from lexical retrieval
  fusion_score: float        -- RRF combined score
  fusion_rank: int           -- rank in merged results (1-indexed)
  bounding_boxes: BoundingBox[]
```

---

## RRF Fusion Formula

```
RRF_score(d) = Σ_r weight_r / (k + rank_r(d))

Where:
  rank_r(d) = rank of document d in retrieval system r
  k         = smoothing constant (default 60)
  weight_r  = per-system weight (dense_weight or bm25_weight)

Documents appearing in only one system get rank = ∞ contribution from missing system.
```

---

## Error States

```
NO_INDEX             -- VectorIndex or LexicalIndex not initialized
EMPTY_RESULT         -- no chunks retrieved (not an error — triggers NO_ANSWER path)
RETRIEVAL_FAILED     -- underlying index error
INVALID_FILTER       -- doc_filter contains unknown document IDs
```

---

## Performance Expectations

| Corpus | Mode | Target (Desktop) | Target (Android Mid) |
|---|---|---|---|
| 10,000 chunks | Hybrid | < 50 ms | < 150 ms |
| 100,000 chunks | Hybrid | < 100 ms | < 400 ms |

---

## Notes

- When doc_filter is set to a single document, dense and BM25 searches are restricted to that document's index.
- The RRF k parameter and weights are tunable; calibrate against benchmark in Phase 17.
- Ablation modes (DENSE_ONLY, BM25_ONLY) must be supported for evaluation in Phase 17.
