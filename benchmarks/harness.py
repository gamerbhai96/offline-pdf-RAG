"""
Offline Document Intelligence Engine — Evaluation Harness
Phase 0: Benchmark Framework

This harness measures system accuracy across all phases.
It is NOT a final-only activity — run after every phase that
touches retrieval, chunking, embedding, QA, or confidence.

Usage:
    python -m benchmarks.harness --suite retrieval --phase 7
    python -m benchmarks.harness --suite all --report html
    python -m benchmarks.harness --compare baseline_p7.json current.json
"""

from __future__ import annotations

import json
import time
import statistics
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any
from enum import Enum

# ---------------------------------------------------------------------------
# Question Types
# ---------------------------------------------------------------------------

class QuestionType(str, Enum):
    FACTUAL = "FACTUAL"
    DEFINITION = "DEFINITION"
    LIST = "LIST"
    STEPS = "STEPS"
    NUMERICAL = "NUMERICAL"
    TABLE = "TABLE"
    COMPARISON = "COMPARISON"
    MULTI_PAGE = "MULTI_PAGE"
    SUMMARY = "SUMMARY"
    QUOTE = "QUOTE"
    NO_ANSWER = "NO_ANSWER"
    AMBIGUOUS = "AMBIGUOUS"
    FOLLOW_UP = "FOLLOW_UP"


# ---------------------------------------------------------------------------
# Benchmark Data Structures
# ---------------------------------------------------------------------------

@dataclass
class BenchmarkQuestion:
    """A single benchmark question with expected answers."""
    question_id: str
    question: str
    document: str                         # filename relative to datasets/pdfs/
    question_type: QuestionType
    expected_pages: list[int]             # pages expected to contain the answer
    expected_evidence: list[str]          # key phrases that must appear in evidence
    expected_answer_fragments: list[str]  # fragments that must appear in answer
    is_answerable: bool = True            # False for NO_ANSWER questions
    follow_up_of: str | None = None       # question_id this follows up on
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["question_type"] = self.question_type.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BenchmarkQuestion":
        d = d.copy()
        d["question_type"] = QuestionType(d["question_type"])
        return cls(**d)


@dataclass
class RetrievalResult:
    """Metrics for a single retrieval evaluation."""
    question_id: str
    retrieved_pages: list[int]
    retrieved_chunk_ids: list[str]
    retrieved_scores: list[float]
    expected_pages: list[int]

    # Computed metrics
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0       # Mean Reciprocal Rank
    ndcg: float = 0.0
    latency_ms: float = 0.0


@dataclass
class AnswerResult:
    """Metrics for a single answer evaluation."""
    question_id: str
    predicted_answer: str
    expected_fragments: list[str]
    expected_pages: list[int]
    cited_pages: list[int]

    # Computed metrics
    exact_match: float = 0.0          # 1.0 if answer contains all expected fragments
    fragment_f1: float = 0.0          # F1 over expected fragments found
    citation_accuracy: float = 0.0    # fraction of expected pages cited
    no_answer_accuracy: float = 0.0   # 1.0 if no-answer correctly detected
    answer_latency_ms: float = 0.0
    confidence_level: str = ""
    validation_passed: bool = False


@dataclass
class PhaseMetrics:
    """Aggregate metrics for a full benchmark run."""
    phase: str
    run_timestamp: str
    total_questions: int = 0
    questions_by_type: dict[str, int] = field(default_factory=dict)

    # Retrieval
    recall_at_1: float = 0.0
    recall_at_5: float = 0.0
    recall_at_10: float = 0.0
    mrr: float = 0.0
    ndcg: float = 0.0

    # Answer
    exact_match: float = 0.0
    fragment_f1: float = 0.0
    citation_accuracy: float = 0.0
    no_answer_accuracy: float = 0.0
    answer_validation_rate: float = 0.0   # fraction passing Gate 2

    # Performance
    avg_retrieval_latency_ms: float = 0.0
    avg_answer_latency_ms: float = 0.0
    p95_total_latency_ms: float = 0.0

    # Regressions vs. previous run
    regressions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Metric Computation
