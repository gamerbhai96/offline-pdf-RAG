# Dependency Licensing Registry

> **Policy**: No production dependency may be introduced without an entry in this file.
> This file is reviewed and updated at the start of every phase.
>
> **Commercial intent**: This application may be distributed as a commercial, closed-source product.
> All dependencies must be compatible with this intent.
>
> Last updated: Phase 0

---

## ⛔ Prohibited Dependencies

These libraries are PROHIBITED in the production dependency tree without a separately purchased commercial license.

| Dependency | License | Risk | Decision |
|---|---|---|---|
| MuPDF | AGPL v3 / Commercial | AGPL forces full source disclosure of linked app | **PROHIBITED** unless commercial license purchased |
| PyMuPDF (fitz) | AGPL v3 / Commercial | Same contamination as MuPDF | **PROHIBITED** unless commercial license purchased |
| Camelot | GPL v3 | GPL forces source disclosure of linked code | **PROHIBITED** |
| tabula-py | MIT (Python binding) but uses Tabula (MIT/Apache) — verify JVM lib | Check transitive | **REVIEW REQUIRED** before use |

---

## ✅ Approved Dependencies

### Python / Desktop / CLI

| Dependency | Version | License | Commercial Use | Distribution Requirements | Reason for Selection | Alternatives |
|---|---|---|---|---|---|---|
| `pdfplumber` | ≥0.11 | MIT | ✅ Yes | None | Best open-source PDF text + table extraction with coordinate support | pdfminer.six (MIT, lower-level) |
| `pdfminer.six` | ≥20221105 | MIT | ✅ Yes | None | Low-level PDF layout engine underlying pdfplumber; used for direct layout access | pdfplumber wraps this |
| `pypdf` | ≥4.0 | BSD 3-Clause | ✅ Yes | None | PDF metadata, page count, password detection, basic PDF manipulation | pikepdf (LGPL — review needed) |
| `Pillow` | ≥10.0 | HPND (Historical Permission Notice) — permissive | ✅ Yes | None | Image extraction from PDF pages; preprocessing for OCR | None at this size class |
| `pytesseract` | ≥0.3 | Apache 2.0 | ✅ Yes | None | Python binding for Tesseract OCR | easyocr (Apache 2.0, larger) |
| `tesseract` (binary) | ≥5.0 | Apache 2.0 | ✅ Yes | Must include Apache 2.0 notice | OCR engine for scanned PDFs on Python/CLI | ML Kit (Android only) |
| `rank_bm25` | ≥0.2 | Apache 2.0 | ✅ Yes | None | BM25 implementation for lexical retrieval | Whoosh (MIT), Elasticsearch (SSPL — commercial concern) |
| `hnswlib` | ≥0.7 | Apache 2.0 | ✅ Yes | None | HNSW approximate nearest neighbour index | faiss (MIT, but heavy), ScaNN (Apache 2.0) |
| `onnxruntime` | ≥1.17 | MIT | ✅ Yes | None | Cross-platform ONNX model inference runtime | TFLite (Apache 2.0), PyTorch (BSD) |
| `numpy` | ≥1.26 | BSD 3-Clause | ✅ Yes | None | Numerical operations for embeddings and scoring | — |
| `scipy` | ≥1.12 | BSD 3-Clause | ✅ Yes | None | Cosine similarity, statistical utilities | — |
| `tokenizers` | ≥0.19 | Apache 2.0 | ✅ Yes | None | HuggingFace fast tokenizer (Rust-backed) for embedding preprocessing | SentencePiece (Apache 2.0) |
| `spacy` (small model) | ≥3.7 | MIT (library) + MIT (en_core_web_sm model) | ✅ Yes | None | Entity extraction for Question Engine (Python/CLI only) | NLTK (Apache 2.0), rule-based fallback |
| `pytest` | ≥8.0 | MIT | ✅ Dev only | None | Test framework | unittest (stdlib) |
| `pytest-cov` | ≥5.0 | MIT | ✅ Dev only | None | Coverage reporting | — |
| `click` | ≥8.1 | BSD 3-Clause | ✅ Yes | None | CLI framework for evaluation harness | argparse (stdlib) |
| `rich` | ≥13.0 | MIT | ✅ Yes | None | Rich terminal output for CLI harness | colorama (BSD) |
| `jsonschema` | ≥4.21 | MIT | ✅ Yes | None | JSON Schema validation for data contracts | — |
| `msgpack` | ≥1.0 | Apache 2.0 | ✅ Yes | None | Compact binary serialization for index caching | pickle (stdlib, but not cross-lang) |
| `tqdm` | ≥4.66 | MIT | ✅ Yes | None | Progress bars for indexing pipeline | — |
| `python-dotenv` | ≥1.0 | BSD 3-Clause | ✅ Yes | None | Configuration from .env files | — |

### Android / Kotlin

