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

## Development Phases

| Phase | Title | Status |
|---|---|---|
| 0 | Architecture + Evaluation + Licensing | ✅ Complete |
| 1 | PDF Engine + OCR | ⏳ Pending |
| 2 | Document Structure + Chunking | ⏳ Pending |
| 3 | Embedding Model Benchmark | ⏳ Pending |
| 4 | BM25 / Lexical Index | ⏳ Pending |
| 5 | Vector Index / HNSW | ⏳ Pending |
| 6 | Question Understanding + ConversationContext | ⏳ Pending |
| 7 | Question Router | ⏳ Pending |
| 8 | Hybrid Retrieval | ⏳ Pending |
| 9 | Adaptive Ranking + Evidence Validation | ⏳ Pending |
| 10 | Route-Specific Answer Extraction | ⏳ Pending |
| 11 | Answer Validation + Confidence | ⏳ Pending |
| 12 | Safe Presentation Engine | ⏳ Pending |
| 13 | Citations + PDF Highlighting | ⏳ Pending |
| 14 | CLI Evaluation + Debug Harness | ⏳ Pending |
| 15 | Web/Desktop Application | ⏳ Pending |
| 16 | Offline Android Application | ⏳ Pending |
| 17 | Performance + Memory + Battery Optimization | ⏳ Pending |
| 18 | Final Accuracy + Ablation Testing | ⏳ Pending |
| 19 | Production Hardening + Release | ⏳ Pending |

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
