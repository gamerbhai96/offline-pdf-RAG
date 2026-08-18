"""
Document Intelligence Pipeline — Master Orchestrator

Connects all phases into a single coherent pipeline:

  Phase 1: DocumentParser → ParsedDocument
  Phase 2: Chunker → ChunkingResult
  Phase 3: EmbeddingEngine → embeddings
  Phase 4: LexicalIndexer → BM25 index
  Phase 5: VectorIndex → HNSW/BruteForce index
  Phase 6: QuestionAnalyzer + ConversationResolver → NormalizedQuery
  Phase 7: QuestionRouter → RouteDecision
  Phase 8: HybridRetriever → RetrievedChunks
  Phase 9: DeterministicRanker + EvidenceValidator → ValidatedEvidence
  Phase 10-12: AnswerBuilder + Validator + Confidence → PresentableAnswer
  Phase 13: CitationEngine → Citations

Usage:
    pipeline = DocumentPipeline(config=PipelineConfig())
    pipeline.ingest(pdf_path)
    answer = pipeline.ask("What is TCP?")
    print(answer.plain_text())
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Pipeline configuration ─────────────────────────────────────────────────────

@dataclass
class PipelineConfig:
    """Single configuration object for the full pipeline."""
    # Storage
    index_dir: Path = Path("./index_store")
    model_path: Optional[Path] = None          # ONNX model path (None = stub embeddings)

    # Embedding
    embedding_model_id: str = "bge-small-en-v1.5"
    embedding_dimension: int = 384

    # Chunking
    max_tokens_per_chunk: int = 512
    overlap_tokens: int = 64

    # Retrieval
    top_k_dense: int = 50
    top_k_bm25: int = 50
    hybrid_alpha: float = 0.5              # 0=BM25 only, 1=dense only

    # Validation
    validation_threshold: float = 0.30

    # OCR
    force_ocr: bool = False

    # Presentation
    max_list_items: int = 8


# ── Pipeline state ─────────────────────────────────────────────────────────────

@dataclass
class IngestedDocument:
    document_id: str
    file_path: str
    file_hash: str
    page_count: int
    chunk_count: int
    title: Optional[str] = None


@dataclass
class DebugTrace:
    # Phase 6
    original_query: str = ""
    normalized_query: str = ""
    query_entity: list[str] = field(default_factory=list)
    query_attribute: Optional[str] = None
    query_intent: str = ""
    query_format: str = "AUTO"
    
    # Aliases for backwards compatibility
    entities: list[dict] = field(default_factory=list)
    target_attribute: Optional[str] = None
    intent: str = ""
    format_hint: str = ""
    
    # Phase 7
    route: str = ""
    
    # Phase 8 (Hybrid Retrieval)
    bm25_top: list[dict] = field(default_factory=list)
    dense_top: list[dict] = field(default_factory=list)
    hybrid_top: list[dict] = field(default_factory=list)
    
    # Phase 9 (Ranking & Validation)
    ranked_results: list[dict] = field(default_factory=list)
    gate_1_results: list[dict] = field(default_factory=list)
    
    # Phase 10 (Extraction)
    answer_draft: dict = field(default_factory=dict)
    
    # Phase 11 (Answer Validation)
    gate_2_passed: bool = False
    unsupported_items: list[dict] = field(default_factory=list)
    
    # Phase 12 (Confidence)
    retrieval_confidence: float = 0.0
    evidence_confidence: float = 0.0
    answerability_confidence: float = 0.0
    final_confidence_level: str = ""
    
    # Final Decision
    final_decision: str = ""


@dataclass
class PipelineAnswer:
    question: str
    answer: "PresentableAnswer"             # type: ignore
    citations: dict[str, "Citation"]        # type: ignore
    elapsed_ms: float
    route: str
    confidence: str
    debug_trace: Optional[DebugTrace] = None

    def plain_text(self) -> str:
        text = self.answer.plain_text()
        if getattr(self.answer, "is_no_answer", False):
            return text

        if self.citations:
            text += "\n\nSources:"
            seen_pages: set[int] = set()
            for cite in self.citations.values():
                if cite.page_number not in seen_pages:
                    text += f"\n  {cite.to_full_ref()}"
                    seen_pages.add(cite.page_number)
        return text


# ── Main pipeline ──────────────────────────────────────────────────────────────

class DocumentPipeline:
    """
    Full offline document intelligence pipeline.
    Manages state: loaded documents, index, conversation context.
    """

    def __init__(self, config: Optional[PipelineConfig] = None):
        self.config = config or PipelineConfig()
        self._documents: dict[str, IngestedDocument] = {}
        self._chunks: dict[str, list] = {}  # document_id → [Chunk]
        self._conversation_context = None
        self._session_id = f"session_{int(time.time())}"

        # Lazy-initialized components
        self._embedding_engine = None
        self._lexical_indexer = None
        self._vector_index = None

        self._init_components()

    def _init_components(self) -> None:
        from core.embedding.engine import EmbeddingEngine
        from core.lexical.indexer import LexicalIndexer
        from core.vector.index import create_vector_index

        stub = self.config.model_path is None
        self._embedding_engine = EmbeddingEngine(
            model_id=self.config.embedding_model_id,
            model_path=self.config.model_path,
            stub=stub,
            dimension=self.config.embedding_dimension,
        )
        self._lexical_indexer = LexicalIndexer()
        self._vector_index = create_vector_index(
            dimension=self.config.embedding_dimension,
            prefer_hnsw=True,
        )
        self._question_analyzer = _import("core.question.analyzer", "QuestionAnalyzer")()
        self._conversation_resolver = _import("core.question.analyzer", "ConversationResolver")()
        self._question_router = _import("core.question.router", "QuestionRouter")()

    # ── Document ingestion ─────────────────────────────────────────────────────

    def ingest(self, pdf_path: str | Path, password: str | None = None) -> IngestedDocument:
        """
        Full ingestion pipeline: parse → structure → chunk → embed → index.
        Safe: one bad page does NOT fail the whole ingestion.
        """
        from core.document.parser import DocumentParser, ParseOptions
        from core.document.structure import StructureAnalyzer
        from core.chunking.chunker import Chunker
        import uuid

        path = Path(pdf_path)
        t0 = time.perf_counter()
        document_id = str(uuid.uuid4())

        log.info("Ingesting: %s", path.name)

        # Phase 1: Parse
        parser = DocumentParser(options=ParseOptions(force_ocr=self.config.force_ocr))
        parsed_doc = parser.parse(path, password=password)
        log.info("Parsed: %d pages, %d errors", parsed_doc.page_count, len(parsed_doc.errors))

        # Phase 2: Structure + chunk
        analyzer = StructureAnalyzer()
        structured = analyzer.analyze(parsed_doc, document_id=document_id)

        chunker = Chunker(
            max_tokens=self.config.max_tokens_per_chunk,
            overlap_tokens=self.config.overlap_tokens,
        )
        chunk_result = chunker.chunk(structured, document_id)
        chunks = chunk_result.chunks
        log.info("Chunked: %d chunks (strategy=%s)", len(chunks), chunk_result.strategy.value)

        # Phase 3: Embed
        if chunks:
            texts = [c.text for c in chunks]
            ids   = [c.chunk_id for c in chunks]

            BATCH = 64
            import numpy as np
            all_embeddings = []
            for i in range(0, len(texts), BATCH):
                batch_results = self._embedding_engine.embed_passages(
                    texts[i:i+BATCH], ids[i:i+BATCH]
                )
                all_embeddings.append(
                    np.array([r.embedding for r in batch_results], dtype="float32")
                )
            embeddings = np.vstack(all_embeddings)

            # Phase 4: BM25 index (rebuild with all documents)
            all_chunks = list(self._get_all_chunks()) + chunks
            self._lexical_indexer.build(all_chunks)

            # Phase 5: Vector index
            self._vector_index.add(ids, embeddings)
            log.info("Indexed: %d embeddings (dim=%d)", len(ids), embeddings.shape[1])

        # Store state
        self._chunks[document_id] = chunks

        raw_title = parsed_doc.title or path.name
        clean_title = re.sub(r"^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}_", "", raw_title).strip()

        doc = IngestedDocument(
            document_id=document_id,
            file_path=str(path),
            file_hash=parsed_doc.file_hash,
            page_count=parsed_doc.page_count,
            chunk_count=len(chunks),
            title=clean_title,
        )
        self._documents[document_id] = doc

        elapsed = (time.perf_counter() - t0) * 1000
        log.info("Ingestion complete: %.1f ms", elapsed)
        return doc

    # ── Querying ───────────────────────────────────────────────────────────────

    def ask(
        self,
        question: str,
        document_id: Optional[str] = None,
    ) -> PipelineAnswer:
        """
        Answer a natural language question against ingested documents.
        """
        from core.question.analyzer import ConversationContext
        from core.retrieval.hybrid import HybridRetriever, DeterministicRanker, EvidenceValidator
        from core.qa.answer_engine import (
            AnswerBuilder, AnswerValidator, ConfidenceEngine, SafePresentationEngine
        )
        from core.citation.engine import CitationEngine
        from core.question.router import RetrievalPreview

        t0 = time.perf_counter()
        trace = DebugTrace()

        # Phase 6: Analyze question
        if self._conversation_context is None:
            self._conversation_context = ConversationContext(session_id=self._session_id)

        analyzed = self._question_analyzer.analyze(question, self._conversation_context)
        resolved = self._conversation_resolver.resolve(analyzed, self._conversation_context)
        
        trace.original_query = question
        trace.normalized_query = resolved.resolved_query or resolved.normalized_query
        trace.query_entity = [e.text for e in resolved.entities]
        trace.query_attribute = getattr(resolved, "target_attribute", None)
        trace.query_intent = resolved.question_type.value
        trace.query_format = resolved.format_hint.value if resolved.format_hint else "AUTO"

        trace.entities = [{"text": e.text, "type": e.etype} for e in resolved.entities]
        trace.target_attribute = trace.query_attribute
        trace.intent = trace.query_intent
        trace.format_hint = trace.query_format

        # Phase 8: Retrieve
        retriever = HybridRetriever(
            vector_index=self._vector_index,
            lexical_indexer=self._lexical_indexer,
            embedding_engine=self._embedding_engine,
            alpha=self.config.hybrid_alpha,
            top_k=self.config.top_k_dense,
        )

        doc_id = document_id or (list(self._documents.keys())[-1] if self._documents else None)
        raw_candidates = retriever.retrieve(resolved, document_id=doc_id)
        
        # Log Hybrid Results
        for c in raw_candidates[:5]:
            trace.hybrid_top.append({
                "chunk_id": c.chunk_id, "score": c.fusion_score,
                "dense": c.dense_score, "bm25": c.bm25_score, "text": c.text[:50]
            })

        # Preview for router
        preview = RetrievalPreview(
            has_table_evidence=any("\t" in c.text for c in raw_candidates),
            evidence_count=len(raw_candidates),
            top_score=raw_candidates[0].fusion_score if raw_candidates else 0.0,
        )

        # Phase 7: Route
        route = self._question_router.route(resolved, preview)
        trace.route = route.route.value

        # Phase 9: Rank + validate
        ranker = DeterministicRanker()
        ranked = ranker.rerank(resolved, raw_candidates, top_k=20)
        for c in ranked:
            trace.ranked_results.append({
                "chunk_id": c.chunk_id,
                "page_id": c.page_id,
                "dense_score": round(c.dense_score, 4),
                "bm25_score": round(c.bm25_score, 4),
                "rrf_score": round(c.rrf_score, 4),
                "term_overlap": round(c.term_overlap, 4),
                "entity_overlap": round(c.entity_overlap, 4),
                "attribute_overlap": round(c.attribute_overlap, 4),
                "heading_score": round(c.heading_score, 4),
                "section_score": round(c.section_score, 4),
                "phrase_match": round(c.phrase_match, 4),
                "final_score": round(c.final_score, 4),
                "text_snippet": c.text[:100]
            })

        validator = EvidenceValidator()
        validated, rejected = validator.validate(
            resolved, ranked, threshold=self.config.validation_threshold
        )
        log.info(
            "Evidence: %d validated, %d rejected (route=%s)",
            len(validated), len(rejected), route.route.value,
        )
        for v in validated:
            trace.gate_1_results.append({"chunk_id": v.chunk_id, "status": "PASS", "score": v.validation_score})
        for r in rejected:
            trace.gate_1_results.append({"chunk_id": r["chunk_id"], "status": "FAIL", "reason": r["rejection_reason"]})

        # Phase 10-12: Build + validate + score answer
        builder = AnswerBuilder()
        draft = builder.build(question, validated, route)
        if draft:
            trace.answer_draft = draft.to_dict()

        ans_validator = AnswerValidator()
        passed, validated_draft, coverage, unsupported = ans_validator.validate(draft, validated)
        trace.gate_2_passed = passed
        trace.unsupported_items = unsupported

        conf_engine = ConfidenceEngine()
        confidence = conf_engine.score(
            evidence=validated,
            validation_passed=passed,
            coverage_score=coverage,
            is_complete=draft.is_complete if draft else False,
        )
        
        # Store separated confidence trace
        trace.retrieval_confidence = confidence.retrieval_score
        trace.evidence_confidence = confidence.evidence_score
        trace.answerability_confidence = confidence.answerability_score
        trace.final_confidence_level = confidence.level.value
        trace.final_decision = "ANSWER" if passed and confidence.level.value != "NO_ANSWER" else "NO_ANSWER"

        presenter = SafePresentationEngine()
        answer = presenter.format(validated_draft if passed else None, route, confidence)

        # Phase 13: Citations
        doc_titles = {d.document_id: d.title for d in self._documents.values()}
        cite_engine = CitationEngine()
        
        used_evidence = []
        if passed and validated_draft and not getattr(answer, "is_no_answer", False):
            used_evidence_ids = {eid for pt in validated_draft.all_points() for eid in pt.evidence_ids}
            used_evidence = [e for e in validated if e.chunk_id in used_evidence_ids]
            
        citations = cite_engine.generate(used_evidence, document_titles=doc_titles)

        # Update conversation context
        self._conversation_context.turn_index += 1
        self._conversation_context.push_entities(
            resolved.entities, self._conversation_context.turn_index
        )

        elapsed = (time.perf_counter() - t0) * 1000
        log.info("Answer in %.1f ms (confidence=%s)", elapsed, confidence.level.value)

        return PipelineAnswer(
            question=question,
            answer=answer,
            citations=citations,
            elapsed_ms=elapsed,
            route=route.route.value,
            confidence=confidence.level.value,
            debug_trace=trace,
        )

    # ── Utilities ──────────────────────────────────────────────────────────────

    def _get_all_chunks(self):
        for chunks in self._chunks.values():
            yield from chunks

    @property
    def documents(self) -> list[IngestedDocument]:
        return list(self._documents.values())

    @property
    def total_chunks(self) -> int:
        return sum(len(c) for c in self._chunks.values())

    def reset_conversation(self) -> None:
        self._conversation_context = None
        log.info("Conversation context reset.")


def _import(module: str, name: str):
    import importlib
    m = importlib.import_module(module)
    return getattr(m, name)