# ---------------------------------------------------------------------------

class MetricComputer:
    """Computes retrieval and answer metrics."""

    @staticmethod
    def recall_at_k(retrieved_pages: list[int], expected_pages: list[int], k: int) -> float:
        """Fraction of expected pages found in top-k retrieved pages."""
        if not expected_pages:
            return 1.0
        top_k = set(retrieved_pages[:k])
        found = sum(1 for p in expected_pages if p in top_k)
        return found / len(expected_pages)

    @staticmethod
    def mrr(retrieved_pages: list[int], expected_pages: list[int]) -> float:
        """Mean Reciprocal Rank — rank of first relevant page."""
        expected_set = set(expected_pages)
        for i, page in enumerate(retrieved_pages, 1):
            if page in expected_set:
                return 1.0 / i
        return 0.0

    @staticmethod
    def ndcg(retrieved_pages: list[int], expected_pages: list[int], k: int = 10) -> float:
        """Normalized Discounted Cumulative Gain at k."""
        import math
        expected_set = set(expected_pages)
        dcg = 0.0
        for i, page in enumerate(retrieved_pages[:k], 1):
            if page in expected_set:
                dcg += 1.0 / math.log2(i + 1)
        # Ideal DCG: all expected pages retrieved at top positions
        idcg = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(expected_pages), k) + 1))
        return dcg / idcg if idcg > 0 else 0.0

    @staticmethod
    def fragment_f1(predicted: str, expected_fragments: list[str]) -> float:
        """Fraction of expected fragments found in the predicted answer (case-insensitive)."""
        if not expected_fragments:
            return 1.0
        pred_lower = predicted.lower()
        found = sum(1 for f in expected_fragments if f.lower() in pred_lower)
        precision = found / len(expected_fragments)
        recall = found / len(expected_fragments)
        if precision + recall == 0:
            return 0.0
        return 2 * precision * recall / (precision + recall)

    @staticmethod
    def exact_match(predicted: str, expected_fragments: list[str]) -> float:
        """1.0 if ALL expected fragments are found in predicted answer."""
        if not expected_fragments:
            return 1.0
        pred_lower = predicted.lower()
        return 1.0 if all(f.lower() in pred_lower for f in expected_fragments) else 0.0

    @classmethod
    def compute_retrieval(
        cls,
        question: BenchmarkQuestion,
        retrieved_pages: list[int],
        retrieved_chunk_ids: list[str],
        retrieved_scores: list[float],
        latency_ms: float
    ) -> RetrievalResult:
        r = RetrievalResult(
            question_id=question.question_id,
            retrieved_pages=retrieved_pages,
            retrieved_chunk_ids=retrieved_chunk_ids,
            retrieved_scores=retrieved_scores,
            expected_pages=question.expected_pages,
            latency_ms=latency_ms
        )
        r.recall_at_1 = cls.recall_at_k(retrieved_pages, question.expected_pages, 1)
        r.recall_at_5 = cls.recall_at_k(retrieved_pages, question.expected_pages, 5)
        r.recall_at_10 = cls.recall_at_k(retrieved_pages, question.expected_pages, 10)
        r.mrr = cls.mrr(retrieved_pages, question.expected_pages)
        r.ndcg = cls.ndcg(retrieved_pages, question.expected_pages)
        return r

    @classmethod
    def compute_answer(
        cls,
        question: BenchmarkQuestion,
        predicted_answer: str,
        cited_pages: list[int],
        confidence_level: str,
        validation_passed: bool,
        latency_ms: float
    ) -> AnswerResult:
        r = AnswerResult(
            question_id=question.question_id,
            predicted_answer=predicted_answer,
            expected_fragments=question.expected_evidence,
            expected_pages=question.expected_pages,
            cited_pages=cited_pages,
            confidence_level=confidence_level,
            validation_passed=validation_passed,
            answer_latency_ms=latency_ms
        )

        if not question.is_answerable:
            # For NO_ANSWER questions, correct if answer is empty or contains "couldn't find"
            is_no_answer = (
                not predicted_answer.strip() or
                "couldn't find" in predicted_answer.lower() or
                "no answer" in predicted_answer.lower()
            )
            r.no_answer_accuracy = 1.0 if is_no_answer else 0.0
        else:
            r.exact_match = cls.exact_match(predicted_answer, question.expected_evidence)
            r.fragment_f1 = cls.fragment_f1(predicted_answer, question.expected_evidence)

            # Citation accuracy
            if question.expected_pages:
                found_pages = sum(1 for p in question.expected_pages if p in cited_pages)
                r.citation_accuracy = found_pages / len(question.expected_pages)
            else:
                r.citation_accuracy = 1.0

        return r


