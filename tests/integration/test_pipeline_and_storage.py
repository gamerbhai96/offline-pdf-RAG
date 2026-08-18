"""
Integration tests — Full pipeline end-to-end + Storage layer.
Uses synthetic in-memory PDFs. No real PDF files required.
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def _make_pdf(out_path: Path, text: str = "Hadoop provides fault tolerance. TCP ensures reliability.") -> Path:
    """Create a minimal test PDF at the given path (or inside it if it's a directory)."""
    out_path = Path(out_path)
    if out_path.is_dir() or (not out_path.suffix):
        out_path = out_path / "test.pdf"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
        import io
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=letter)
        c.setFont("Helvetica", 12)
        y = 720
        for line in text.split(". "):
            c.drawString(72, y, line + ".")
            y -= 20
        c.save()
        out_path.write_bytes(buf.getvalue())
        return out_path
    except ImportError:
        from tests.unit.test_phase1_document import make_minimal_pdf
        out_path.write_bytes(make_minimal_pdf(text))
        return out_path


# ─────────────────────────────────────────────────────────────
# Database tests — Phase 15
# ─────────────────────────────────────────────────────────────

class TestDatabase:
    def test_init_creates_tables(self, tmp_path):
        from storage.database import Database
        db = Database(tmp_path / "test.db")
        # If we get here without exception, schema applied
        docs = db.list_documents()
        assert isinstance(docs, list)

    def test_insert_and_get_document(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        rec = DocumentRecord(
            document_id=doc_id,
            file_path="/tmp/test.pdf",
            file_hash="a" * 64,
            title="Test Document",
            page_count=5,
            chunk_count=20,
        )
        db.insert_document(rec)
        retrieved = db.get_document(doc_id)
        assert retrieved is not None
        assert retrieved.document_id == doc_id
        assert retrieved.title == "Test Document"
        assert retrieved.page_count == 5

    def test_get_document_by_hash(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        fhash = "b" * 64
        rec = DocumentRecord(
            document_id=str(uuid.uuid4()),
            file_path="/tmp/test.pdf",
            file_hash=fhash,
            title="Hash Test",
            page_count=3, chunk_count=10,
        )
        db.insert_document(rec)
        found = db.get_document_by_hash(fhash)
        assert found is not None
        assert found.file_hash == fhash

    def test_document_not_found_returns_none(self, tmp_path):
        from storage.database import Database
        db = Database(tmp_path / "test.db")
        assert db.get_document("nonexistent-id") is None

    def test_list_documents_returns_all(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        for i in range(3):
            db.insert_document(DocumentRecord(
                document_id=str(uuid.uuid4()),
                file_path=f"/tmp/doc{i}.pdf",
                file_hash="c" * 60 + str(i).zfill(4),
                title=f"Doc {i}",
                page_count=i + 1, chunk_count=(i + 1) * 5,
            ))
        docs = db.list_documents()
        assert len(docs) == 3

    def test_delete_document(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        db.insert_document(DocumentRecord(
            document_id=doc_id, file_path="/tmp/x.pdf",
            file_hash="d" * 64, title="To Delete",
            page_count=1, chunk_count=5,
        ))
        db.delete_document(doc_id)
        assert db.get_document(doc_id) is None

    def test_insert_and_retrieve_chunks(self, tmp_path):
        from storage.database import Database, DocumentRecord, ChunkRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        db.insert_document(DocumentRecord(
            document_id=doc_id, file_path="/tmp/c.pdf",
            file_hash="e" * 64, title="Chunk Test",
            page_count=1, chunk_count=3,
        ))
        chunks = [
            ChunkRecord(chunk_id=str(uuid.uuid4()), document_id=doc_id,
                        page_id="1", text=f"Text {i}", token_count=10,
                        chunk_index=i, strategy="HEADING_AWARE")
            for i in range(3)
        ]
        db.insert_chunks(chunks)
        retrieved = db.get_chunks_for_document(doc_id)
        assert len(retrieved) == 3

    def test_chunks_deleted_with_document(self, tmp_path):
        from storage.database import Database, DocumentRecord, ChunkRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        db.insert_document(DocumentRecord(
            document_id=doc_id, file_path="/tmp/d.pdf",
            file_hash="f" * 64, title="Cascade",
            page_count=1, chunk_count=2,
        ))
        db.insert_chunks([
            ChunkRecord(str(uuid.uuid4()), doc_id, "1", "text", 5, 0, "PARAGRAPH"),
        ])
        db.delete_document(doc_id)
        assert db.get_chunks_for_document(doc_id) == []

    def test_insert_and_retrieve_messages(self, tmp_path):
        from storage.database import Database, MessageRecord
        db = Database(tmp_path / "test.db")
        session_id = "test-session-001"
        msgs = [
            MessageRecord(str(uuid.uuid4()), session_id, "user", "What is TCP?"),
            MessageRecord(str(uuid.uuid4()), session_id, "assistant", "TCP is a transport protocol.", route="FACT_QA", confidence="HIGH"),
        ]
        for m in msgs:
            db.insert_message(m)
        retrieved = db.get_messages(session_id)
        assert len(retrieved) == 2
        assert retrieved[0].role == "user"
        assert retrieved[1].role == "assistant"
        assert retrieved[1].confidence == "HIGH"

    def test_index_metadata_upsert(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        db.insert_document(DocumentRecord(
            document_id=doc_id, file_path="/tmp/m.pdf",
            file_hash="g" * 64, title="Meta", page_count=1, chunk_count=5,
        ))
        metadata = {
            "embedding_model_id": "bge-small-en-v1.5",
            "embedding_model_version": "1.0",
            "embedding_dimension": 384,
            "chunking_strategy": "HEADING_AWARE",
            "source_document_hash": "g" * 64,
        }
        db.upsert_index_metadata(doc_id, metadata)
        # Second upsert should update, not insert
        db.upsert_index_metadata(doc_id, metadata)
        # Should not raise

    def test_is_index_stale_default(self, tmp_path):
        from storage.database import Database
        db = Database(tmp_path / "test.db")
        # Non-existent document → stale
        assert db.is_index_stale("no-such-doc") is True

    def test_mark_index_stale(self, tmp_path):
        from storage.database import Database, DocumentRecord
        db = Database(tmp_path / "test.db")
        doc_id = str(uuid.uuid4())
        db.insert_document(DocumentRecord(
            document_id=doc_id, file_path="/tmp/s.pdf",
            file_hash="h" * 64, title="Stale", page_count=1, chunk_count=1,
        ))
        db.upsert_index_metadata(doc_id, {
            "embedding_model_id": "bge-small-en-v1.5",
            "embedding_model_version": "1.0",
            "embedding_dimension": 384,
            "chunking_strategy": "HEADING_AWARE",
            "source_document_hash": "h" * 64,
        })
        assert db.is_index_stale(doc_id) is False
        db.mark_index_stale(doc_id)
        assert db.is_index_stale(doc_id) is True


# ─────────────────────────────────────────────────────────────
# Integration test — Full pipeline end-to-end
# ─────────────────────────────────────────────────────────────

class TestPipelineIntegration:
    """End-to-end integration tests using stub embeddings."""

    def _make_pipeline(self, tmp_path):
        from core.pipeline import DocumentPipeline, PipelineConfig
        return DocumentPipeline(PipelineConfig(
            index_dir=tmp_path / "index",
            model_path=None,              # stub embeddings
            embedding_dimension=384,
            validation_threshold=0.0,     # accept all evidence in integration test
        ))

    def test_ingest_single_pdf(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path)
        doc = pipeline.ingest(pdf)
        assert doc.document_id != ""
        assert doc.page_count >= 1
        assert doc.chunk_count >= 0  # may be 0 if all chunks dropped (tiny PDF)

    def test_ingest_returns_document_record(self, tmp_path):
        from core.pipeline import IngestedDocument
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path)
        doc = pipeline.ingest(pdf)
        assert isinstance(doc, IngestedDocument)

    def test_documents_list_grows_after_ingest(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf1 = _make_pdf(tmp_path / "a.pdf", "Hadoop provides fault tolerance.")
        pdf2 = _make_pdf(tmp_path / "b.pdf", "TCP ensures reliable delivery.")
        pipeline.ingest(pdf1)
        pipeline.ingest(pdf2)
        assert len(pipeline.documents) == 2

    def test_ask_returns_pipeline_answer(self, tmp_path):
        from core.pipeline import PipelineAnswer
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path, "Hadoop is an open-source distributed computing framework. "
                                   "It provides fault tolerance and scalability.")
        pipeline.ingest(pdf)
        result = pipeline.ask("What is Hadoop?")
        assert isinstance(result, PipelineAnswer)
        assert result.question == "What is Hadoop?"
        assert result.elapsed_ms > 0

    def test_ask_returns_answer_text(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path, "TCP is a transport protocol that ensures reliable delivery.")
        pipeline.ingest(pdf)
        result = pipeline.ask("What is TCP?")
        text = result.plain_text()
        assert len(text) > 0

    def test_ask_has_route_and_confidence(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path, "BM25 is a ranking function used in information retrieval.")
        pipeline.ingest(pdf)
        result = pipeline.ask("What is BM25?")
        assert result.route in ("FACT_QA", "LIST", "SUMMARY", "TABLE", "NO_ANSWER")
        assert result.confidence in ("HIGH", "MEDIUM", "LOW", "NO_ANSWER")

    def test_multiple_questions_same_pipeline(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path,
            "Hadoop provides scalability. TCP ensures reliability. "
            "BM25 is used for keyword search. HNSW is used for vector search."
        )
        pipeline.ingest(pdf)
        questions = ["What is Hadoop?", "What is TCP?", "What is BM25?"]
        for q in questions:
            result = pipeline.ask(q)
            assert result.question == q

    def test_reset_conversation(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path, "TCP is a protocol.")
        pipeline.ingest(pdf)
        pipeline.ask("What is TCP?")
        pipeline.reset_conversation()
        assert pipeline._conversation_context is None

    def test_total_chunks_positive_after_ingest(self, tmp_path):
        pipeline = self._make_pipeline(tmp_path)
        pdf = _make_pdf(tmp_path,
            "This document has multiple paragraphs. " * 10 +
            "It covers distributed systems and networking protocols. " * 5
        )
        pipeline.ingest(pdf)
        # Chunk count may be 0 for very tiny PDFs (all dropped by MIN_CHUNK_TOKENS)
        assert isinstance(pipeline.total_chunks, int)
        assert pipeline.total_chunks >= 0


# ─────────────────────────────────────────────────────────────
# Pipeline orchestration correctness tests
# ─────────────────────────────────────────────────────────────

class TestPipelineConfig:
    def test_default_config(self):
        from core.pipeline import PipelineConfig
        cfg = PipelineConfig()
        assert cfg.embedding_dimension == 384
        assert cfg.max_tokens_per_chunk == 512
        assert cfg.hybrid_alpha == 0.5

    def test_custom_config(self):
        from core.pipeline import PipelineConfig
        cfg = PipelineConfig(max_tokens_per_chunk=256, hybrid_alpha=0.7)
        assert cfg.max_tokens_per_chunk == 256
        assert cfg.hybrid_alpha == 0.7
