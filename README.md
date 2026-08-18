# Offline Document Intelligence Engine

A production-quality, privacy-first, completely offline document intelligence application.

## Core Philosophy

> "Find the answer in my documents, show me exactly where it came from, and never make something up."

```
RETRIEVE → VERIFY → EXTRACT → ORGANIZE → VALIDATE → CITE
```

## Architecture

Six engines working in sequence:

1. **Document Engine** — PDF parsing, OCR, layout analysis
2. **Indexing Engine** — Chunking, embedding, BM25, HNSW
3. **Question Engine** — Query understanding, coreference, routing
4. **Retrieval Engine** — Hybrid dense+BM25 retrieval, rank fusion
5. **Answer Engine** — Route-specific extraction, safe presentation
6. **Trust Engine** — Dual-gate validation, confidence, citations


## Key Constraints

- ✅ Completely offline after setup
- ✅ No generative LLMs
- ✅ No cloud APIs
- ✅ Permissively licensed dependencies only
- ✅ Android-compatible architecture

## Project Structure

```
/apps         → Android and Web applications
/core         → Intelligence engines (language-independent contracts)
/docs         → Interface specs, schemas, licensing registry
/storage      → Database schema and migrations
/models       → ONNX model files and registry
/benchmarks   → Evaluation harness and datasets
/tests        → Unit, integration, retrieval, accuracy tests
/cli          → CLI evaluation harness (Phase 14)
/scripts      → Setup, model download, utilities
```

## Absolute Rules

1. NO SOURCE → NO ANSWER
2. NO UNSUPPORTED FACTS
3. SOURCE STRUCTURE > GENERATED STRUCTURE
4. EXTRACT > REWRITE
5. IF FINAL VALIDATION FAILS → DISCARD ENTIRE ANSWER