# ---------------------------------------------------------------------------
# Benchmark Dataset Loader
# ---------------------------------------------------------------------------

class BenchmarkDataset:
    """Loads and validates the benchmark Q&A dataset."""

    DATASET_DIR = Path(__file__).parent / "datasets"
    QUESTIONS_DIR = DATASET_DIR / "questions"

    def __init__(self):
        self.questions: list[BenchmarkQuestion] = []

    def load(self, suite: str = "all") -> list[BenchmarkQuestion]:
        """Load questions from JSON files. suite = 'all' | question_type name."""
        self.questions = []
        files = sorted(self.QUESTIONS_DIR.glob("*.json"))
        if not files:
            print(f"[WARNING] No question files found in {self.QUESTIONS_DIR}")
            print("          Run `python -m benchmarks.harness create-sample` to generate sample questions.")
            return []

        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        q = BenchmarkQuestion.from_dict(item)
                        if suite == "all" or q.question_type.value == suite.upper():
                            self.questions.append(q)
            except Exception as e:
                print(f"[ERROR] Failed to load {f}: {e}")

        print(f"[INFO] Loaded {len(self.questions)} benchmark questions.")
        return self.questions

    def get_by_type(self, qtype: QuestionType) -> list[BenchmarkQuestion]:
        return [q for q in self.questions if q.question_type == qtype]

    def get_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        for q in self.questions:
            stats[q.question_type.value] = stats.get(q.question_type.value, 0) + 1
        return stats

    @staticmethod
    def create_sample_questions() -> list[BenchmarkQuestion]:
        """Creates a minimal sample dataset for initial testing.
        
        Replace with real benchmark questions once documents are available.
        These are structural placeholders — not real benchmark data.
        """
        return [
            # --- FACTUAL ---
            BenchmarkQuestion(
                question_id="f001",
                question="What is the purpose of the TCP handshake?",
                document="sample_networking.pdf",
                question_type=QuestionType.FACTUAL,
                expected_pages=[12],
                expected_evidence=["three-way handshake", "connection establishment"],
                expected_answer_fragments=["handshake"],
                notes="Simple factual lookup"
            ),
            # --- DEFINITION ---
            BenchmarkQuestion(
                question_id="d001",
                question="What is a binary search tree?",
                document="sample_dsa.pdf",
                question_type=QuestionType.DEFINITION,
                expected_pages=[45],
                expected_evidence=["left subtree", "right subtree", "node"],
                expected_answer_fragments=["binary search tree"],
                notes="Definition question"
            ),
            # --- LIST ---
            BenchmarkQuestion(
                question_id="l001",
                question="What are the advantages of Hadoop?",
                document="sample_bigdata.pdf",
                question_type=QuestionType.LIST,
                expected_pages=[24, 25],
                expected_evidence=["scalability", "fault tolerance"],
                expected_answer_fragments=["scalability"],
                notes="Classic list question from master spec"
            ),
            # --- NO_ANSWER ---
            BenchmarkQuestion(
                question_id="na001",
                question="What is the speed of light in vacuum?",
                document="sample_networking.pdf",
                question_type=QuestionType.NO_ANSWER,
                expected_pages=[],
                expected_evidence=[],
                expected_answer_fragments=[],
                is_answerable=False,
                notes="Answer not in document — system must return no-answer"
            ),
            # --- FOLLOW_UP ---
            BenchmarkQuestion(
                question_id="fu001",
                question="What is TCP?",
                document="sample_networking.pdf",
                question_type=QuestionType.DEFINITION,
                expected_pages=[10],
                expected_evidence=["Transmission Control Protocol"],
                expected_answer_fragments=["TCP", "protocol"],
                notes="First turn — establishes TCP as topic"
            ),
            BenchmarkQuestion(
                question_id="fu002",
                question="What are its advantages?",
                document="sample_networking.pdf",
                question_type=QuestionType.FOLLOW_UP,
                expected_pages=[11],
                expected_evidence=["reliable", "ordered"],
                expected_answer_fragments=["reliable"],
                follow_up_of="fu001",
                notes="Follow-up — 'its' should resolve to TCP"
            ),
            # --- NUMERICAL ---
            BenchmarkQuestion(
                question_id="n001",
                question="How many nodes can a Hadoop cluster support?",
                document="sample_bigdata.pdf",
                question_type=QuestionType.NUMERICAL,
                expected_pages=[30],
                expected_evidence=["thousands", "nodes"],
                expected_answer_fragments=["nodes"],
                notes="Numerical answer"
            ),
            # --- AMBIGUOUS ---
            BenchmarkQuestion(
                question_id="a001",
                question="What are the steps?",
                document="sample_networking.pdf",
                question_type=QuestionType.AMBIGUOUS,
                expected_pages=[],
                expected_evidence=[],
                expected_answer_fragments=[],
                is_answerable=False,
                notes="Ambiguous — no context for 'the steps'. System must ask for clarification."
            ),
        ]


