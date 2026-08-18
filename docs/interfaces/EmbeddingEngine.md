# Interface: EmbeddingEngine

**Version**: 1.0.0
**Determinism**: DETERMINISTIC for same model + same input + same preprocessing version
**Implemented in Phase**: 3

---

## Purpose

Convert text into dense embedding vectors for semantic retrieval.
This interface is the SOLE OWNER of query/passage prefix logic.
Callers MUST NOT manually add model-specific prefixes.

---

## Input — embed_query

```
embed_query(text: string) → EmbeddingResult

text: string
  The user's search query or question. Raw, no prefix added by caller.
```

## Input — embed_passage

```
embed_passage(text: string) → EmbeddingResult

text: string
  A document passage or chunk text. Raw, no prefix added by caller.
```

## Input — embed_batch

```
embed_batch(texts: string[], mode: QUERY | PASSAGE) → EmbeddingResult[]

texts: string[]
  List of texts to embed. Must use batching for performance.
  Max batch size: configurable (default 32, lower on low-RAM devices).
```

---

## Output — EmbeddingResult

```
vector: float[]           -- normalized embedding vector
dimension: int            -- must match model.dimension
model_id: string          -- echoed from model config
model_version: string     -- echoed from model config
preprocessing_version: string -- echoed from preprocessing config
```

---

## Properties (read-only, from model registry)

```
model_id: string
model_version: string
quantization: string      -- "none" | "int8" | "fp16"
dimension: int
normalization: string     -- "L2" | "none"
preprocessing_version: string
query_prefix: string      -- owned by engine, not exposed to callers
passage_prefix: string    -- owned by engine, not exposed to callers
```

---

## Per-Model Prefix Behavior (Internal — not part of public API)

| Model ID | Query Prefix | Passage Prefix | Notes |
|---|---|---|---|
| `e5-small-v2` | `"query: "` | `"passage: "` | Required — omitting degrades recall by ~15% |
| `e5-large-v2` | `"query: "` | `"passage: "` | Same as e5-small |
| `bge-small-en-v1.5` | `"Represent this sentence for searching relevant passages: "` | `""` | Only query prefix |
| `all-MiniLM-L6-v2` | `""` | `""` | No prefixes needed |
| `all-MiniLM-L12-v2` | `""` | `""` | No prefixes needed |

---

## Error States

```
MODEL_NOT_LOADED        -- ONNX model not yet loaded into memory
TOKENIZATION_FAILED     -- tokenizer could not process input
SEQUENCE_TOO_LONG       -- input truncated to max_length (warn, not fail)
INFERENCE_FAILED        -- ONNX runtime error
DIMENSION_MISMATCH      -- output dimension does not match configured dimension
```

---

## Compatibility Rules

Two embeddings are compatible ONLY when ALL of the following match:
- model_id
- model_version
- quantization
- preprocessing_version
- normalization
- dimension

Equal dimensions alone do NOT imply compatibility.

---

## Performance Expectations

| Device Class | Target (single chunk, 128 tokens) | Target (batch of 32) |
|---|---|---|
| Desktop | < 10 ms | < 100 ms |
| High-end Android (INT8) | < 20 ms | < 300 ms |
| Mid-range Android (INT8) | < 50 ms | < 800 ms |
| Low-end Android (INT8) | < 100 ms | < 2000 ms |

---

## Notes

- Model is loaded lazily. Do not hold model in memory when not actively embedding.
- On low-end devices (< 3 GB RAM), unload embedding model after indexing completes.
- Batch size should be reduced automatically when available RAM is low.
- ONNX INT8 quantization is mandatory for Android deployment.
- L2 normalization is applied to all output vectors unless model config says otherwise.
