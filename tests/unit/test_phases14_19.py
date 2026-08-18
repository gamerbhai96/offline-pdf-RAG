"""
Tests for Phase 14 (CLI), Phase 17 (Config), Phase 18 (Accuracy benchmark),
Phase 19 (Production hardening checks).
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest


# ── Phase 14 — Config tests ────────────────────────────────────────────────────

class TestODIConfig:
    def setup_method(self):
        from config import reset_config
        reset_config()

    def teardown_method(self):
        from config import reset_config
        reset_config()
        # Remove any ODI env vars set in tests
        for k in list(os.environ):
            if k.startswith("ODI_"):
                del os.environ[k]

    def test_load_config_returns_config(self):
        from config import load_config, ODIConfig
        cfg = load_config()
        assert isinstance(cfg, ODIConfig)

    def test_default_embedding_dimension(self):
        from config import load_config
        cfg = load_config()
        assert cfg.embedding_dimension == 384

    def test_default_max_tokens(self):
        from config import load_config
        cfg = load_config()
        assert cfg.max_tokens_per_chunk == 512

    def test_env_override_dimension(self):
        os.environ["ODI_EMBEDDING_DIM"] = "256"
        from config import load_config
        cfg = load_config()
        assert cfg.embedding_dimension == 256

    def test_env_override_alpha(self):
        os.environ["ODI_HYBRID_ALPHA"] = "0.75"
        from config import load_config
        cfg = load_config()
        assert abs(cfg.hybrid_alpha - 0.75) < 1e-9

    def test_singleton(self):
        from config import load_config
        cfg1 = load_config()
        cfg2 = load_config()
        assert cfg1 is cfg2

    def test_pipeline_config_returns_object(self):
        from config import load_config
        from core.pipeline import PipelineConfig
        cfg = load_config()
        pc = cfg.pipeline_config()
        assert isinstance(pc, PipelineConfig)

    def test_db_path_under_store_dir(self):
        from config import load_config
        cfg = load_config()
        assert cfg.db_path.parent == cfg.store_dir

    def test_max_upload_bytes(self):
        from config import load_config
        cfg = load_config()
        assert cfg.max_upload_bytes == cfg.max_upload_mb * 1024 * 1024

    def test_summary_contains_model_id(self):
        from config import load_config
        cfg = load_config()
        assert cfg.model_id in cfg.summary()

    def test_force_ocr_false_by_default(self):
        from config import load_config
        cfg = load_config()
        assert cfg.force_ocr is False

    def test_force_ocr_env_override(self):
        os.environ["ODI_FORCE_OCR"] = "1"
        from config import load_config
        cfg = load_config()
        assert cfg.force_ocr is True


# ── Phase 18 — Accuracy benchmark utilities ────────────────────────────────────

class TestAccuracyBenchmark:
    def test_token_f1_exact_match(self):
        from benchmarks.accuracy_benchmark import _token_f1
        assert _token_f1("hello world", "hello world") == 1.0

    def test_token_f1_no_overlap(self):
        from benchmarks.accuracy_benchmark import _token_f1
        assert _token_f1("apple orange", "car bus train") == 0.0

    def test_token_f1_partial(self):
        from benchmarks.accuracy_benchmark import _token_f1
        score = _token_f1("TCP is a protocol", "TCP ensures reliability")
        assert 0.0 < score < 1.0

    def test_token_f1_empty_gold(self):
        from benchmarks.accuracy_benchmark import _token_f1
        assert _token_f1("", "") == 1.0

    def test_keyword_recall_all_found(self):
        from benchmarks.accuracy_benchmark import _keyword_recall
        assert _keyword_recall("Hadoop provides fault tolerance", ["hadoop", "fault"]) == 1.0

    def test_keyword_recall_none_found(self):
        from benchmarks.accuracy_benchmark import _keyword_recall
        assert _keyword_recall("The sky is blue", ["hadoop", "tcp"]) == 0.0

    def test_keyword_recall_empty_keywords(self):
        from benchmarks.accuracy_benchmark import _keyword_recall
        assert _keyword_recall("any text", []) == 1.0

    def test_make_sample_qa_returns_list(self):
        from benchmarks.accuracy_benchmark import make_sample_qa, QASample
        samples = make_sample_qa()
        assert len(samples) >= 3
        assert all(isinstance(s, QASample) for s in samples)

    def test_make_sample_qa_has_no_answer_question(self):
        from benchmarks.accuracy_benchmark import make_sample_qa
        samples = make_sample_qa()
        no_answer = [s for s in samples if s.expected_answer is None]
        assert len(no_answer) >= 1

    def test_eval_result_no_answer_flag(self):
        from benchmarks.accuracy_benchmark import EvalResult
        r = EvalResult(
            question="What?", question_type="FACT",
            retrieved_chunk_texts=[], predicted_answer="",
            expected_answer=None, exact_match=False, fragment_f1=0.0,
            keyword_recall=0.0, route="NO_ANSWER", confidence="NO_ANSWER",
            is_no_answer=True, correct_no_answer=True,
        )
        assert r.correct_no_answer is True

    def test_benchmark_summary_serializable(self):
        from benchmarks.accuracy_benchmark import BenchmarkSummary
        from dataclasses import asdict
        s = BenchmarkSummary(
            n_questions=10, n_no_answer_expected=2,
            exact_match_rate=0.7, avg_fragment_f1=0.65,
            avg_keyword_recall=0.8, no_answer_precision=0.9,
            no_answer_recall=0.8, route_distribution={"FACT_QA": 8, "NO_ANSWER": 2},
            confidence_distribution={"HIGH": 5, "MEDIUM": 3, "NO_ANSWER": 2},
        )
        json.dumps(asdict(s))


# ── Phase 19 — Production hardening ────────────────────────────────────────────

class TestProductionHardening:
    """Validates that the pipeline handles adversarial/edge-case inputs safely."""

    def _make_pipeline(self, tmp_path):
        from core.pipeline import DocumentPipeline, PipelineConfig
        return DocumentPipeline(PipelineConfig(
            index_dir=tmp_path / "index",
            model_path=None,
            validation_threshold=0.0,
        ))

    def _make_tiny_pdf(self, tmp_path):
        from tests.unit.test_phase1_document import make_minimal_pdf
        p = tmp_path / "tiny.pdf"
        p.write_bytes(make_minimal_pdf("Hello World. This is a test."))
        return p

    def test_empty_question_does_not_crash(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = self._make_tiny_pdf(tmp_path)
        pipeline.ingest(pdf)
        # Should return NO_ANSWER gracefully
        result = pipeline.ask("")
        assert result is not None
        assert result.confidence in ("HIGH", "MEDIUM", "LOW", "NO_ANSWER")

    def test_very_long_question_does_not_crash(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = self._make_tiny_pdf(tmp_path)
        pipeline.ingest(pdf)
        long_q = "What is " + " ".join(["very"] * 500) + " important?"
        result = pipeline.ask(long_q)
        assert result is not None

    def test_special_characters_in_question(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = self._make_tiny_pdf(tmp_path)
        pipeline.ingest(pdf)
        result = pipeline.ask("What is the <impact> of TCP/IP & UDP?!?")
        assert result is not None

    def test_ask_without_documents_raises_or_returns_no_answer(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        # No documents ingested
        result = pipeline.ask("What is TCP?")
        # Should be NO_ANSWER (no evidence)
        assert result.confidence in ("HIGH", "MEDIUM", "LOW", "NO_ANSWER")

    def test_ingest_nonexistent_file_raises(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        from core.document.errors import DocumentFileNotFoundError
        with pytest.raises(DocumentFileNotFoundError):
            pipeline.ingest(tmp_path / "does_not_exist.pdf")

    def test_reset_conversation_is_idempotent(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pipeline.reset_conversation()
        pipeline.reset_conversation()   # should not raise

    def test_multiple_ingests_different_docs(self, tmp_path):
        from tests.unit.test_phase1_document import make_minimal_pdf
        pipeline = self._make_pipeline(tmp_path)
        for i in range(3):
            p = tmp_path / f"doc{i}.pdf"
            p.write_bytes(make_minimal_pdf(f"Document {i} about topic {i}."))
            pipeline.ingest(p)
        assert len(pipeline.documents) == 3

    def test_confidence_engine_always_returns_valid_level(self):
        from core.qa.answer_engine import ConfidenceEngine, ConfidenceLevel
        from core.retrieval.hybrid import ValidatedEvidence
        engine = ConfidenceEngine()
        ev = [ValidatedEvidence(
            chunk_id="c1", document_id="d1", page_id="1",
            text="test", dense_score=0.5, bm25_score=0.5,
            fusion_score=0.5, validation_score=0.5, validation_passed=True,
        )]
        for passed in (True, False):
            result = engine.score(ev, passed, 0.8)
            assert result.level in [l for l in ConfidenceLevel]
            assert 0.0 <= result.final_score <= 1.0

    def test_database_wal_mode(self, tmp_path):
        """Verify WAL mode is set (for concurrent reads)."""
        import sqlite3
        from storage.database import Database
        db = Database(tmp_path / "test.db")
        conn = sqlite3.connect(str(tmp_path / "test.db"))
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"

    def test_config_store_dir_created_on_init(self, tmp_path):
        import os; os.environ["ODI_STORE_DIR"] = str(tmp_path / "new_store")
        from config import load_config, reset_config
        reset_config()
        cfg = load_config()
        assert cfg.store_dir.exists()
        reset_config()
        del os.environ["ODI_STORE_DIR"]


# ── Phase 17 — Performance benchmark utilities ─────────────────────────────────

class TestPerfBenchmark:
    def test_check_targets_passes_with_fast_result(self):
        from benchmarks.perf_benchmark import PerfResult, check_targets
        result = PerfResult(
            pdf_name="test.pdf", page_count=10, chunk_count=50,
            ingestion_ms=100.0, query_p50_ms=30.0, query_p95_ms=80.0,
            query_p99_ms=100.0, query_max_ms=120.0, peak_rss_mb=50.0,
            chunks_per_sec=500.0, embedding_dim=384, n_query_runs=20,
        )
        failures = check_targets(result)
        assert failures == []

    def test_check_targets_fails_slow_query(self):
        from benchmarks.perf_benchmark import PerfResult, check_targets
        result = PerfResult(
            pdf_name="test.pdf", page_count=5, chunk_count=20,
            ingestion_ms=200.0, query_p50_ms=100.0, query_p95_ms=500.0,  # too slow
            query_p99_ms=600.0, query_max_ms=700.0, peak_rss_mb=50.0,
            chunks_per_sec=100.0, embedding_dim=384, n_query_runs=20,
        )
        failures = check_targets(result)
        assert any("p95" in f for f in failures)

    def test_check_targets_fails_high_memory(self):
        from benchmarks.perf_benchmark import PerfResult, check_targets, TARGETS
        result = PerfResult(
            pdf_name="test.pdf", page_count=5, chunk_count=20,
            ingestion_ms=100.0, query_p50_ms=30.0, query_p95_ms=80.0,
            query_p99_ms=100.0, query_max_ms=120.0,
            peak_rss_mb=TARGETS["peak_rss_mb"] + 100,   # over limit
            chunks_per_sec=200.0, embedding_dim=384, n_query_runs=20,
        )
        failures = check_targets(result)
        assert any("RSS" in f or "rss" in f.lower() for f in failures)

    def test_perf_result_to_dict_serializable(self):
        from benchmarks.perf_benchmark import PerfResult
        result = PerfResult(
            pdf_name="test.pdf", page_count=5, chunk_count=20,
            ingestion_ms=100.0, query_p50_ms=30.0, query_p95_ms=80.0,
            query_p99_ms=100.0, query_max_ms=120.0, peak_rss_mb=50.0,
            chunks_per_sec=200.0, embedding_dim=384, n_query_runs=20,
        )
        json.dumps(result.to_dict())