# ---------------------------------------------------------------------------
# Regression Detector
# ---------------------------------------------------------------------------

class RegressionDetector:
    """Compares current metrics against a baseline and flags regressions."""

    # Minimum degradation to flag as regression
    THRESHOLDS = {
        "recall_at_5":      0.03,  # 3% drop
        "recall_at_10":     0.03,
        "mrr":              0.02,
        "fragment_f1":      0.03,
        "citation_accuracy": 0.05,
        "no_answer_accuracy": 0.05,
    }

    @classmethod
    def compare(cls, baseline: PhaseMetrics, current: PhaseMetrics) -> list[str]:
        regressions = []
        for metric, threshold in cls.THRESHOLDS.items():
            baseline_val = getattr(baseline, metric, None)
            current_val = getattr(current, metric, None)
            if baseline_val is None or current_val is None:
                continue
            drop = baseline_val - current_val
            if drop > threshold:
                regressions.append(
                    f"{metric}: {baseline_val:.3f} → {current_val:.3f} "
                    f"(drop={drop:.3f}, threshold={threshold:.3f})"
                )
        return regressions


# ---------------------------------------------------------------------------
# Report Generator
# ---------------------------------------------------------------------------

class ReportGenerator:
    """Generates benchmark reports in JSON and plain-text formats."""

    RESULTS_DIR = Path(__file__).parent / "results"

    @classmethod
    def save(cls, metrics: PhaseMetrics, phase: str) -> Path:
        cls.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        ts = metrics.run_timestamp.replace(":", "-").replace(" ", "_")
        path = cls.RESULTS_DIR / f"phase_{phase}_{ts}.json"
        path.write_text(json.dumps(metrics.to_dict(), indent=2), encoding="utf-8")
        return path

    @classmethod
    def print_summary(cls, metrics: PhaseMetrics) -> None:
        print("\n" + "=" * 60)
        print(f"  BENCHMARK RESULTS — Phase {metrics.phase}")
        print(f"  Timestamp: {metrics.run_timestamp}")
        print("=" * 60)
        print(f"  Total Questions : {metrics.total_questions}")
        print(f"  By Type         : {metrics.questions_by_type}")
        print()
        print("  RETRIEVAL")
        print(f"    Recall@1      : {metrics.recall_at_1:.3f}")
        print(f"    Recall@5      : {metrics.recall_at_5:.3f}")
        print(f"    Recall@10     : {metrics.recall_at_10:.3f}")
        print(f"    MRR           : {metrics.mrr:.3f}")
        print(f"    NDCG          : {metrics.ndcg:.3f}")
        print()
        print("  ANSWER")
        print(f"    Exact Match   : {metrics.exact_match:.3f}")
        print(f"    Fragment F1   : {metrics.fragment_f1:.3f}")
        print(f"    Citation Acc. : {metrics.citation_accuracy:.3f}")
        print(f"    No-Answer Acc.: {metrics.no_answer_accuracy:.3f}")
        print(f"    Validation    : {metrics.answer_validation_rate:.3f}")
        print()
        print("  PERFORMANCE")
        print(f"    Avg Retrieval : {metrics.avg_retrieval_latency_ms:.1f} ms")
        print(f"    Avg Answer    : {metrics.avg_answer_latency_ms:.1f} ms")
        print(f"    P95 Total     : {metrics.p95_total_latency_ms:.1f} ms")
        if metrics.regressions:
            print()
            print("  ⚠ REGRESSIONS DETECTED:")
            for r in metrics.regressions:
                print(f"    - {r}")
        print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# Main Harness
