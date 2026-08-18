"""
Performance & Memory Benchmark — Phase 17

Measures:
  - Ingestion time: parse + chunk + embed + index
  - Query latency: p50 / p95 / p99 / max
  - Memory peak (RSS) during ingestion
  - Throughput: chunks/second indexed

Run:
    python benchmarks/perf_benchmark.py --pdf path/to/doc.pdf --runs 20
"""
from __future__ import annotations

import argparse
import gc
import json
import logging
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class PerfResult:
    pdf_name: str
    page_count: int
    chunk_count: int
    ingestion_ms: float
    query_p50_ms: float
    query_p95_ms: float
    query_p99_ms: float
    query_max_ms: float
    peak_rss_mb: float
    chunks_per_sec: float
    embedding_dim: int
    n_query_runs: int

    def print_report(self) -> None:
        print(f"\n{'═'*60}")
        print(f"  ODI Engine — Performance Report")
        print(f"{'═'*60}")
        print(f"  PDF          : {self.pdf_name}")
        print(f"  Pages        : {self.page_count}")
        print(f"  Chunks       : {self.chunk_count}")
        print(f"  Embedding dim: {self.embedding_dim}")
        print(f"{'─'*60}")
        print(f"  Ingestion    : {self.ingestion_ms:.1f} ms")
        print(f"  Throughput   : {self.chunks_per_sec:.1f} chunks/s")
        print(f"  Peak RSS     : {self.peak_rss_mb:.1f} MB")
        print(f"{'─'*60}")
        print(f"  Query latency ({self.n_query_runs} runs):")
        print(f"    p50  : {self.query_p50_ms:.1f} ms")
        print(f"    p95  : {self.query_p95_ms:.1f} ms")
        print(f"    p99  : {self.query_p99_ms:.1f} ms")
        print(f"    max  : {self.query_max_ms:.1f} ms")
        print(f"{'═'*60}\n")

    def to_dict(self) -> dict:
        return asdict(self)


def _rss_mb() -> float:
    """Current process RSS in MB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except ImportError:
        return 0.0


def run_benchmark(
    pdf_path: Path,
    queries: list[str],
    n_query_runs: int = 20,
    embedding_dim: int = 384,
    verbose: bool = False,
) -> PerfResult:
    from core.pipeline import DocumentPipeline, PipelineConfig

    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING)

    pipeline = DocumentPipeline(PipelineConfig(
        embedding_dimension=embedding_dim,
        model_path=None,          # stub embeddings
        validation_threshold=0.0,
    ))

    rss_before = _rss_mb()
    gc.collect()

    # ── Ingestion ──────────────────────────────────────────────────────────────
    print(f"[BENCH] Ingesting {pdf_path.name} ...", end=" ", flush=True)
    t_start = time.perf_counter()
    doc = pipeline.ingest(pdf_path)
    ingestion_ms = (time.perf_counter() - t_start) * 1000

    rss_after = _rss_mb()
    peak_rss = max(rss_after - rss_before, 0.0)
    chunks_per_sec = (doc.chunk_count / (ingestion_ms / 1000)) if ingestion_ms > 0 else 0
    print(f"✓  {doc.page_count}p / {doc.chunk_count} chunks / {ingestion_ms:.0f}ms")

    # ── Query latency ──────────────────────────────────────────────────────────
    print(f"[BENCH] Running {n_query_runs} query iterations ...", end=" ", flush=True)
    timings: list[float] = []
    for i in range(n_query_runs):
        q = queries[i % len(queries)]
        t0 = time.perf_counter()
        pipeline.ask(q)
        timings.append((time.perf_counter() - t0) * 1000)
        pipeline.reset_conversation()

    timings.sort()
    p50 = statistics.median(timings)
    p95 = timings[int(len(timings) * 0.95)]
    p99 = timings[min(int(len(timings) * 0.99), len(timings) - 1)]
    print(f"✓  p50={p50:.1f}ms p95={p95:.1f}ms")

    return PerfResult(
        pdf_name=pdf_path.name,
        page_count=doc.page_count,
        chunk_count=doc.chunk_count,
        ingestion_ms=ingestion_ms,
        query_p50_ms=p50,
        query_p95_ms=p95,
        query_p99_ms=p99,
        query_max_ms=max(timings),
        peak_rss_mb=peak_rss,
        chunks_per_sec=chunks_per_sec,
        embedding_dim=embedding_dim,
        n_query_runs=n_query_runs,
    )


# ── Calibration targets (Phase 17 thresholds) ─────────────────────────────────

TARGETS = {
    "ingestion_ms_per_page": 500,    # ≤ 500ms/page
    "query_p95_ms": 200,             # ≤ 200ms p95
    "peak_rss_mb": 512,              # ≤ 512 MB RAM
}


def check_targets(result: PerfResult) -> list[str]:
    """Return list of failed targets (empty = all passed)."""
    failures = []
    ms_per_page = result.ingestion_ms / max(result.page_count, 1)
    if ms_per_page > TARGETS["ingestion_ms_per_page"]:
        failures.append(
            f"Ingestion {ms_per_page:.0f}ms/page > target {TARGETS['ingestion_ms_per_page']}ms/page"
        )
    if result.query_p95_ms > TARGETS["query_p95_ms"]:
        failures.append(
            f"Query p95 {result.query_p95_ms:.0f}ms > target {TARGETS['query_p95_ms']}ms"
        )
    if result.peak_rss_mb > TARGETS["peak_rss_mb"] and result.peak_rss_mb > 0:
        failures.append(
            f"Peak RSS {result.peak_rss_mb:.0f}MB > target {TARGETS['peak_rss_mb']}MB"
        )
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ODI Performance Benchmark")
    parser.add_argument("--pdf", required=True, help="PDF file to benchmark")
    parser.add_argument("--runs", type=int, default=20, help="Query iterations")
    parser.add_argument("--dim", type=int, default=384, help="Embedding dimension")
    parser.add_argument("--output", help="Save JSON results to file")
    parser.add_argument("--queries", nargs="+", default=[
        "What is the main topic of this document?",
        "List the key findings.",
        "What are the conclusions?",
        "How many pages does it have?",
        "Summarize the introduction.",
    ])
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    pdf = Path(args.pdf)
    if not pdf.exists():
        print(f"[ERROR] File not found: {pdf}", file=sys.stderr)
        return 1

    result = run_benchmark(pdf, args.queries, args.runs, args.dim, args.verbose)
    result.print_report()

    failures = check_targets(result)
    if failures:
        print("[WARN] Performance targets not met:")
        for f in failures:
            print(f"  ✗ {f}")
    else:
        print("[PASS] All performance targets met ✓")

    if args.output:
        Path(args.output).write_text(json.dumps(result.to_dict(), indent=2))
        print(f"[INFO] Results saved to {args.output}")

    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
