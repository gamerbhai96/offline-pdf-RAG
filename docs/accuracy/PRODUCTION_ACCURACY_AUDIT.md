# PRODUCTION ACCURACY AUDIT
*Final Consolidation Report*

## Baseline Scorecard
- **Retrieval Accuracy (Top 5):** 47.1%
- **Semantic Section Confusion:** 52.9%
- **Intent Recognition Accuracy:** 5.9%

## Primary Failure Modes Identified

1. **Section Confusion (Semantic Retrieval Gap)**: The system frequently retrieves a structurally dense or keyword-dense chunk over the actual defining section. E.g., for "What is Hive?", it brings back "Partitioning and Bucketing - Hive supports partitioning..." instead of the Introduction section.
2. **Intent Analysis Mismatch**: The query analyzer frequently misclassifies the `expected_intent`.
3. **Answer Hallucination on OOD**: When asked Out-Of-Domain questions (e.g., deploying ML models with Hive), the pipeline still extracts random chunks (like "Querying Hive allows users...") and formats it as a FACT_QA instead of rejecting it as NO_ANSWER.
4. **Header Extraction Issues**: The pipeline's structural chunker often merges bullet points into single chunks that dominate TF-IDF/BM25 scores over actual conceptual headers.

## Prioritized Repair List (DO NOT IMPLEMENT YET)
1. **Fix QuestionAnalyzer**: Improve intent parsing to accurately classify SUMMARY vs FACT_QA.
2. **Fix Chunking/Structure Analyzer**: The parser needs to correctly tag headings and prevent bullet-point aggregations from masking main concepts.
3. **Fix DeterministicRanker**: Apply a stronger penalty to chunks that lack the primary concept term in their recognized `heading`.
4. **Fix Confidence Engine (Gate 2 / OOD)**: Tighten the answerability score to reject Out-Of-Domain queries that coincidentally contain the word "Hive".