# ---------------------------------------------------------------------------

class BenchmarkHarness:
    """
    Main benchmark harness. In Phase 0, this validates the harness itself
    and creates the initial sample dataset.
    
    In later phases, callers inject the pipeline under test via the
    `run_retrieval_benchmark` and `run_answer_benchmark` methods.
    """

    def __init__(self, phase: str = "0"):
        self.phase = phase
        self.dataset = BenchmarkDataset()
        self.computer = MetricComputer()

    def run_retrieval_benchmark(
        self,
        retrieval_fn,  # Callable[[str, str | None], tuple[list[int], list[str], list[float]]]
        suite: str = "all"
    ) -> list[RetrievalResult]:
        """
        Run retrieval benchmark.
        
        retrieval_fn(question_text, document_path) → (pages, chunk_ids, scores)
        """
        questions = self.dataset.load(suite)
        results = []
        for q in questions:
            if not q.is_answerable and q.question_type == QuestionType.NO_ANSWER:
                continue  # Skip no-answer for retrieval benchmark
            t0 = time.perf_counter()
            try:
                pages, chunk_ids, scores = retrieval_fn(q.question, q.document)
            except Exception as e:
                print(f"[ERROR] Retrieval failed for {q.question_id}: {e}")
                pages, chunk_ids, scores = [], [], []
            latency_ms = (time.perf_counter() - t0) * 1000
            result = self.computer.compute_retrieval(q, pages, chunk_ids, scores, latency_ms)
            results.append(result)
        return results

    def run_answer_benchmark(
        self,
        answer_fn,  # Callable[[str, str | None], tuple[str, list[int], str, bool]]
        suite: str = "all"
    ) -> list[AnswerResult]:
        """
        Run answer benchmark.
        
        answer_fn(question_text, document_path) → (answer_text, cited_pages, confidence_level, validation_passed)
        """
        questions = self.dataset.load(suite)
        results = []
        for q in questions:
            t0 = time.perf_counter()
            try:
                answer, pages, confidence, validated = answer_fn(q.question, q.document)
            except Exception as e:
                print(f"[ERROR] Answer failed for {q.question_id}: {e}")
                answer, pages, confidence, validated = "", [], "NO_ANSWER", False
            latency_ms = (time.perf_counter() - t0) * 1000
            result = self.computer.compute_answer(q, answer, pages, confidence, validated, latency_ms)
            results.append(result)
        return results

    def aggregate_retrieval(self, results: list[RetrievalResult]) -> PhaseMetrics:
        import datetime
        metrics = PhaseMetrics(
            phase=self.phase,
            run_timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            total_questions=len(results),
        )
        if not results:
            return metrics
        metrics.recall_at_1  = statistics.mean(r.recall_at_1 for r in results)
        metrics.recall_at_5  = statistics.mean(r.recall_at_5 for r in results)
        metrics.recall_at_10 = statistics.mean(r.recall_at_10 for r in results)
        metrics.mrr          = statistics.mean(r.mrr for r in results)
        metrics.ndcg         = statistics.mean(r.ndcg for r in results)
        latencies = [r.latency_ms for r in results]
        metrics.avg_retrieval_latency_ms = statistics.mean(latencies)
        metrics.p95_total_latency_ms = sorted(latencies)[int(len(latencies) * 0.95)]
        return metrics

    def aggregate_answers(self, results: list[AnswerResult]) -> PhaseMetrics:
        import datetime
        metrics = PhaseMetrics(
            phase=self.phase,
            run_timestamp=datetime.datetime.now().isoformat(timespec="seconds"),
            total_questions=len(results),
        )
        if not results:
            return metrics
        answerable = [r for r in results if r.expected_fragments]
        no_answer  = [r for r in results if not r.expected_fragments]
        if answerable:
            metrics.exact_match        = statistics.mean(r.exact_match for r in answerable)
            metrics.fragment_f1        = statistics.mean(r.fragment_f1 for r in answerable)
            metrics.citation_accuracy  = statistics.mean(r.citation_accuracy for r in answerable)
            metrics.answer_validation_rate = statistics.mean(
                1.0 if r.validation_passed else 0.0 for r in answerable
            )
        if no_answer:
            metrics.no_answer_accuracy = statistics.mean(r.no_answer_accuracy for r in no_answer)
        latencies = [r.answer_latency_ms for r in results]
        metrics.avg_answer_latency_ms = statistics.mean(latencies)
        return metrics

    def create_sample_dataset(self) -> None:
        """Write sample benchmark questions to disk."""
        questions = BenchmarkDataset.create_sample_questions()
        output_dir = BenchmarkDataset.QUESTIONS_DIR
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "sample_questions.json"
        data = [q.to_dict() for q in questions]
        output_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(f"[INFO] Sample dataset written to {output_file}")
        print(f"[INFO] {len(questions)} questions created.")
        print("[INFO] Replace sample_questions.json with real benchmark data before Phase 3.")

    def self_test(self) -> bool:
        """Validate the harness itself. Returns True if all checks pass."""
        print("\n[HARNESS SELF-TEST]")
        passed = 0
        failed = 0

        # Test 1: Metric computation
        pages = [12, 15, 24, 30, 5]
        expected = [12, 24]
        assert MetricComputer.recall_at_k(pages, expected, 1) == 0.5, "recall@1 failed"
        assert MetricComputer.recall_at_k(pages, expected, 5) == 1.0, "recall@5 failed"
        assert MetricComputer.mrr(pages, expected) == 1.0, "mrr failed"
        print("  ✓ Metric computation: recall@k, MRR")
        passed += 1

        # Test 2: Fragment F1
        f1 = MetricComputer.fragment_f1("Hadoop provides scalability and fault tolerance.", ["scalability", "fault tolerance"])
        assert f1 == 1.0, f"fragment_f1 failed: {f1}"
        f1_partial = MetricComputer.fragment_f1("Hadoop provides scalability.", ["scalability", "fault tolerance"])
        assert f1_partial == 0.5, f"fragment_f1 partial failed: {f1_partial}"
        print("  ✓ Fragment F1 scoring")
        passed += 1

        # Test 3: Exact match
        em = MetricComputer.exact_match("scalability and fault tolerance", ["scalability", "fault tolerance"])
        assert em == 1.0, "exact_match failed"
        em_fail = MetricComputer.exact_match("only scalability", ["scalability", "fault tolerance"])
        assert em_fail == 0.0, "exact_match false positive"
        print("  ✓ Exact match scoring")
        passed += 1

        # Test 4: NDCG
        ndcg = MetricComputer.ndcg([12, 24], [12, 24])
        assert ndcg == 1.0, f"ndcg perfect failed: {ndcg}"
        ndcg_none = MetricComputer.ndcg([1, 2, 3], [99, 100])
        assert ndcg_none == 0.0, f"ndcg zero failed: {ndcg_none}"
        print("  ✓ NDCG computation")
        passed += 1

        # Test 5: Sample dataset creation
        samples = BenchmarkDataset.create_sample_questions()
        assert len(samples) >= 8, "Not enough sample questions"
        for s in samples:
            d = s.to_dict()
            s2 = BenchmarkQuestion.from_dict(d)
            assert s2.question_id == s.question_id
        print(f"  ✓ Sample dataset serialization ({len(samples)} questions)")
        passed += 1

        # Test 6: Schema files exist
        schema_dir = Path(__file__).parent.parent / "docs" / "schemas"
        required_schemas = [
            "Document.json", "Page.json", "Section.json", "Chunk.json",
            "IndexMetadata.json", "Evidence.json", "NormalizedQuery.json",
            "ConversationContext.json", "Answer.json", "Citation.json", "ModelRegistry.json"
        ]
        missing = [s for s in required_schemas if not (schema_dir / s).exists()]
        if missing:
            print(f"  ✗ Missing schemas: {missing}")
            failed += 1
        else:
            print(f"  ✓ All {len(required_schemas)} JSON schemas present")
            passed += 1

        # Test 7: Interface files exist
        interface_dir = Path(__file__).parent.parent / "docs" / "interfaces"
        required_interfaces = [
            "DocumentParser.md", "OCRProcessor.md", "StructureAnalyzer.md",
            "Chunker.md", "EmbeddingEngine.md", "LexicalIndexer.md",
            "VectorIndex.md", "HybridRetriever.md", "Ranker.md",
            "QuestionAnalyzer.md", "ConversationResolver.md", "QuestionRouter.md",
            "ExtractiveQA.md", "EvidenceValidator.md", "AnswerBuilder.md",
            "AnswerValidator.md", "ConfidenceEngine.md", "CitationEngine.md",
            "SafePresentationEngine.md"
        ]
        missing_ifaces = [i for i in required_interfaces if not (interface_dir / i).exists()]
        if missing_ifaces:
            print(f"  ✗ Missing interfaces: {missing_ifaces}")
            failed += 1
        else:
            print(f"  ✓ All {len(required_interfaces)} interface contracts present")
            passed += 1

        # Test 8: Licensing doc exists
        licensing = Path(__file__).parent.parent / "docs" / "licensing.md"
        if not licensing.exists():
            print("  ✗ docs/licensing.md missing")
            failed += 1
        else:
            print("  ✓ docs/licensing.md present")
            passed += 1

        # Test 9: DB schema exists
        db_schema = Path(__file__).parent.parent / "storage" / "schema.sql"
        if not db_schema.exists():
            print("  ✗ storage/schema.sql missing")
            failed += 1
        else:
            print("  ✓ storage/schema.sql present")
            passed += 1

        # Test 10: Model registry exists and parses
        registry = Path(__file__).parent.parent / "models" / "registry.json"
        if not registry.exists():
            print("  ✗ models/registry.json missing")
            failed += 1
        else:
            data = json.loads(registry.read_text())
            assert "models" in data, "registry.json missing 'models' key"
            assert len(data["models"]) >= 5, "Expected at least 5 model entries"
            print(f"  ✓ models/registry.json present ({len(data['models'])} models)")
            passed += 1

        print(f"\n  Results: {passed} passed, {failed} failed")
        if failed == 0:
            print("  [HARNESS SELF-TEST PASSED]\n")
        else:
            print("  [HARNESS SELF-TEST FAILED]\n")
        return failed == 0


def main():
    """Entry point for benchmark harness CLI."""
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="ODI Benchmark Harness")
    parser.add_argument("--phase", default="0", help="Phase being benchmarked")
    parser.add_argument("--suite", default="all", help="Question suite to run")
    parser.add_argument("--self-test", action="store_true", help="Run harness self-test")
    parser.add_argument("--create-sample", action="store_true", help="Create sample dataset")
    args = parser.parse_args()

    harness = BenchmarkHarness(phase=args.phase)

    if args.self_test:
        ok = harness.self_test()
        sys.exit(0 if ok else 1)

    if args.create_sample:
        harness.create_sample_dataset()
        sys.exit(0)

    print(f"[INFO] Benchmark harness ready. Phase {args.phase}.")
    print("[INFO] Implement retrieval_fn and answer_fn to run full benchmarks.")
    print("[INFO] Run --self-test to validate harness integrity.")


if __name__ == "__main__":
    main()
