"""
SQLite Persistence Layer — Phase 15

Wraps the storage/schema.sql schema with a typed Python DAO layer.
All reads/writes go through this layer — no raw SQL elsewhere in the codebase.

Matches schema.sql column conventions:
  Document.id, ChatSession.id, Message.id, Chunk.id, etc.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


@dataclass
class DocumentRecord:
    document_id: str
    file_path: str
    file_hash: str
    title: Optional[str]
    page_count: int
    chunk_count: int              # cached, updated separately
    embedding_model_id: Optional[str] = None
    ingested_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class ChunkRecord:
    chunk_id: str
    document_id: str
    page_id: str
    text: str
    token_count: int
    chunk_index: int
    strategy: str
    strategy_version: str = "1.0"
    section_id: Optional[str] = None
    parent_chunk_id: Optional[str] = None
    chunk_type: str = "TEXT"


@dataclass
class MessageRecord:
    message_id: str
    session_id: str
    role: str           # "user" | "system"
    content: str        # stored in raw_query / answer_json
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    route: Optional[str] = None
    confidence: Optional[str] = None
    turn_index: int = 0


class Database:
    """
    SQLite database wrapper. Conforms to schema.sql from Phase 0.
    Thread-safe via per-call connections with WAL mode.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            tables = {row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            if "Document" not in tables:
                ddl = (SCHEMA_PATH.read_text(encoding="utf-8")
                       if SCHEMA_PATH.exists() else _EMBEDDED_DDL)
                conn.executescript(ddl)
                log.info("DB schema initialized at %s", self.db_path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Document ──────────────────────────────────────────────────────────────

    def insert_document(self, doc: DocumentRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO Document
                (id, file_path, file_hash, title, page_count, model_id, created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (doc.document_id, doc.file_path, doc.file_hash, doc.title,
                 doc.page_count, doc.embedding_model_id,
                 doc.ingested_at),
            )

    def get_document(self, document_id: str) -> Optional[DocumentRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM Document WHERE id=?", (document_id,)
            ).fetchone()
        return self._row_to_doc(row) if row else None

    def get_document_by_hash(self, file_hash: str) -> Optional[DocumentRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM Document WHERE file_hash=?", (file_hash,)
            ).fetchone()
        return self._row_to_doc(row) if row else None

    def list_documents(self) -> list[DocumentRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM Document ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_doc(r) for r in rows]

    def delete_document(self, document_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM Document WHERE id=?", (document_id,))

    def _row_to_doc(self, row) -> DocumentRecord:
        d = dict(row)
        return DocumentRecord(
            document_id=d["id"],
            file_path=d["file_path"],
            file_hash=d["file_hash"],
            title=d.get("title"),
            page_count=d.get("page_count", 0),
            chunk_count=0,          # not stored in Document table
            embedding_model_id=d.get("model_id"),
            ingested_at=d.get("created_at", ""),
        )

    # ── Chunk ─────────────────────────────────────────────────────────────────

    def insert_chunks(self, chunks: list[ChunkRecord]) -> None:
        """Insert chunks. Requires a matching Page row — inserts a synthetic one if missing."""
        with self._connect() as conn:
            # Ensure pages exist for each chunk (synthetic page row)
            page_ids_seen: set[str] = set()
            for c in chunks:
                if c.page_id not in page_ids_seen:
                    conn.execute(
                        """INSERT OR IGNORE INTO Page
                        (id, document_id, page_number, raw_text, ocr_used)
                        VALUES (?,?,?,?,0)""",
                        (c.page_id, c.document_id,
                         int(c.page_id) if c.page_id.isdigit() else 0,
                         ""),
                    )
                    page_ids_seen.add(c.page_id)

            conn.executemany(
                """INSERT OR IGNORE INTO Chunk
                (id, document_id, page_id, section_id, text, token_count,
                 chunk_index, parent_chunk_id, chunk_type, strategy, strategy_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                [(c.chunk_id, c.document_id, c.page_id, None,
                  c.text, c.token_count, c.chunk_index, None,
                  c.chunk_type, c.strategy, c.strategy_version)
                 for c in chunks],
            )

    def get_chunks_for_document(self, document_id: str) -> list[ChunkRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM Chunk WHERE document_id=? ORDER BY chunk_index",
                (document_id,)
            ).fetchall()
        return [self._row_to_chunk(r) for r in rows]

    def get_chunk(self, chunk_id: str) -> Optional[ChunkRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM Chunk WHERE id=?", (chunk_id,)
            ).fetchone()
        return self._row_to_chunk(row) if row else None

    def _row_to_chunk(self, row) -> ChunkRecord:
        d = dict(row)
        return ChunkRecord(
            chunk_id=d["id"],
            document_id=d["document_id"],
            page_id=d["page_id"],
            text=d["text"],
            token_count=d.get("token_count", 0),
            chunk_index=d.get("chunk_index", 0),
            strategy=d.get("strategy", "FIXED_OVERLAP"),
            strategy_version=d.get("strategy_version", "1.0"),
            section_id=d.get("section_id"),
            parent_chunk_id=d.get("parent_chunk_id"),
            chunk_type=d.get("chunk_type", "TEXT"),
        )

    # ── Session / Messages ─────────────────────────────────────────────────────

    def ensure_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO ChatSession
                (id, document_scope, created_at) VALUES (?,?,?)""",
                (session_id, "ALL", datetime.now().isoformat()),
            )

    def insert_message(self, msg: MessageRecord) -> None:
        self.ensure_session(msg.session_id)
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO Message
                (id, session_id, turn_index, role, raw_query, answer_json, created_at)
                VALUES (?,?,?,?,?,?,?)""",
                (msg.message_id, msg.session_id, msg.turn_index,
                 "user" if msg.role == "user" else "system",
                 msg.content if msg.role == "user" else None,
                 json.dumps({"text": msg.content, "route": msg.route, "confidence": msg.confidence})
                 if msg.role != "user" else None,
                 msg.created_at),
            )

    def get_messages(self, session_id: str, limit: int = 50) -> list[MessageRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM Message WHERE session_id=?
                ORDER BY created_at DESC LIMIT ?""",
                (session_id, limit),
            ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            content = d.get("raw_query") or ""
            route = conf = None
            if d.get("answer_json"):
                try:
                    aj = json.loads(d["answer_json"])
                    content = aj.get("text", "")
                    route = aj.get("route")
                    conf = aj.get("confidence")
                except Exception:
                    pass
            result.append(MessageRecord(
                message_id=d["id"],
                session_id=d["session_id"],
                role="user" if d["role"] == "user" else "assistant",
                content=content,
                created_at=d["created_at"],
                route=route,
                confidence=conf,
                turn_index=d.get("turn_index", 0),
            ))
        return result[::-1]   # chronological

    # ── IndexMetadata ─────────────────────────────────────────────────────────

    def upsert_index_metadata(self, document_id: str, metadata: dict) -> None:
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT index_id FROM IndexMetadata WHERE document_id=?", (document_id,)
            ).fetchone()
            now = datetime.now().isoformat()
            if existing:
                conn.execute(
                    """UPDATE IndexMetadata SET
                    embedding_model_id=?, is_stale=0
                    WHERE document_id=?""",
                    (metadata.get("embedding_model_id"), document_id),
                )
            else:
                conn.execute(
                    """INSERT INTO IndexMetadata
                    (index_id, index_version, document_id,
                     embedding_model_id, embedding_model_version,
                     embedding_dimension, embedding_preprocessing_version,
                     chunking_strategy, chunking_version,
                     distance_metric, vector_index_type, vector_index_version,
                     is_stale, source_document_hash, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,0,?,?)""",
                    (str(uuid.uuid4()), "1.0", document_id,
                     metadata.get("embedding_model_id", "stub"),
                     metadata.get("embedding_model_version", "1.0"),
                     metadata.get("embedding_dimension", 384),
                     metadata.get("embedding_preprocessing_version", "1.0"),
                     metadata.get("chunking_strategy", "HEADING_AWARE"),
                     metadata.get("chunking_version", "1.0"),
                     "cosine", "brute_force", "1.0",
                     metadata.get("source_document_hash", ""),
                     now),
                )

    def mark_index_stale(self, document_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE IndexMetadata SET is_stale=1 WHERE document_id=?", (document_id,)
            )

    def is_index_stale(self, document_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT is_stale FROM IndexMetadata WHERE document_id=?", (document_id,)
            ).fetchone()
        return bool(row["is_stale"]) if row else True


# ── Embedded fallback DDL ──────────────────────────────────────────────────────
# Only used if schema.sql is not accessible. Kept minimal to avoid drift.
_EMBEDDED_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS Document (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    title TEXT,
    page_count INTEGER NOT NULL DEFAULT 0,
    language TEXT NOT NULL DEFAULT 'en',
    indexed_at TEXT,
    model_id TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Page (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    raw_text TEXT NOT NULL DEFAULT '',
    ocr_used INTEGER NOT NULL DEFAULT 0,
    ocr_confidence REAL,
    has_tables INTEGER DEFAULT 0,
    has_images INTEGER DEFAULT 0,
    width_pts REAL,
    height_pts REAL,
    UNIQUE(document_id, page_number)
);

CREATE TABLE IF NOT EXISTS Section (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL REFERENCES Page(id) ON DELETE CASCADE,
    heading TEXT,
    heading_level INTEGER,
    start_offset INTEGER,
    end_offset INTEGER,
    parent_section_id TEXT REFERENCES Section(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS Chunk (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_id TEXT NOT NULL REFERENCES Page(id) ON DELETE CASCADE,
    section_id TEXT REFERENCES Section(id) ON DELETE SET NULL,
    text TEXT NOT NULL,
    token_count INTEGER,
    start_offset INTEGER,
    end_offset INTEGER,
    bounding_boxes TEXT,
    chunk_index INTEGER,
    parent_chunk_id TEXT REFERENCES Chunk(id) ON DELETE SET NULL,
    chunk_type TEXT NOT NULL DEFAULT 'TEXT',
    strategy TEXT NOT NULL DEFAULT 'FIXED_OVERLAP',
    strategy_version TEXT NOT NULL DEFAULT '1.0'
);

CREATE TABLE IF NOT EXISTS IndexMetadata (
    index_id TEXT PRIMARY KEY,
    index_version TEXT NOT NULL DEFAULT '1.0',
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    embedding_model_id TEXT NOT NULL DEFAULT 'stub',
    embedding_model_version TEXT NOT NULL DEFAULT '1.0',
    embedding_dimension INTEGER NOT NULL DEFAULT 384,
    embedding_preprocessing_version TEXT NOT NULL DEFAULT '1.0',
    chunking_strategy TEXT NOT NULL DEFAULT 'HEADING_AWARE',
    chunking_version TEXT NOT NULL DEFAULT '1.0',
    distance_metric TEXT NOT NULL DEFAULT 'cosine',
    vector_index_type TEXT NOT NULL DEFAULT 'brute_force',
    vector_index_version TEXT NOT NULL DEFAULT '1.0',
    hnsw_M INTEGER,
    hnsw_ef_construction INTEGER,
    hnsw_ef_search INTEGER,
    bm25_index_path TEXT,
    hnsw_index_path TEXT,
    is_stale INTEGER NOT NULL DEFAULT 0,
    stale_reason TEXT,
    created_at TEXT NOT NULL,
    source_document_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ChatSession (
    id TEXT PRIMARY KEY,
    document_scope TEXT NOT NULL DEFAULT 'ALL',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ConversationContext (
    session_id TEXT PRIMARY KEY REFERENCES ChatSession(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL DEFAULT 0,
    resolved_topics TEXT,
    entity_stack TEXT,
    last_document_scope TEXT,
    last_format_preference TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES ChatSession(id) ON DELETE CASCADE,
    turn_index INTEGER NOT NULL DEFAULT 0,
    role TEXT NOT NULL DEFAULT 'user',
    raw_query TEXT,
    normalized_query TEXT,
    answer_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS Citation (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES Message(id) ON DELETE CASCADE,
    chunk_id TEXT NOT NULL,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    section_heading TEXT,
    highlighted_text TEXT,
    bounding_boxes TEXT
);

CREATE TABLE IF NOT EXISTS Bookmark (
    id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ModelRegistry (
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    model_format TEXT NOT NULL DEFAULT 'onnx',
    quantization TEXT NOT NULL DEFAULT 'none',
    checksum TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT 'MIT',
    commercial_use INTEGER NOT NULL DEFAULT 1,
    role TEXT NOT NULL DEFAULT 'embedding',
    PRIMARY KEY (model_id, model_version, quantization)
);

CREATE TABLE IF NOT EXISTS SchemaVersion (
    version TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

INSERT OR IGNORE INTO SchemaVersion VALUES ('1.0.0', datetime('now'));
"""
