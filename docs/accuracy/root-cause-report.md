# Root Cause Analysis & Validation Findings

## 1. Intent Taxonomy Validation
The benchmark uses `LIST_QA`, `FACT_QA`, `SUMMARY`, `PROCEDURE`.
The router outputs `LIST`, `FACT_QA`, `SUMMARY`, `TABLE`.
- **Mapping:** `LIST_QA` -> `LIST`, `FACT_QA` -> `FACT_QA`, `SUMMARY` -> `SUMMARY`, `TABLE` -> `TABLE`, `PROCEDURE` -> `FACT_QA` (fallback).
- **Corrected Intent Metrics:** With this mapping, Intent Recognition Accuracy is 88.2% (15/17 match). The analyzer is actually quite accurate under the corrected mapping. The major failure is that it routes `PROCEDURE` to `FACT_QA` fallback instead of a dedicated path, but standard intents match.

## 2. Golden Evidence Validation
Verified that 14 out of 17 expected sections DO NOT match exact chunk headings. The pipeline structural chunker often modifies or merges headings.
- **Correction:** `EXPECTED_SECTION_VALID: NO` for most. Expected sections were mapped dynamically via subset text matching.

## 3 & 4 & 5. Deep Retrieval/Rank Analysis (BM25 vs Dense vs Hybrid vs Final)
We traced the exact ranks for failing questions. 
- **Pattern A (Section Confusion):** For "What is Hive?" (Q_DEF_1) and "What is partitioning?" (Q_DEF_4), the correct defining chunks ranked **BM25=22** and **Dense=2**. The dense index easily found it, but BM25 completely buried it in favor of keyword-stuffed chunks. Because BM25 buried it (rank > 20), RRF dropped it to rank 16. It never even reached the top 10 for the `DeterministicRanker` to look at. **Failing Stage:** Hybrid Fusion (RRF cutoff due to BM25 dominance by noisy chunks).
- **Pattern B (Lost in Deterministic Ranker):** For "hive layer" (Q_FACT_3), BM25=25, Dense=9. RRF saved it at rank 18. But `DeterministicRanker` only looks at `top_k=10`, so it was chopped off before it could be promoted. **Failing Stage:** Deterministic Ranker cutoff threshold.

## 6. Chunk Quality Findings
- **Analysis:** Incorrect chunks that win BM25 frequently have massive token counts (e.g., 512 tokens) and merge multiple bulleted sections. For "What are the features of Hive?", the top incorrect chunk merged 3 different sections (2048 characters). This high keyword frequency dominates BM25 scoring over the short, focused definition chunks (which only have ~50 tokens). **Conclusion:** The `Chunker` is creating overly broad mega-chunks.

## 7. Out-Of-Domain Hallucination Root Cause (Q_NOANS_2)
- **Trace for:** "How to deploy a machine learning model using Hive?"
- **Finding:** The query routed to `FACT_QA`. `HybridRetriever` grabbed a random chunk (rank 34 Dense) because it contained the word "model" and "Hive". 
- **The Core Failure:** `EvidenceValidator` (Gate 1) passed it with an artificially high score of `0.814`. The `AnswerValidator` (Gate 2) just checks if the drafted answer is grounded in the (flawed) evidence, so it passes. `ConfidenceEngine` saw high evidence scores and gave `0.962` answerability. 
- **Root Cause:** Gate 1 has no strict semantic overlap check for OOD concepts. It trusts the dense embedding score blindly if lexical validation doesn't explicitly veto it.

---

## 8. UPDATED ROOT CAUSE PRIORITY (REPAIR LIST)

### P0 Critical: Hybrid Retrieval Imbalance (BM25 Megachunks)
- **Failure:** Correct chunks are destroyed by RRF because BM25 ranks them >20 due to competing mega-chunks.
- **Failing Component:** `Chunker.chunk()` and `HybridRetriever.retrieve()`
- **Minimal Repair:** Limit `Chunker` to split aggressively on headings instead of merging them. Increase `HybridRetriever` `alpha` to heavily favor Dense (e.g., 0.8) for short factual queries, or apply length-normalization to BM25.

### P1 Major: Out-Of-Domain Validation Bypass
- **Failure:** Gate 1 passes irrelevant chunks for OOD queries with scores > 0.8.
- **Failing Component:** `EvidenceValidator.validate()`
- **Minimal Repair:** Enforce a hard keyword/entity overlap check in Gate 1. If the query asks for "machine learning" and those terms do not exist in the chunk text, force `status=FAIL` with reason `missing_core_entity`.

### P2 Moderate: Deterministic Ranker Cutoff
- **Failure:** Correct chunks sitting at rank 11-20 after RRF are ignored.
- **Failing Component:** `DeterministicRanker.rerank()`
- **Minimal Repair:** Increase the `top_k` passed from `HybridRetriever` to `DeterministicRanker` from 10 to 20 to give it a wider net before structural reranking.

### P3 Optimization: Intent Taxonomy for Procedures
- **Failure:** "How to" questions default to `FACT_QA`.
- **Failing Component:** `QuestionRouter.route()`
- **Minimal Repair:** Add `PROCEDURE` / `STEPS` intent explicitly to the router to force a `LIST` format extraction.
