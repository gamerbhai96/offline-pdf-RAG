"""
ODI Engine — Command Line Interface (Phase 14)

Usage:
    python -m odi ingest path/to/doc.pdf
    python -m odi ask "What is TCP?"
    python -m odi ask "What is TCP?" --doc doc-id-here
    python -m odi list
    python -m odi reset
    python -m odi benchmark --query "What is Hadoop?" --pdf path/to/test.pdf

All state is persisted in .odi_store/ (SQLite + index files).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _get_store_dir() -> Path:
    store = Path(".odi_store")
    store.mkdir(exist_ok=True)
    return store


def cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest one or more PDF files into the index."""
    from core.pipeline import DocumentPipeline, PipelineConfig
    from storage.database import Database, DocumentRecord, ChunkRecord

    store = _get_store_dir()
    db = Database(store / "odi.db")
    pipeline = DocumentPipeline(PipelineConfig(index_dir=store / "index"))

    # Load existing index state if any (reload documents into memory)
    existing = db.list_documents()
    if existing:
        print(f"[ODI] {len(existing)} document(s) already in store.")

    paths = [Path(p) for p in args.paths]
    for path in paths:
        if not path.exists():
            print(f"[ERROR] File not found: {path}", file=sys.stderr)
            continue

        # Check if already ingested (by file hash)
        from core.document.models import ParsedDocument
        file_hash = ParsedDocument.compute_hash(path)
        existing_doc = db.get_document_by_hash(file_hash)
        if existing_doc and not args.force:
            print(f"[SKIP] {path.name} already ingested (id={existing_doc.document_id[:8]})")
            continue

        print(f"[INGEST] {path.name} ...", end=" ", flush=True)
        t0 = time.perf_counter()
        try:
            doc = pipeline.ingest(path)
            elapsed = (time.perf_counter() - t0) * 1000

            # Persist to DB
            db.insert_document(DocumentRecord(
                document_id=doc.document_id,
                file_path=str(path.resolve()),
                file_hash=file_hash,
                title=doc.title,
                page_count=doc.page_count,
                chunk_count=doc.chunk_count,
                embedding_model_id=pipeline._embedding_engine.model_id,
            ))
            chunks_from_doc = pipeline._chunks.get(doc.document_id, [])
            db.insert_chunks([
                ChunkRecord(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    page_id=c.page_id,
                    text=c.text,
                    token_count=c.token_count,
                    chunk_index=c.chunk_index,
                    strategy=c.strategy.value,
                    section_id=c.section_id,
                    parent_chunk_id=c.parent_chunk_id,
                )
                for c in chunks_from_doc
            ])
            print(f"✓  {doc.page_count}p / {doc.chunk_count} chunks / {elapsed:.0f}ms")
        except Exception as exc:
            print(f"✗  FAILED: {exc}", file=sys.stderr)

    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    """Answer a question against ingested documents."""
    from core.pipeline import DocumentPipeline, PipelineConfig
    from storage.database import Database

    store = _get_store_dir()
    db = Database(store / "odi.db")
    existing = db.list_documents()

    if not existing:
        print("[ERROR] No documents ingested. Run: python -m odi ingest <pdf>", file=sys.stderr)
        return 1

    pipeline = DocumentPipeline(PipelineConfig(index_dir=store / "index"))

    # Re-ingest documents into in-memory index (needed after restart)
    # In a production app this would be loaded from saved index files
    if not pipeline.documents:
        for doc_rec in existing:
            p = Path(doc_rec.file_path)
            if p.exists():
                try:
                    pipeline.ingest(p)
                except Exception as exc:
                    print(f"[WARN] Could not reload {p.name}: {exc}", file=sys.stderr)

    if not pipeline.documents:
        print("[ERROR] No documents could be loaded into memory.", file=sys.stderr)
        return 1

    result = pipeline.ask(args.question, document_id=getattr(args, "doc", None))

    # Display answer
    print(f"\n{'─'*60}")
    print(f"Q: {args.question}")
    print(f"{'─'*60}")
    print(result.plain_text())
    print(f"{'─'*60}")
    print(f"Route: {result.route}  |  Confidence: {result.confidence}  |  {result.elapsed_ms:.0f}ms")

    # Persist to DB as message
    import uuid
    session_id = "cli-session"
    from storage.database import MessageRecord
    db.insert_message(MessageRecord(
        message_id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=args.question,
    ))
    db.insert_message(MessageRecord(
        message_id=str(uuid.uuid4()),
        session_id=session_id,
        role="assistant",
        content=result.answer.plain_text(),
        route=result.route,
        confidence=result.confidence,
    ))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List all ingested documents."""
    from storage.database import Database
    db = Database(_get_store_dir() / "odi.db")
    docs = db.list_documents()
    if not docs:
        print("No documents ingested yet.")
        return 0
    print(f"\n{'ID':10} {'Title':30} {'Pages':6} {'Chunks':7} {'Ingested'}")
    print("─" * 70)
    for d in docs:
        doc_id = d.document_id[:8]
        title = (d.title or "Untitled")[:28]
        print(f"{doc_id:10} {title:30} {d.page_count:6} {d.chunk_count:7}  {d.ingested_at[:10]}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    """Remove all indexed documents."""
    import shutil
    store = _get_store_dir()
    if not args.yes:
        ans = input("This will delete ALL indexed documents. Continue? [y/N] ")
        if ans.lower() != "y":
            print("Aborted.")
            return 0
    shutil.rmtree(store, ignore_errors=True)
    print("Store cleared.")
    return 0


def cmd_benchmark(args: argparse.Namespace) -> int:
    """Run a quick retrieval benchmark on a single PDF + query."""
    import statistics
    from core.pipeline import DocumentPipeline, PipelineConfig

    if not args.pdf or not Path(args.pdf).exists():
        print("[ERROR] --pdf must point to an existing PDF file.", file=sys.stderr)
        return 1

    store = _get_store_dir()
    pipeline = DocumentPipeline(PipelineConfig(index_dir=store / "bench_index"))

    print(f"[BENCH] Ingesting {args.pdf} ...")
    doc = pipeline.ingest(args.pdf)
    print(f"  → {doc.page_count} pages, {doc.chunk_count} chunks")

    queries = args.queries if args.queries else [args.query or "What is this document about?"]
    timings: list[float] = []

    for q in queries:
        result = pipeline.ask(q)
        timings.append(result.elapsed_ms)
        print(f"\n  Q: {q}")
        print(f"  Route: {result.route}  Confidence: {result.confidence}  Time: {result.elapsed_ms:.1f}ms")
        if not result.answer.is_no_answer:
            # Print first 200 chars of answer
            txt = result.answer.plain_text()
            print(f"  A: {txt[:200]}{'...' if len(txt)>200 else ''}")

    if len(timings) > 1:
        print(f"\n[BENCH SUMMARY]  n={len(timings)}  "
              f"avg={statistics.mean(timings):.1f}ms  "
              f"median={statistics.median(timings):.1f}ms  "
              f"max={max(timings):.1f}ms")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="odi",
        description="Offline Document Intelligence Engine",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest PDF file(s)")
    p_ingest.add_argument("paths", nargs="+", help="Path(s) to PDF file(s)")
    p_ingest.add_argument("--force", action="store_true", help="Re-ingest even if already indexed")

    # ask
    p_ask = sub.add_parser("ask", help="Ask a question")
    p_ask.add_argument("question", help="Natural language question")
    p_ask.add_argument("--doc", default=None, help="Restrict to document ID prefix")

    # list
    sub.add_parser("list", help="List ingested documents")

    # reset
    p_reset = sub.add_parser("reset", help="Clear all indexed documents")
    p_reset.add_argument("--yes", action="store_true", help="Skip confirmation")

    # benchmark
    p_bench = sub.add_parser("benchmark", help="Quick performance benchmark")
    p_bench.add_argument("--pdf", required=True, help="PDF to benchmark")
    p_bench.add_argument("--query", default=None, help="Single query")
    p_bench.add_argument("--queries", nargs="+", default=None, help="Multiple queries")

    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    dispatch = {
        "ingest": cmd_ingest,
        "ask": cmd_ask,
        "list": cmd_list,
        "reset": cmd_reset,
        "benchmark": cmd_benchmark,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
