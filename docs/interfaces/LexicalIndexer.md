# Interface: LexicalIndexer

**Version**: 1.0.0
**Determinism**: DETERMINISTIC
**Implemented in Phase**: 4

---

## Purpose

Build and query a BM25 lexical index over document chunks.
Critical for exact-match retrieval: names, acronyms, numbers, dates, identifiers, technical terms.

---

## Methods

### build_index

```
build_index(chunks: Chunk[], options: BM25Options) → IndexHandle

BM25Options:
  k1: float           -- term frequency saturation. Default: 1.5
  b: float            -- length normalization. Default: 0.75
  tokenizer: string   -- "whitespace" | "word" | "stemmed". Default: "word"
  stopwords: string[] -- words to exclude. Default: English stopwords
  language: string    -- BCP 47. Default: "en"
  index_version: string -- semver, stored in IndexMetadata
```

### search

```
search(query: string, top_k: int, doc_filter: string[] | null) → LexicalResult[]

query: string
  Raw or normalized query text.

top_k: int
  Number of results to return.

doc_filter: string[] | null
  Optional list of document_ids to restrict search. null = all documents.

Returns:
  LexicalResult[]
    chunk_id: UUID
    document_id: UUID
    page_number: int
    score: float       -- BM25 score, not normalized
    text: string       -- chunk text excerpt
```

### add_chunks (incremental)

```
add_chunks(chunks: Chunk[]) → void
  Add new chunks to an existing index without full rebuild.
```

### remove_document

```
remove_document(document_id: UUID) → void
  Remove all chunks for a document from the index.
```

### save / load

```
save(path: string) → void
load(path: string) → IndexHandle
```

---

## Required Metadata (persisted with index)

```
index_path: string
document_ids: UUID[]          -- all documents covered
total_chunks: int
vocabulary_size: int
bm25_k1: float
bm25_b: float
tokenizer: string
language: string
index_version: string
created_at: ISO 8601 string
```

---

## Error States

```
INDEX_NOT_BUILT       -- search called before build
INDEX_CORRUPT         -- loaded index fails integrity check
EMPTY_INDEX           -- no chunks indexed
TOKENIZATION_FAILED   -- tokenizer error
IO_ERROR              -- save/load disk error
```

---

## Performance Expectations

| Corpus Size | Build Time (Desktop) | Search Time |
|---|---|---|
| 1,000 chunks | < 1 second | < 5 ms |
| 10,000 chunks | < 5 seconds | < 20 ms |
| 100,000 chunks | < 60 seconds | < 100 ms |

---

## Notes

- BM25 is especially effective for: exact names, technical terminology, dates, numbers, abbreviations, rare words.
- Stemming may improve recall for inflected forms but risks false positives for technical terms. Configurable.
- Index is serialized using msgpack for compact, cross-version storage.
- Incremental additions are supported; full rebuild is triggered when tokenizer/version changes.