| Dependency | Version | License | Commercial Use | Distribution Requirements | Reason for Selection | Alternatives |
|---|---|---|---|---|---|---|
| PDFium (Android system) | System-provided | BSD 3-Clause | ✅ Yes | Bundled by AOSP; redistributed as system component | Native PDF rendering + text extraction + coordinate access | MuPDF (AGPL — prohibited) |
| `barteksc/AndroidPdfViewer` | ≥3.2 | Apache 2.0 | ✅ Yes | Include Apache 2.0 notice | PDFium wrapper for Android PDF rendering in views | Custom PDFium JNI |
| ML Kit Text Recognition | latest | Apache 2.0 | ✅ Yes | None | On-device OCR, no network required, fast, Google-maintained | Tesseract4Android (Apache 2.0, fallback) |
| `onnxruntime-android` (AAR) | ≥1.17 | MIT | ✅ Yes | None | ONNX model inference on Android; official Microsoft AAR | TFLite (Apache 2.0) |
| AndroidX Room | ≥2.6 | Apache 2.0 | ✅ Yes | None | SQLite ORM for Android with schema migration support | SQLDelight (Apache 2.0) |
| Jetpack Compose | ≥1.6 | Apache 2.0 | ✅ Yes | None | Declarative UI framework for Android | XML Views (Apache 2.0) |
| Kotlin Coroutines | ≥1.8 | Apache 2.0 | ✅ Yes | None | Async processing for indexing and query pipelines | RxJava (Apache 2.0) |
| WorkManager | ≥2.9 | Apache 2.0 | ✅ Yes | None | Background PDF indexing that survives app kills | Foreground Service (more intrusive) |
| Hilt (Dagger) | ≥2.51 | Apache 2.0 | ✅ Yes | None | Dependency injection for Clean Architecture | Koin (Apache 2.0) |

### Models

| Model | Version | License | Commercial Use | Distribution Requirements | Reason | Alternatives |
|---|---|---|---|---|---|---|
| `BAAI/bge-small-en-v1.5` | 1.5.0 | MIT | ✅ Yes | None | SOTA retrieval accuracy at small size; benchmark winner on MTEB | E5-small-v2 (MIT) |
| `intfloat/e5-small-v2` | 2.0 | MIT | ✅ Yes | None | Strong retrieval model; requires query/passage prefixes | BGE-small (MIT) |
| `sentence-transformers/all-MiniLM-L6-v2` | latest | Apache 2.0 | ✅ Yes | None | General-purpose semantic similarity; baseline for benchmarking | BGE-small (MIT) |
| `deepset/minilm-uncased-squad2` | latest | Apache 2.0 | ✅ Yes | None | Compact extractive QA (~90 MB, INT8 ~45 MB); squad2-trained | roberta-base-squad2 (CC BY 4.0, 460 MB — too large) |
| `cross-encoder/ms-marco-MiniLM-L-6-v2` | latest | Apache 2.0 | ✅ Yes | None | Compact cross-encoder reranker for optional high-accuracy mode | ms-marco-MiniLM-L-4-v2 (smaller, slightly worse) |
| `en_core_web_sm` (spaCy) | ≥3.7 | MIT | ✅ Yes | None | Named entity recognition for Question Engine | en_core_web_md (MIT, larger) |

---

## 🔍 Transitive Dependency Audit

### pdfplumber transitive chain
- `pdfminer.six` (MIT) ✅
- `Wand` (MIT, optional, image handling) ✅
- `pypdfium2` (Apache 2.0, optional) ✅
- `pdfplumber` does NOT bundle MuPDF ✅

### hnswlib transitive chain
- Pure C++ with Python bindings via pybind11 (BSD) ✅
- No AGPL or GPL dependencies ✅

### ONNX Runtime transitive chain
- Microsoft ONNX Runtime (MIT) ✅
- Flatbuffers (Apache 2.0) ✅
- abseil-cpp (Apache 2.0) ✅
- Eigen (MPL2 — permissive for static/dynamic linking) ✅
- No AGPL or GPL dependencies ✅

### PDFium (Android) transitive chain
- FreeType (BSD/FTL — permissive) ✅
- ICU (Unicode License — permissive) ✅
- OpenJPEG (BSD 2-Clause) ✅
- zlib (zlib License — permissive) ✅
- libpng (PNG Reference Library License — permissive) ✅
- No AGPL or GPL dependencies ✅

---

## 📋 Pending Reviews

| Dependency | Reason for Review | Priority |
|---|---|---|
| `pikepdf` | Uses QPDF (Apache 2.0) underneath — verify no AGPL transitives | LOW |
| `tabula-py` | Uses Java Tabula library; verify Tabula's license (MIT) and JVM runtime | LOW |
| `easyocr` | Apache 2.0 but larger model size — evaluate if Tesseract insufficient | LOW |
| Tesseract4Android | Apache 2.0 — evaluate for Android OCR fallback if ML Kit has gaps | MEDIUM |

---

## 📌 Policy Notes

1. This file must be updated before introducing any new dependency.
2. A dependency's license is the license of its **least permissive component**, including all transitive dependencies and bundled native libraries.
3. LGPL dependencies require careful evaluation — dynamic linking is generally safer than static linking for commercial closed-source products.
4. Apache 2.0 requires inclusion of the NOTICE file in distributions.
5. BSD licenses typically require preservation of the copyright notice.
6. MIT licenses require preservation of the copyright notice.
