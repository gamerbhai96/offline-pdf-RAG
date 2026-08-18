# System Inventory & Execution Trace

## 1. Document Ingestion Trace (`DocumentPipeline.ingest`)
The ingestion pipeline is designed to be fail-safe at the page level.

1. **Parser Phase**: `DocumentParser.parse` (reads PDF, applies OCR if needed, chunks by page).
2. **Structuring Phase**: `StructureAnalyzer.analyze` (identifies layout elements, headers, tables).
3. **Chunking Phase**: `Chunker.chunk` (applies token-based chunking with overlap).
4. **Embedding Phase**: `EmbeddingEngine.embed_passages` (generates dense embeddings in batches).
5. **Indexing Phase**:
   - `LexicalIndexer.build`: BM25 index rebuilt with all document chunks.
   - `VectorIndex.add`: HNSW/BruteForce dense vector index populated.

## 2. Query Execution Trace (`DocumentPipeline.ask`)
When a query is issued, it traverses the following strictly ordered phases:

1. **Question Analysis**: `QuestionAnalyzer.analyze` (Identifies entities, targets, intents, format hints).
2. **Context Resolution**: `ConversationResolver.resolve` (Resolves references using `ConversationContext`).
3. **Hybrid Retrieval**: `HybridRetriever.retrieve` (Fetches top K from Dense + Sparse, merges using Reciprocal Rank Fusion - RRF).
4. **Preview & Routing**: `RetrievalPreview` gives a quick snapshot of retrieval (e.g., table presence). `QuestionRouter.route` selects the answer strategy (e.g., FACT_QA, SUMMARY).
5. **Deterministic Ranking**: `DeterministicRanker.rerank` (Applies structural/lexical bonuses to RRF scores for top N candidates).
6. **Evidence Validation (Gate 1)**: `EvidenceValidator.validate` (Filters candidates falling below a hard score threshold).
7. **Answer Building**: `AnswerBuilder.build` (Synthesizes the answer based on validated evidence and routing intent).
8. **Answer Validation (Gate 2)**: `AnswerValidator.validate` (Ensures the drafted answer doesn't hallucinate beyond the validated evidence).
9. **Confidence Scoring**: `ConfidenceEngine.score` (Calculates final confidence level based on retrieval, evidence, and answerability scores).
10. **Presentation Formatting**: `SafePresentationEngine.format` (Finalizes text layout, lists, tables).
11. **Citation Generation**: `CitationEngine.generate` (Appends source tracking references to the output).

*All states and metric data can be extracted through the `DebugTrace` object exposed by the pipeline without modifying production files.*
