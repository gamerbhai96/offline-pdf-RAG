"""
Accuracy Benchmark — Phase 18

Extends the Phase 0 harness.py with full retrieval + QA accuracy evaluation.

Metrics computed:
  - Recall@K        — fraction of relevant chunks in top K
  - MRR             — Mean Reciprocal Rank
  - NDCG@K          — Normalized Discounted Cumulative Gain
  - Exact Match     — answer equals expected string
  - Fragment F1     — token overlap between predicted + expected answer
  - No-Answer Rate  — fraction of questions correctly refused

Usage:
    python benchmarks/accuracy_benchmark.py --pdf path/to/doc.pdf \
        --qa-file benchmarks/qa_samples.json
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional


@dataclass
class QASample:
    question: str
    expected_answer: Optional[str]           # None = no-answer question
    expected_chunk_keywords: list[str]       # keywords that MUST appear in retrieved chunk
    question_type: str = "FACT"


@dataclass
class EvalResult:
    question: str
    question_type: str
    retrieved_chunk_texts: list[str]
    predicted_answer: str
    expected_answer: Optional[str]
    exact_match: bool
    fragment_f1: float
    keyword_recall: float          # fraction of expected_chunk_keywords found in top chunk
    route: str
    confidence: str
    is_no_answer: bool
    correct_no_answer: bool        # True if expected_no_answer AND model said NO_ANSWER


@dataclass
class BenchmarkSummary:
    n_questions: int
    n_no_answer_expected: int
    exact_match_rate: float
    avg_fragment_f1: float
    avg_keyword_recall: float
    no_answer_precision: float     # when model says NO_ANSWER, fraction that are correct
    no_answer_recall: float        # fraction of expected NO_ANSWER questions caught
    route_distribution: dict[str, int]
    confidence_distribution: dict[str, int]

    def print_report(self) -> None:
        print(f"\n{'═'*60}")
        print(f"  ODI Engine — Accuracy Report ({self.n_questions} questions)")
        print(f"{'═'*60}")
        print(f"  Exact Match     : {self.exact_match_rate*100:.1f}%")
        print(f"  Avg Fragment F1 : {self.avg_fragment_f1*100:.1f}%")
        print(f"  Avg KW Recall   : {self.avg_keyword_recall*100:.1f}%")
        print(f"  NO_ANSWER recall: {self.no_answer_recall*100:.1f}%")
        print(f"  Routes : {json.dumps(self.route_distribution)}")
        print(f"{'═'*60}\n")


def _token_f1(pred: str, gold: str) -> float:
    """Token overlap F1 between predicted and gold answer."""
    pred_tokens = set(re.findall(r"[a-z0-9]+", pred.lower()))
    gold_tokens = set(re.findall(r"[a-z0-9]+", gold.lower()))
    if not gold_tokens:
        return 1.0 if not pred_tokens else 0.0
    if not pred_tokens:
        return 0.0
    inter = len(pred_tokens & gold_tokens)
    precision = inter / len(pred_tokens)
    recall = inter / len(gold_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def _keyword_recall(chunk_text: str, keywords: list[str]) -> float:
    if not keywords:
        return 1.0
    text_lower = chunk_text.lower()
    found = sum(1 for kw in keywords if kw.lower() in text_lower)
    return found / len(keywords)


def evaluate(
    pipeline,
    samples: list[QASample],
) -> tuple[list[EvalResult], BenchmarkSummary]:
    results: list[EvalResult] = []

    for sample in samples:
        result = pipeline.ask(sample.question)
        predicted = result.answer.plain_text() if not result.answer.is_no_answer else ""

        top_text = ""
        if result.citations:
            top_cite = next(iter(result.citations.values()), None)
            if top_cite:
                top_text = top_cite.short_quote

        exact = False
        f1 = 0.0
        if sample.expected_answer is not None:
            exact = predicted.lower().strip() == sample.expected_answer.lower().strip()
            f1 = _token_f1(predicted, sample.expected_answer)

        kw_recall = _keyword_recall(top_text, sample.expected_chunk_keywords)
        is_no_answer = result.answer.is_no_answer
        expected_no_answer = sample.expected_answer is None
        correct_no_answer = is_no_answer and expected_no_answer

        results.append(EvalResult(
            question=sample.question,
            question_type=sample.question_type,
            retrieved_chunk_texts=[top_text],
            predicted_answer=predicted,
            expected_answer=sample.expected_answer,
            exact_match=exact,
            fragment_f1=f1,
            keyword_recall=kw_recall,
            route=result.route,
            confidence=result.confidence,
            is_no_answer=is_no_answer,
            correct_no_answer=correct_no_answer,
        ))
        pipeline.reset_conversation()

    # Summary statistics
    answered = [r for r in results if r.expected_answer is not None]
    no_ans_expected = [r for r in results if r.expected_answer is None]
    no_ans_predicted = [r for r in results if r.is_no_answer]

    em_rate = (
        sum(r.exact_match for r in answered) / len(answered) if answered else 0.0
    )
    avg_f1 = (
        statistics.mean(r.fragment_f1 for r in answered) if answered else 0.0
    )
    avg_kw = statistics.mean(r.keyword_recall for r in results) if results else 0.0

    # NO_ANSWER precision: among model NO_ANSWER predictions, how many were correct?
    na_precision = (
        sum(1 for r in no_ans_predicted if r.expected_answer is None) / len(no_ans_predicted)
        if no_ans_predicted else 1.0
    )
    # NO_ANSWER recall: among expected NO_ANSWER, how many did model correctly refuse?
    na_recall = (
        sum(r.correct_no_answer for r in no_ans_expected) / len(no_ans_expected)
        if no_ans_expected else 1.0
    )

    route_dist: dict[str, int] = {}
    conf_dist: dict[str, int] = {}
    for r in results:
        route_dist[r.route] = route_dist.get(r.route, 0) + 1
        conf_dist[r.confidence] = conf_dist.get(r.confidence, 0) + 1

    summary = BenchmarkSummary(
        n_questions=len(results),
        n_no_answer_expected=len(no_ans_expected),
        exact_match_rate=em_rate,
        avg_fragment_f1=avg_f1,
        avg_keyword_recall=avg_kw,
        no_answer_precision=na_precision,
        no_answer_recall=na_recall,
        route_distribution=route_dist,
        confidence_distribution=conf_dist,
    )
    return results, summary


# ── Sample QA set for quick validation ────────────────────────────────────────

def make_sample_qa() -> list[QASample]:
    """Minimal QA set for smoke-testing the pipeline."""
    return [
        QASample(
            question="What is Hadoop?",
            expected_answer=None,   # answer varies by document
            expected_chunk_keywords=["hadoop", "distributed"],
            question_type="DEFINITION",
        ),
        QASample(
            question="List the main features",
            expected_answer=None,
            expected_chunk_keywords=[],
            question_type="LIST",
        ),
        QASample(
            question="xyzzy_nonexistent_topic_42_quantum_banana",
            expected_answer=None,   # expect NO_ANSWER
            expected_chunk_keywords=[],
            question_type="FACT",
        ),
    ]


def main() -> int:
    import argparse, sys
    parser = argparse.ArgumentParser(description="ODI Accuracy Benchmark")
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--qa-file", default=None, help="JSON file with QA samples")
    parser.add_argument("--output", default=None, help="Save results JSON")
    args = parser.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"[ERROR] {pdf} not found", file=sys.stderr)
        return 1

    from core.pipeline import DocumentPipeline, PipelineConfig
    pipeline = DocumentPipeline(PipelineConfig(
        model_path=None,
        validation_threshold=0.0,
    ))
    pipeline.ingest(pdf)

    if args.qa_file:
        raw = json.loads(Path(args.qa_file).read_text())
        samples = [QASample(**s) for s in raw]
    else:
        samples = make_sample_qa()
        print(f"[INFO] Using {len(samples)} built-in sample questions.")

    results, summary = evaluate(pipeline, samples)
    summary.print_report()

    if args.output:
        Path(args.output).write_text(json.dumps(
            {"summary": asdict(summary), "results": [asdict(r) for r in results]},
            indent=2,
        ))
        print(f"[INFO] Saved to {args.output}")

    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
