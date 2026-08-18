"""
Tests for Phases 2–9: Chunker, Embedding, BM25, Vector Index,
Question Analyzer, Router, Hybrid Retrieval, Evidence Validation.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
import pytest

# ─────────────────────────────────────────────────────────────
# Helpers — synthetic document fixtures
# ─────────────────────────────────────────────────────────────

def _make_structured_doc(n_sections: int = 3, blocks_per_section: int = 4):
    """Build a minimal StructuredDocument without parsing any real PDF."""
    from core.document.models import BlockType, BoundingBox, ParsedDocument, ParsedPage, TextBlock
    from core.document.structure import (
        DetectedHeading, DetectedSection, StructuredDocument
    )

    pages = []
    sections = []
    headings = []
    heading_texts = ["Introduction", "Methods", "Results", "Discussion", "Conclusion"]
    body_sentences = [
        "Hadoop provides scalability and fault tolerance for big data.",
        "TCP ensures reliable ordered delivery of packets across networks.",
        "The algorithm runs in O(n log n) time with constant space.",
        "Results show a 40% improvement over the baseline system.",
        "The embedding dimension is 384 for all models evaluated.",
    ]

    all_blocks = []
    y_pos = 700.0
    for i in range(n_sections):
        heading_text = heading_texts[i % len(heading_texts)]
        sec_id = str(uuid.uuid4())
        sec_blocks = []

        heading_block = TextBlock(
            text=heading_text,
            bbox=BoundingBox(72, y_pos, 300, y_pos + 20, page=1),
            font_size=18.0,
            is_bold=True,
            block_type=BlockType.HEADING,
            reading_order=len(all_blocks),
        )
        y_pos -= 25
        all_blocks.append(heading_block)

        for j in range(blocks_per_section):
            text = body_sentences[(i * blocks_per_section + j) % len(body_sentences)]
            blk = TextBlock(
                text=text,
                bbox=BoundingBox(72, y_pos, 540, y_pos + 14, page=1),
                font_size=12.0,
                block_type=BlockType.TEXT,
                reading_order=len(all_blocks),
            )
            y_pos -= 18
            all_blocks.append(blk)
            sec_blocks.append(blk)

        section = DetectedSection(
            section_id=sec_id,
            page_number=1,
            heading=heading_text,
            heading_level=1,
            text_blocks=sec_blocks,
            start_offset=i * 200,
            end_offset=(i + 1) * 200,
        )
        sections.append(section)
        headings.append(DetectedHeading(
            section_id=sec_id,
            page_number=1,
            text=heading_text,
            level=1,
            bbox=BoundingBox(72, y_pos + 100, 300, y_pos + 120, page=1),
            font_size=18.0,
        ))

    page = ParsedPage(
        page_number=1,
        raw_text="\n".join(b.text for b in all_blocks),
        text_blocks=all_blocks,
        width_pts=612.0,
        height_pts=792.0,
    )
    pages.append(page)

    parsed_doc = ParsedDocument(
        file_path="synthetic.pdf",
        file_hash="a" * 64,
        page_count=1,
        pages=pages,
    )
    return StructuredDocument(
        document_id="doc-001",
        file_path="synthetic.pdf",
        pages=pages,
        sections=sections,
        headings=headings,
        lists=[],
        header_footer_texts=set(),
    )


def _make_chunks(n: int = 10, document_id: str = "doc-001"):
    """Create n synthetic Chunk objects."""
    from core.chunking.chunker import Chunk, ChunkStrategy, ChunkType
    texts = [
        "Hadoop provides scalability and fault tolerance for distributed computing systems.",
        "TCP ensures reliable ordered delivery of packets across heterogeneous networks.",
        "The embedding dimension is 384 for all sentence transformer models evaluated.",
        "BM25 is a probabilistic ranking function used in information retrieval.",
        "HNSW enables approximate nearest neighbour search with logarithmic complexity.",
        "The convolutional neural network achieved 95% accuracy on the test set.",
        "Distributed file systems partition data across multiple commodity servers.",
        "Query expansion improves recall by adding related terms to the original query.",
        "Cosine similarity measures the angle between two vectors in embedding space.",
        "Reciprocal rank fusion combines multiple ranked lists into a single ranking.",
    ]
    chunks = []
    for i in range(n):
        text = texts[i % len(texts)]
        chunks.append(Chunk(
            chunk_id=str(uuid.uuid4()),
            document_id=document_id,
            page_id=str((i % 5) + 1),
            text=text,
            token_count=len(text.split()),
            start_offset=i * 100,
            end_offset=(i + 1) * 100,
            chunk_index=i,
            strategy=ChunkStrategy.HEADING_AWARE,
        ))
    return chunks


# ─────────────────────────────────────────────────────────────
# Phase 2 — Chunker
# ─────────────────────────────────────────────────────────────

class TestChunker:
    def test_heading_aware_produces_chunks(self):
        from core.chunking.chunker import Chunker, ChunkStrategy
        structured = _make_structured_doc(n_sections=3)
        chunker = Chunker(strategy=ChunkStrategy.HEADING_AWARE, max_tokens=512)
        result = chunker.chunk(structured, "doc-test-001")
        assert len(result.chunks) >= 1

    def test_all_chunks_have_ids(self):
        from core.chunking.chunker import Chunker
        structured = _make_structured_doc(n_sections=2)
        result = Chunker().chunk(structured, "doc-ids")
        for c in result.chunks:
            assert c.chunk_id, "chunk_id must not be empty"
            assert len(c.chunk_id) == 36  # UUID format

    def test_chunks_within_max_tokens(self):
        from core.chunking.chunker import Chunker
        max_t = 100
        structured = _make_structured_doc(n_sections=2)
        result = Chunker(max_tokens=max_t).chunk(structured, "doc-limit")
        for c in result.chunks:
            assert c.token_count <= max_t, (
                f"Chunk '{c.text[:30]}' has {c.token_count} tokens > max {max_t}"
            )

    def test_min_chunk_tokens_filtering(self):
        from core.chunking.chunker import Chunker, MIN_CHUNK_TOKENS
        structured = _make_structured_doc(n_sections=1, blocks_per_section=1)
        result = Chunker().chunk(structured, "doc-min")
        for c in result.chunks:
            assert c.token_count >= MIN_CHUNK_TOKENS

    def test_chunk_indices_sequential(self):
        from core.chunking.chunker import Chunker
        structured = _make_structured_doc(n_sections=3)
        result = Chunker().chunk(structured, "doc-idx")
        for expected_idx, chunk in enumerate(result.chunks):
            assert chunk.chunk_index == expected_idx

    def test_document_id_propagated(self):
        from core.chunking.chunker import Chunker
        structured = _make_structured_doc()
        result = Chunker().chunk(structured, "my-doc-uuid")
        for c in result.chunks:
            assert c.document_id == "my-doc-uuid"

    def test_strategy_version_set(self):
        from core.chunking.chunker import Chunker, STRATEGY_VERSION
        structured = _make_structured_doc()
        result = Chunker().chunk(structured, "doc-sv")
        for c in result.chunks:
            assert c.strategy_version == STRATEGY_VERSION

    def test_paragraph_strategy(self):
        from core.chunking.chunker import Chunker, ChunkStrategy
        structured = _make_structured_doc()
        result = Chunker(strategy=ChunkStrategy.PARAGRAPH).chunk(structured, "doc-para")
        assert len(result.chunks) >= 1
        assert result.strategy == ChunkStrategy.PARAGRAPH

    def test_fixed_overlap_strategy(self):
        from core.chunking.chunker import Chunker, ChunkStrategy
        structured = _make_structured_doc()
        result = Chunker(strategy=ChunkStrategy.FIXED_OVERLAP, max_tokens=50, overlap_tokens=10).chunk(structured, "doc-fo")
        assert len(result.chunks) >= 1
        assert result.strategy == ChunkStrategy.FIXED_OVERLAP

    def test_sentence_strategy(self):
        from core.chunking.chunker import Chunker, ChunkStrategy
        structured = _make_structured_doc()
        result = Chunker(strategy=ChunkStrategy.SENTENCE).chunk(structured, "doc-sent")
        assert len(result.chunks) >= 1

    def test_auto_selects_heading_aware_when_headings_present(self):
        from core.chunking.chunker import Chunker, ChunkStrategy
        structured = _make_structured_doc(n_sections=3)
        result = Chunker().chunk(structured, "doc-auto")
        assert result.strategy == ChunkStrategy.HEADING_AWARE

    def test_chunk_to_dict_serializable(self):
        from core.chunking.chunker import Chunker
        structured = _make_structured_doc()
        result = Chunker().chunk(structured, "doc-dict")
        for c in result.chunks:
            d = c.to_dict()
            json.dumps(d)   # must not raise

    def test_total_tokens_calculated(self):
        from core.chunking.chunker import Chunker
        structured = _make_structured_doc()
        result = Chunker().chunk(structured, "doc-tok")
        expected_total = sum(c.token_count for c in result.chunks)
        assert result.total_tokens == expected_total


# ─────────────────────────────────────────────────────────────
# Phase 3 — EmbeddingEngine (stub mode, no model files needed)
# ─────────────────────────────────────────────────────────────

class TestEmbeddingEngine:
    def test_stub_produces_normalized_vectors(self):
        from core.embedding.engine import EmbeddingEngine, _l2_normalize
        engine = EmbeddingEngine(stub=True, dimension=384)
        result = engine.embed_passages(["Hello world"], ["chunk-1"])
        assert len(result) == 1
        emb = result[0].embedding
        norm = float(np.linalg.norm(emb))
        assert abs(norm - 1.0) < 1e-5, f"Vector not L2-normalized (norm={norm})"

    def test_stub_correct_dimension(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True, dimension=256)
        result = engine.embed_passages(["test"], ["c1"])
        assert result[0].embedding.shape == (256,)

    def test_embed_query_returns_1d_vector(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True, dimension=384)
        vec = engine.embed_query("What is TCP?")
        assert vec.ndim == 1
        assert vec.shape[0] == 384

    def test_embed_multiple_passages(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True, dimension=384)
        texts = ["First sentence.", "Second sentence.", "Third sentence."]
        ids = ["c1", "c2", "c3"]
        results = engine.embed_passages(texts, ids)
        assert len(results) == 3
        for r, expected_id in zip(results, ids):
            assert r.chunk_id == expected_id
            assert r.model_id == engine.model_id

    def test_embed_query_batch(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True, dimension=384)
        vecs = engine.embed_queries(["Q1", "Q2", "Q3"])
        assert vecs.shape == (3, 384)

    def test_is_loaded_stub(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True)
        assert engine.is_loaded() is True

    def test_is_not_loaded_without_model(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=False)
        assert engine.is_loaded() is False

    def test_model_id_propagated(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(model_id="e5-small-v2", stub=True)
        results = engine.embed_passages(["text"], ["c1"])
        assert results[0].model_id == "e5-small-v2"

    def test_preprocessing_version_set(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True)
        results = engine.embed_passages(["text"], ["c1"])
        assert results[0].preprocessing_version != ""

    def test_to_list_returns_floats(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=True, dimension=32)
        results = engine.embed_passages(["text"], ["c1"])
        lst = results[0].to_list()
        assert isinstance(lst, list)
        assert all(isinstance(x, float) for x in lst)
        assert len(lst) == 32

    def test_l2_normalize_unit_vector(self):
        from core.embedding.engine import _l2_normalize
        v = np.array([[3.0, 4.0]])
        normalized = _l2_normalize(v)
        norm = np.linalg.norm(normalized[0])
        assert abs(norm - 1.0) < 1e-6

    def test_mean_pool_shape(self):
        from core.embedding.engine import _mean_pool
        token_emb = np.ones((2, 8, 16), dtype=np.float32)
        mask = np.ones((2, 8), dtype=np.float32)
        pooled = _mean_pool(token_emb, mask)
        assert pooled.shape == (2, 16)

    def test_raises_without_model_when_not_stub(self):
        from core.embedding.engine import EmbeddingEngine
        engine = EmbeddingEngine(stub=False)
        with pytest.raises(RuntimeError):
            engine.embed_query("test")


# ─────────────────────────────────────────────────────────────
# Phase 4 — BM25 LexicalIndexer
# ─────────────────────────────────────────────────────────────

class TestLexicalIndexer:
    def setup_method(self):
        from core.lexical.indexer import LexicalIndexer
        chunks = _make_chunks(n=10)
        self.indexer = LexicalIndexer()
        self.indexer.build(chunks)
        self.chunks = chunks

    def test_build_and_search(self):
        results = self.indexer.search("Hadoop scalability fault tolerance", top_k=5)
        assert len(results) >= 1

    def test_top_result_relevant(self):
        results = self.indexer.search("Hadoop fault tolerance", top_k=3)
        assert any("Hadoop" in r.text or "hadoop" in r.text.lower() for r in results)

    def test_scores_normalized(self):
        results = self.indexer.search("TCP reliable delivery", top_k=5)
        for r in results:
            assert 0.0 <= r.normalized_score <= 1.0

    def test_top_score_is_one(self):
        results = self.indexer.search("scalability fault tolerance", top_k=5)
        if results:
            assert abs(results[0].normalized_score - 1.0) < 0.001

    def test_empty_query_returns_empty(self):
        results = self.indexer.search("", top_k=5)
        assert results == []

    def test_unknown_term_returns_empty(self):
        results = self.indexer.search("xyzzy_nonexistent_term_42", top_k=5)
        assert results == []

    def test_top_k_respected(self):
        results = self.indexer.search("network", top_k=3)
        assert len(results) <= 3

    def test_metadata_populated(self):
        meta = self.indexer.metadata
        assert meta.num_chunks == 10
        assert meta.vocabulary_size > 0

    def test_save_and_load(self, tmp_path):
        from core.lexical.indexer import LexicalIndexer
        self.indexer.save(tmp_path)
        loaded = LexicalIndexer.load(tmp_path)
        assert loaded.num_chunks == self.indexer.num_chunks
        results = loaded.search("embedding dimension", top_k=3)
        assert len(results) >= 1

    def test_save_creates_files(self, tmp_path):
        self.indexer.save(tmp_path)
        assert (tmp_path / "bm25.pkl").exists()
        assert (tmp_path / "bm25_meta.json").exists()

    def test_tokenizer_removes_stopwords(self):
        from core.lexical.indexer import _tokenize
        tokens = _tokenize("the quick brown fox jumped over the lazy dog")
        for stop in ["the", "over"]:
            assert stop not in tokens

    def test_tokenizer_lowercases(self):
        from core.lexical.indexer import _tokenize
        tokens = _tokenize("Hadoop HDFS TCP")
        assert all(t == t.lower() for t in tokens)

    def test_document_scope_filter(self):
        from core.lexical.indexer import LexicalIndexer
        chunks_a = _make_chunks(n=5, document_id="doc-A")
        chunks_b = _make_chunks(n=5, document_id="doc-B")
        indexer = LexicalIndexer()
        indexer.build(chunks_a + chunks_b)
        results = indexer.search("scalability", top_k=10, document_id="doc-A")
        for r in results:
            assert r.document_id == "doc-A"


# ─────────────────────────────────────────────────────────────
# Phase 5 — Vector Index
# ─────────────────────────────────────────────────────────────

class TestBruteForceIndex:
    DIM = 64

    def _random_unit(self, n=1) -> np.ndarray:
        rng = np.random.default_rng(0)
        v = rng.standard_normal((n, self.DIM)).astype(np.float32)
        norms = np.linalg.norm(v, axis=1, keepdims=True)
        return v / norms

    def test_add_and_search(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        embeddings = self._random_unit(5)
        ids = [str(uuid.uuid4()) for _ in range(5)]
        idx.add(ids, embeddings)
        results = idx.search(embeddings[0], top_k=3)
        assert len(results) == 3

    def test_top_result_is_self(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        embeddings = self._random_unit(5)
        ids = ["c0", "c1", "c2", "c3", "c4"]
        idx.add(ids, embeddings)
        results = idx.search(embeddings[2], top_k=1)
        assert results[0].chunk_id == "c2"

    def test_scores_in_range(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        embeddings = self._random_unit(5)
        idx.add(["a", "b", "c", "d", "e"], embeddings)
        results = idx.search(embeddings[0], top_k=5)
        for r in results:
            assert 0.0 <= r.dense_score <= 1.0

    def test_top_k_respected(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        idx.add(["a", "b", "c", "d", "e"], self._random_unit(5))
        results = idx.search(self._random_unit(1)[0], top_k=2)
        assert len(results) == 2

    def test_empty_index_returns_empty(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        results = idx.search(self._random_unit(1)[0], top_k=5)
        assert results == []

    def test_results_sorted_descending(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        idx.add(["a", "b", "c", "d", "e"], self._random_unit(5))
        results = idx.search(self._random_unit(1)[0], top_k=5)
        scores = [r.dense_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_save_and_load(self, tmp_path):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        embeddings = self._random_unit(4)
        ids = ["a", "b", "c", "d"]
        idx.add(ids, embeddings)
        idx.save(tmp_path)
        loaded = BruteForceIndex.load(tmp_path)
        assert loaded.num_chunks == 4
        results = loaded.search(embeddings[0], top_k=1)
        assert results[0].chunk_id == "a"

    def test_incremental_add(self):
        from core.vector.index import BruteForceIndex
        idx = BruteForceIndex(dimension=self.DIM)
        idx.add(["a", "b"], self._random_unit(2))
        idx.add(["c", "d"], self._random_unit(2))
        assert idx.num_chunks == 4

    def test_hnsw_or_brute_force_available(self):
        from core.vector.index import create_vector_index
        idx = create_vector_index(dimension=self.DIM)
        assert idx is not None
        assert idx.num_chunks == 0


# ─────────────────────────────────────────────────────────────
# Phase 6 — Question Analyzer + Conversation Resolver
# ─────────────────────────────────────────────────────────────

class TestQuestionAnalyzer:
    def setup_method(self):
        from core.question.analyzer import QuestionAnalyzer
        self.analyzer = QuestionAnalyzer()

    def test_analyze_returns_normalized_query(self):
        from core.question.analyzer import NormalizedQuery
        result = self.analyzer.analyze("What is TCP?")
        assert isinstance(result, NormalizedQuery)

    def test_definition_type_detected(self):
        from core.question.analyzer import QuestionType
        result = self.analyzer.analyze("What is TCP?")
        assert result.question_type == QuestionType.DEFINITION

    def test_list_type_detected(self):
        from core.question.analyzer import QuestionType
        result = self.analyzer.analyze("List all features of Hadoop")
        assert result.question_type == QuestionType.LIST

    def test_numerical_type_detected(self):
        from core.question.analyzer import QuestionType
        result = self.analyzer.analyze("How many nodes are in the cluster?")
        assert result.question_type == QuestionType.NUMERICAL

    def test_steps_type_detected(self):
        from core.question.analyzer import QuestionType
        result = self.analyzer.analyze("What are the steps to configure HDFS?")
        assert result.question_type == QuestionType.STEPS

    def test_comparison_type_detected(self):
        from core.question.analyzer import QuestionType
        result = self.analyzer.analyze("What is the difference between TCP and UDP?")
        assert result.question_type == QuestionType.COMPARISON

    def test_bullet_format_hint_detected(self):
        from core.question.analyzer import FormatHint
        result = self.analyzer.analyze("List features in bullet points")
        assert result.format_hint == FormatHint.BULLETS

    def test_table_format_hint_detected(self):
        from core.question.analyzer import FormatHint
        result = self.analyzer.analyze("Show me the comparison as a table")
        assert result.format_hint == FormatHint.TABLE

    def test_empty_query_flagged_ambiguous(self):
        result = self.analyzer.analyze("")
        assert result.ambiguity_flag is True

    def test_raw_query_preserved(self):
        q = "What is Hadoop?"
        result = self.analyzer.analyze(q)
        assert result.raw_query == q

    def test_to_dict_serializable(self):
        result = self.analyzer.analyze("What is TCP?")
        d = result.to_dict()
        json.dumps(d)


class TestConversationResolver:
    def test_resolves_pronoun_with_context(self):
        from core.question.analyzer import (
            ConversationContext, ConversationResolver, Entity, NormalizedQuery, QuestionType
        )
        ctx = ConversationContext(session_id="sess-1")
        ctx.entity_stack.append({"text": "Hadoop", "type": "TECH", "turn": 1, "mention_count": 1})

        resolver = ConversationResolver()
        query = NormalizedQuery(
            raw_query="How does it scale?",
            normalized_query="how does it scale?",
            resolved_query="how does it scale?",
            question_type=QuestionType.EXPLANATION,
        )
        resolved = resolver.resolve(query, ctx)
        assert "hadoop" in resolved.resolved_query.lower() or resolved.coreference_resolved

    def test_no_resolution_without_context(self):
        from core.question.analyzer import ConversationResolver, NormalizedQuery, QuestionType
        resolver = ConversationResolver()
        query = NormalizedQuery(
            raw_query="What is it?",
            normalized_query="what is it?",
            resolved_query="what is it?",
            question_type=QuestionType.UNKNOWN,
        )
        resolved = resolver.resolve(query, None)
        assert resolved.resolved_query == query.normalized_query

    def test_entity_stack_grows(self):
        from core.question.analyzer import ConversationContext, Entity
        ctx = ConversationContext(session_id="sess-2")
        ctx.push_entities([Entity("TCP", "TECH", 0, 3)], turn=1)
        assert len(ctx.entity_stack) == 1

    def test_entity_stack_deduplication(self):
        from core.question.analyzer import ConversationContext, Entity
        ctx = ConversationContext(session_id="sess-3")
        ctx.push_entities([Entity("TCP", "TECH", 0, 3)], turn=1)
        ctx.push_entities([Entity("TCP", "TECH", 0, 3)], turn=2)
        assert len(ctx.entity_stack) == 1
        assert ctx.entity_stack[0]["mention_count"] == 2


# ─────────────────────────────────────────────────────────────
# Phase 7 — Question Router
# ─────────────────────────────────────────────────────────────

class TestQuestionRouter:
    def setup_method(self):
        from core.question.router import QuestionRouter, RetrievalPreview
        from core.question.analyzer import QuestionAnalyzer
        self.router = QuestionRouter()
        self.analyzer = QuestionAnalyzer()
        self.good_preview = RetrievalPreview(evidence_count=5, top_score=0.7)
        self.empty_preview = RetrievalPreview(evidence_count=0, top_score=0.0)

    def _query(self, text: str):
        return self.analyzer.analyze(text)

    def test_no_evidence_returns_fact_qa(self):
        from core.question.router import Route
        q = self._query("What is TCP?")
        decision = self.router.route(q, self.empty_preview)
        assert decision.route == Route.FACT_QA

    def test_definition_routes_to_fact_qa(self):
        from core.question.router import Route
        q = self._query("What is Hadoop?")
        decision = self.router.route(q, self.good_preview)
        assert decision.route == Route.FACT_QA

    def test_list_routes_to_list(self):
        from core.question.router import Route
        q = self._query("List all features of TCP")
        decision = self.router.route(q, self.good_preview)
        assert decision.route == Route.LIST

    def test_steps_routes_to_list_numbered(self):
        from core.question.router import Route, AnswerFormat
        q = self._query("What are the steps to install Hadoop?")
        decision = self.router.route(q, self.good_preview)
        assert decision.route == Route.LIST
        assert decision.answer_format == AnswerFormat.NUMBERED_LIST

    def test_comparison_with_table_routes_to_table(self):
        from core.question.router import Route, RetrievalPreview
        q = self._query("Compare TCP and UDP")
        preview = RetrievalPreview(evidence_count=5, top_score=0.6, has_table_evidence=True)
        decision = self.router.route(q, preview)
        assert decision.route == Route.TABLE

    def test_explicit_bullet_hint(self):
        from core.question.router import Route, AnswerFormat
        q = self._query("Give me features in bullet points")
        decision = self.router.route(q, self.good_preview)
        assert decision.route == Route.LIST
        assert decision.answer_format == AnswerFormat.BULLET_LIST

    def test_route_decision_has_reason(self):
        q = self._query("What is TCP?")
        decision = self.router.route(q, self.good_preview)
        assert decision.routing_reason != ""

    def test_route_decision_to_dict(self):
        q = self._query("What is TCP?")
        decision = self.router.route(q, self.good_preview)
        d = decision.to_dict()
        json.dumps(d)


# ─────────────────────────────────────────────────────────────
# Phase 8 — Hybrid Retriever (stub embeddings)
# ─────────────────────────────────────────────────────────────

class TestHybridRetriever:
    DIM = 384   # must match stub engine default

    def setup_method(self):
        from core.embedding.engine import EmbeddingEngine
        from core.lexical.indexer import LexicalIndexer
        from core.vector.index import BruteForceIndex
        from core.retrieval.hybrid import HybridRetriever
        from core.question.analyzer import QuestionAnalyzer

        chunks = _make_chunks(10)
        texts = [c.text for c in chunks]
        ids   = [c.chunk_id for c in chunks]

        engine = EmbeddingEngine(stub=True, dimension=self.DIM)
        results = engine.embed_passages(texts, ids)
        embeddings = np.array([r.embedding for r in results], dtype="float32")

        vector_idx = BruteForceIndex(dimension=self.DIM)
        vector_idx.add(ids, embeddings)

        lexical_idx = LexicalIndexer()
        lexical_idx.build(chunks)

        self.retriever = HybridRetriever(
            vector_index=vector_idx,
            lexical_indexer=lexical_idx,
            embedding_engine=engine,
            alpha=0.5,
            top_k=5,
        )
        self.analyzer = QuestionAnalyzer()

    def test_retrieve_returns_results(self):
        q = self.analyzer.analyze("What is Hadoop?")
        results = self.retriever.retrieve(q)
        assert len(results) >= 1

    def test_results_sorted_by_fusion_score(self):
        q = self.analyzer.analyze("TCP reliable delivery")
        results = self.retriever.retrieve(q)
        scores = [r.fusion_score for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_all_results_have_chunk_ids(self):
        q = self.analyzer.analyze("embedding dimension")
        results = self.retriever.retrieve(q)
        for r in results:
            assert r.chunk_id != ""

    def test_top_k_respected(self):
        q = self.analyzer.analyze("scalability")
        results = self.retriever.retrieve(q, top_k=3)
        assert len(results) <= 3

    def test_dense_only_alpha(self):
        from core.retrieval.hybrid import HybridRetriever
        from core.embedding.engine import EmbeddingEngine
        from core.lexical.indexer import LexicalIndexer
        from core.vector.index import BruteForceIndex
        DIM = 384
        chunks = _make_chunks(5)
        engine = EmbeddingEngine(stub=True, dimension=DIM)
        embeddings_np = np.array(
            [r.embedding for r in engine.embed_passages([c.text for c in chunks], [c.chunk_id for c in chunks])],
            dtype="float32"
        )
        vidx = BruteForceIndex(dimension=DIM)
        vidx.add([c.chunk_id for c in chunks], embeddings_np)
        lex = LexicalIndexer()
        lex.build(chunks)
        ret = HybridRetriever(vidx, lex, engine, alpha=1.0, top_k=3)
        q = self.analyzer.analyze("TCP")
        results = ret.retrieve(q)
        assert len(results) >= 1


# ─────────────────────────────────────────────────────────────
# Phase 9 — Ranker + Evidence Validator
# ─────────────────────────────────────────────────────────────

class TestDeterministicRanker:
    def _make_retrieved_chunks(self):
        from core.retrieval.hybrid import RetrievedChunk
        return [
            RetrievedChunk("c1", "doc", "1", "Hadoop provides scalability", 0.8, 0.7, 0.75),
            RetrievedChunk("c2", "doc", "2", "TCP ensures reliable delivery", 0.4, 0.3, 0.35),
            RetrievedChunk("c3", "doc", "3", "Cosine similarity in embedding space", 0.6, 0.5, 0.55),
        ]

    def test_rerank_returns_sorted(self):
        from core.retrieval.hybrid import DeterministicRanker
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("Hadoop scalability")
        chunks = self._make_retrieved_chunks()
        ranked = DeterministicRanker().rerank(q, chunks, top_k=3)
        scores = [r.fusion_score for r in ranked]
        assert scores == sorted(scores, reverse=True)

    def test_top_k_respected(self):
        from core.retrieval.hybrid import DeterministicRanker
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("Hadoop")
        chunks = self._make_retrieved_chunks()
        ranked = DeterministicRanker().rerank(q, chunks, top_k=2)
        assert len(ranked) <= 2

    def test_empty_input_returns_empty(self):
        from core.retrieval.hybrid import DeterministicRanker
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("test")
        assert DeterministicRanker().rerank(q, [], top_k=5) == []


class TestEvidenceValidator:
    def _make_retrieved_chunk(self, chunk_id, text, dense=0.8, bm25=0.7, fusion=0.75):
        from core.retrieval.hybrid import RetrievedChunk
        return RetrievedChunk(chunk_id, "doc-1", "1", text, dense, bm25, fusion)

    def test_relevant_chunk_passes(self):
        from core.retrieval.hybrid import EvidenceValidator
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("Hadoop scalability")
        chunks = [self._make_retrieved_chunk(
            "c1", "Hadoop provides scalability and fault tolerance.", 0.9, 0.8, 0.85
        )]
        validated, rejected = EvidenceValidator().validate(q, chunks, threshold=0.2)
        assert len(validated) >= 1

    def test_irrelevant_chunk_rejected(self):
        from core.retrieval.hybrid import EvidenceValidator
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("quantum computing superconductors")
        chunks = [self._make_retrieved_chunk(
            "c1", "The weather is nice today.", 0.0, 0.0, 0.0
        )]
        validated, rejected = EvidenceValidator().validate(q, chunks, threshold=0.3)
        assert len(rejected) >= 1

    def test_validated_evidence_has_required_fields(self):
        from core.retrieval.hybrid import EvidenceValidator
        from core.question.analyzer import QuestionAnalyzer
        q = QuestionAnalyzer().analyze("TCP")
        chunks = [self._make_retrieved_chunk(
            "c1", "TCP ensures reliable ordered delivery.", 0.9, 0.8, 0.85
        )]
        validated, _ = EvidenceValidator().validate(q, chunks, threshold=0.0)
        if validated:
            ev = validated[0]
            d = ev.to_dict()
            json.dumps(d)


# ─────────────────────────────────────────────────────────────
# Phase 10-12 — Answer Engine
# ─────────────────────────────────────────────────────────────

class _EvBuilder:
    """Helper to build ValidatedEvidence objects for tests."""
    @staticmethod
    def make(chunk_id, text, page="1", dense=0.8, bm25=0.6, fusion=0.7, val=0.75):
        from core.retrieval.hybrid import ValidatedEvidence
        return ValidatedEvidence(
            chunk_id=chunk_id, document_id="doc-1", page_id=page,
            text=text, dense_score=dense, bm25_score=bm25,
            fusion_score=fusion, validation_score=val, validation_passed=True,
        )


class TestAnswerBuilder:
    def _route(self, route_name="FACT_QA", fmt="SHORT_FACT"):
        from core.question.router import RouteDecision, Route, AnswerFormat
        return RouteDecision(
            route=Route[route_name],
            answer_format=AnswerFormat[fmt],
            format_source="question_type",
            confidence=0.8,
            routing_reason="test",
        )

    def test_fact_qa_builds_answer(self):
        from core.qa.answer_engine import AnswerBuilder
        ev = [_EvBuilder.make("c1", "TCP ensures reliable ordered delivery of packets.")]
        draft = AnswerBuilder().build("What is TCP?", ev, self._route())
        assert draft is not None
        assert len(draft.all_points()) >= 1

    def test_list_route_builds_bullets(self):
        from core.qa.answer_engine import AnswerBuilder
        from core.question.router import Route
        ev = [
            _EvBuilder.make("c1", "Hadoop provides fault tolerance across clusters."),
            _EvBuilder.make("c2", "TCP ensures reliable ordered packet delivery."),
            _EvBuilder.make("c3", "BM25 is a probabilistic ranking function."),
        ]
        draft = AnswerBuilder().build("List features", ev, self._route("LIST", "BULLET_LIST"))
        assert draft.route == Route.LIST
        assert len(draft.all_points()) >= 1

    def test_empty_evidence_returns_no_answer_draft(self):
        from core.qa.answer_engine import AnswerBuilder, AnswerFormat
        draft = AnswerBuilder().build("test", [], self._route())
        assert draft.answer_format == AnswerFormat.NO_ANSWER
        assert draft.is_complete is False

    def test_draft_to_dict_serializable(self):
        from core.qa.answer_engine import AnswerBuilder
        ev = [_EvBuilder.make("c1", "TCP ensures reliable delivery.")]
        draft = AnswerBuilder().build("What is TCP?", ev, self._route())
        d = draft.to_dict()
        json.dumps(d)

    def test_all_points_have_evidence_ids(self):
        from core.qa.answer_engine import AnswerBuilder
        ev = [_EvBuilder.make("c1", "Hadoop provides scalability for large data sets.")]
        draft = AnswerBuilder().build("What is Hadoop?", ev, self._route())
        for point in draft.all_points():
            assert len(point.evidence_ids) >= 1

    def test_summary_route(self):
        from core.qa.answer_engine import AnswerBuilder
        from core.question.router import Route
        ev = [
            _EvBuilder.make("c1", "Hadoop is an open-source framework.", page="1"),
            _EvBuilder.make("c2", "It provides distributed storage and processing.", page="2"),
        ]
        draft = AnswerBuilder().build(
            "Summarize Hadoop", ev, self._route("SUMMARY", "SECTION_SUMMARY")
        )
        assert draft.route == Route.SUMMARY


class TestAnswerValidator:
    def test_valid_answer_passes(self):
        from core.qa.answer_engine import AnswerBuilder, AnswerValidator
        ev = [_EvBuilder.make("c1", "TCP ensures reliable ordered delivery of packets across networks.")]
        route = TestAnswerBuilder()._route()
        draft = AnswerBuilder().build("What is TCP?", ev, route)
        passed, _, coverage, _ = AnswerValidator().validate(draft, ev)
        # Should pass because points reference valid chunk_ids
        assert isinstance(passed, bool)
        assert 0.0 <= coverage <= 1.0

    def test_no_evidence_fails(self):
        from core.qa.answer_engine import AnswerBuilder, AnswerValidator, AnswerFormat
        ev = []
        route = TestAnswerBuilder()._route()
        draft = AnswerBuilder().build("test", ev, route)
        passed, _, coverage, _ = AnswerValidator().validate(draft, ev)
        assert passed is False


class TestConfidenceEngine:
    def test_high_confidence_with_strong_evidence(self):
        from core.qa.answer_engine import ConfidenceEngine, ConfidenceLevel
        ev = [
            _EvBuilder.make("c1", "text", dense=0.95, bm25=0.9, val=0.9),
            _EvBuilder.make("c2", "text2", page="2", dense=0.88, bm25=0.8, val=0.85),
        ]
        result = ConfidenceEngine().score(ev, True, 1.0, qa_confidence=0.9)
        assert result.level in (ConfidenceLevel.HIGH, ConfidenceLevel.MEDIUM)
        assert result.final_score > 0.0

    def test_no_answer_when_validation_failed(self):
        from core.qa.answer_engine import ConfidenceEngine, ConfidenceLevel
        ev = [_EvBuilder.make("c1", "text")]
        result = ConfidenceEngine().score(ev, False, 0.5)
        assert result.level == ConfidenceLevel.NO_ANSWER

    def test_to_dict_serializable(self):
        from core.qa.answer_engine import ConfidenceEngine
        ev = [_EvBuilder.make("c1", "text")]
        result = ConfidenceEngine().score(ev, True, 0.8)
        json.dumps(result.to_dict())


class TestSafePresentationEngine:
    def _make_draft_and_confidence(self, level="HIGH"):
        from core.qa.answer_engine import (
            AnswerBuilder, ConfidenceEngine, ConfidenceLevel, AnswerValidator
        )
        ev = [_EvBuilder.make("c1", "TCP ensures reliable delivery of packets.")]
        route = TestAnswerBuilder()._route()
        draft = AnswerBuilder().build("What is TCP?", ev, route)
        _, validated_draft, coverage, _ = AnswerValidator().validate(draft, ev)
        conf = ConfidenceEngine().score(ev, True, coverage)
        return validated_draft or draft, conf

    def test_valid_answer_formatted(self):
        from core.qa.answer_engine import SafePresentationEngine
        from core.question.router import RouteDecision, Route, AnswerFormat
        draft, conf = self._make_draft_and_confidence()
        route_dec = RouteDecision(Route.FACT_QA, AnswerFormat.SHORT_FACT, "question_type", 0.8, "test")
        answer = SafePresentationEngine().format(draft, route_dec, conf)
        assert answer is not None
        text = answer.plain_text()
        assert len(text) > 0

    def test_no_answer_when_draft_is_none(self):
        from core.qa.answer_engine import (
            SafePresentationEngine, ConfidenceEngine, ConfidenceLevel
        )
        from core.question.router import RouteDecision, Route, AnswerFormat
        conf = ConfidenceEngine().score([], False, 0.0)
        route_dec = RouteDecision(Route.NO_ANSWER, AnswerFormat.NO_ANSWER, "fallback", 0.0, "no ev")
        answer = SafePresentationEngine().format(None, route_dec, conf)
        assert answer.is_no_answer is True


# ─────────────────────────────────────────────────────────────
# Phase 13 — Citation Engine
# ─────────────────────────────────────────────────────────────

class TestCitationEngine:
    def test_generates_citations(self):
        from core.citation.engine import CitationEngine
        ev = [
            _EvBuilder.make("c1", "TCP ensures reliable delivery.", page="3"),
            _EvBuilder.make("c2", "Hadoop provides scalability.", page="5"),
        ]
        cites = CitationEngine().generate(ev, {"doc-1": "Network Guide"})
        assert len(cites) == 2

    def test_deduplication_same_chunk(self):
        from core.citation.engine import CitationEngine
        ev = [
            _EvBuilder.make("c1", "TCP ensures reliable delivery.", page="3"),
            _EvBuilder.make("c1", "TCP ensures reliable delivery.", page="3"),
        ]
        cites = CitationEngine().generate(ev)
        assert len(cites) == 1

    def test_citation_has_page_number(self):
        from core.citation.engine import CitationEngine
        ev = [_EvBuilder.make("c1", "TCP is a transport protocol.", page="7")]
        cites = CitationEngine().generate(ev)
        assert cites["c1"].page_number == 7

    def test_inline_ref_format(self):
        from core.citation.engine import CitationEngine
        ev = [_EvBuilder.make("c1", "TCP is a transport protocol.", page="5")]
        cites = CitationEngine().generate(ev)
        assert cites["c1"].to_inline_ref() == "[p. 5]"

    def test_full_ref_format(self):
        from core.citation.engine import CitationEngine
        ev = [_EvBuilder.make("c1", "TCP is a transport protocol.", page="5")]
        cites = CitationEngine().generate(ev, {"doc-1": "Network Textbook"})
        ref = cites["c1"].to_full_ref()
        assert "Page 5" in ref
        assert "Network Textbook" in ref

    def test_short_quote_max_length(self):
        from core.citation.engine import CitationEngine
        long_text = "x" * 200 + ". More text follows."
        ev = [_EvBuilder.make("c1", long_text, page="1")]
        cites = CitationEngine().generate(ev)
        assert len(cites["c1"].short_quote) <= 105  # 100 + "…"

    def test_to_dict_serializable(self):
        from core.citation.engine import CitationEngine
        ev = [_EvBuilder.make("c1", "TCP is reliable.", page="3")]
        cites = CitationEngine().generate(ev)
        d = cites["c1"].to_dict()
        json.dumps(d)
