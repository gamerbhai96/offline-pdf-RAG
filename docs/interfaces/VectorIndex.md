# Interface: VectorIndex

**Version**: 1.0.0
**Determinism**: DETERMINISTIC for BruteForce; APPROXIMATELY_DETERMINISTIC for HNSW (ANN)
**Implemented in Phase**: 5

---

## Purpose

Store and search dense embedding vectors. Abstracts two implementations:
- `BruteForceIndex` — exact cosine search, for small corpora
- `HNSWIndex` — approximate nearest neighbour, for large corpora

The implementation is selected automatically based on corpus size (configurable threshold).
Callers interact with a single `VectorIndex` interface.

---

## Implementations

### BruteForceIndex
- Exact cosine similarity via numpy dot product on L2-normalized vectors
- Suitable for: < 5,000 chunks (configurable threshold)
- Storage: numpy .npy file
- RAM: ~1.5 MB per 1,000 chunks at 384 dimensions

### HNSWIndex
- Approximate nearest neighbour via hnswlib
- Suitable for: ≥ 5,000 chunks
- Storage: hnswlib .bin file
- RAM: ~6 MB per 1,000 chunks at 384 dimensions (configurable M, ef)

---

## Methods

### add_vectors

```
add_vectors(vectors: float[][], chunk_ids: UUID[], document_ids: UUID[]) → void

vectors: float[][]    -- L2-normalized, dimension must match index config
chunk_ids: UUID[]     -- parallel array, maps vector position to chunk
document_ids: UUID[]  -- parallel array, maps vector position to document
```

### search

```
search(
  query_vector: float[],
  top_k: int,
  doc_filter: UUID[] | null    -- restrict to these document_ids. null = all
) → VectorResult[]

VectorResult:
  chunk_id: UUID
  document_id: UUID
  score: float           -- cosine similarity, 0.0–1.0
  rank: int              -- 1-indexed rank
```

### remove_document

```
remove_document(document_id: UUID) → void
  Mark all vectors for document as deleted. Compaction may be deferred.
```

### save / load

```
save(path: string) → void
load(path: string) → void
```

### get_stats

```
get_stats() → IndexStats
  total_vectors: int
  active_vectors: int    -- excluding deleted
  deleted_vectors: int
  implementation: string -- "brute_force" | "hnsw"
  dimension: int
  size_bytes: int
```

---

## Required Metadata (in IndexMetadata)

```
vector_index_type: "brute_force" | "hnsw"
vector_index_version: string    -- hnswlib version or "numpy-1.0"
hnsw_M: int | null              -- HNSW construction parameter
hnsw_ef_construction: int | null
hnsw_ef_search: int | null
distance_metric: "cosine"       -- only cosine supported in v1
```

---

## HNSW Parameters (defaults, tunable)

| Parameter | Default | Effect |
|---|---|---|
| M | 16 | Graph degree. Higher = better recall, more RAM |
| ef_construction | 200 | Build quality. Higher = better recall, slower build |
| ef_search | 50 | Search quality. Higher = better recall, slower query |

---

## Auto-Switch Threshold

```
BRUTE_FORCE_MAX_CHUNKS: int = 5000    -- configurable

if total_chunks < BRUTE_FORCE_MAX_CHUNKS:
    use BruteForceIndex
else:
    use HNSWIndex
```

---

## Error States

```
INDEX_NOT_INITIALIZED   -- add_vectors not called before search
DIMENSION_MISMATCH      -- query vector dimension ≠ index dimension
INDEX_CORRUPT           -- loaded file fails checksum or header check
VECTOR_NOT_FOUND        -- chunk_id not in index
IO_ERROR                -- save/load disk error
```

---

## Performance Expectations

| Corpus | Implementation | Build (Desktop) | Search k=10 |
|---|---|---|---|
| 1,000 chunks | BruteForce | < 100 ms | < 2 ms |
| 5,000 chunks | BruteForce | < 500 ms | < 10 ms |
| 10,000 chunks | HNSW | < 3 seconds | < 5 ms |
| 100,000 chunks | HNSW | < 30 seconds | < 15 ms |

---

## Notes

- All vectors MUST be L2-normalized before insertion. Cosine similarity is computed as dot product.
- Per-document index files are recommended for Android RAM management.
- When searching across multiple documents, merge results from multiple index files, re-rank by score.
- HNSW recall@10 target: ≥ 0.95 vs brute force. Verified in Phase 17 ablation.
