-- ============================================================
-- Offline Document Intelligence Engine — SQLite Schema
-- Version: 1.0.0
-- Updated: Phase 0
-- ============================================================
-- Notes:
--   All IDs are UUID strings (TEXT type in SQLite).
--   Booleans are stored as INTEGER (0/1).
--   Timestamps are ISO 8601 strings.
--   JSON arrays (bounding_boxes, etc.) are stored as TEXT (JSON).
--   ON DELETE CASCADE ensures document deletion removes all related data.
-- ============================================================

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
PRAGMA auto_vacuum = INCREMENTAL;

-- ============================================================
-- Documents
-- ============================================================
CREATE TABLE IF NOT EXISTS Document (
    id          TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_hash   TEXT NOT NULL UNIQUE,   -- SHA-256
    title       TEXT,
    page_count  INTEGER NOT NULL,
    language    TEXT NOT NULL DEFAULT 'en',
    indexed_at  TEXT,
    model_id    TEXT,
    created_at  TEXT NOT NULL
);

-- ============================================================
-- Pages
-- ============================================================
CREATE TABLE IF NOT EXISTS Page (
    id              TEXT PRIMARY KEY,
    document_id     TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number     INTEGER NOT NULL,
    raw_text        TEXT NOT NULL DEFAULT '',
    ocr_used        INTEGER NOT NULL DEFAULT 0,
    ocr_confidence  REAL,
    has_tables      INTEGER NOT NULL DEFAULT 0,
    has_images      INTEGER NOT NULL DEFAULT 0,
    width_pts       REAL,
    height_pts      REAL,
    UNIQUE(document_id, page_number)
);

CREATE INDEX IF NOT EXISTS idx_page_document ON Page(document_id);

-- ============================================================
-- Sections
-- ============================================================
CREATE TABLE IF NOT EXISTS Section (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_id          TEXT NOT NULL REFERENCES Page(id) ON DELETE CASCADE,
    heading          TEXT,
    heading_level    INTEGER,
    start_offset     INTEGER,
    end_offset       INTEGER,
    parent_section_id TEXT REFERENCES Section(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_section_document ON Section(document_id);
CREATE INDEX IF NOT EXISTS idx_section_page     ON Section(page_id);

-- ============================================================
-- Chunks
-- ============================================================
CREATE TABLE IF NOT EXISTS Chunk (
    id               TEXT PRIMARY KEY,
    document_id      TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_id          TEXT NOT NULL REFERENCES Page(id) ON DELETE CASCADE,
    section_id       TEXT REFERENCES Section(id) ON DELETE SET NULL,
    text             TEXT NOT NULL,
    token_count      INTEGER,
    start_offset     INTEGER,
    end_offset       INTEGER,
    bounding_boxes   TEXT,             -- JSON: [{x0,y0,x1,y1,page}]
    chunk_index      INTEGER,
    parent_chunk_id  TEXT REFERENCES Chunk(id) ON DELETE SET NULL,
    chunk_type       TEXT NOT NULL DEFAULT 'TEXT'
                       CHECK(chunk_type IN ('TEXT', 'TABLE', 'LIST', 'HEADING')),
    strategy         TEXT NOT NULL,
    strategy_version TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chunk_document ON Chunk(document_id);
CREATE INDEX IF NOT EXISTS idx_chunk_page     ON Chunk(page_id);
CREATE INDEX IF NOT EXISTS idx_chunk_section  ON Chunk(section_id);

-- ============================================================
-- Index Metadata (versioned — Amendment 4)
-- ============================================================
CREATE TABLE IF NOT EXISTS IndexMetadata (
    index_id                        TEXT PRIMARY KEY,
    index_version                   TEXT NOT NULL,
    document_id                     TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,

    embedding_model_id              TEXT NOT NULL,
    embedding_model_version         TEXT NOT NULL,
    embedding_dimension             INTEGER NOT NULL,
    embedding_preprocessing_version TEXT NOT NULL,

    chunking_strategy               TEXT NOT NULL,
    chunking_version                TEXT NOT NULL,

    distance_metric                 TEXT NOT NULL DEFAULT 'cosine'
                                      CHECK(distance_metric IN ('cosine','dot','l2')),

    vector_index_type               TEXT NOT NULL
                                      CHECK(vector_index_type IN ('brute_force','hnsw')),
    vector_index_version            TEXT NOT NULL,

    hnsw_M                          INTEGER,
    hnsw_ef_construction            INTEGER,
    hnsw_ef_search                  INTEGER,

    bm25_index_path                 TEXT,
    hnsw_index_path                 TEXT,

    is_stale                        INTEGER NOT NULL DEFAULT 0,
    stale_reason                    TEXT,

    created_at                      TEXT NOT NULL,
    source_document_hash            TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_indexmeta_document ON IndexMetadata(document_id);

-- ============================================================
-- Model Registry (Amendment 5)
-- ============================================================
CREATE TABLE IF NOT EXISTS ModelRegistry (
    model_id              TEXT NOT NULL,
    model_version         TEXT NOT NULL,
    model_format          TEXT NOT NULL CHECK(model_format IN ('onnx','tflite','safetensors')),
    quantization          TEXT NOT NULL DEFAULT 'none' CHECK(quantization IN ('none','int8','fp16')),
    tokenizer_version     TEXT,
    checksum              TEXT NOT NULL,
    query_prefix          TEXT,
    passage_prefix        TEXT,
    normalization         TEXT CHECK(normalization IN ('L2','none')),
    preprocessing_version TEXT,
    dimension             INTEGER,
    max_sequence_length   INTEGER,
    license               TEXT NOT NULL,
    commercial_use        INTEGER NOT NULL DEFAULT 1,
    download_url          TEXT,
    local_path            TEXT,
    role                  TEXT NOT NULL
                            CHECK(role IN ('embedding','reranker','extractive_qa','ner')),
    verified_at           TEXT,
    PRIMARY KEY (model_id, model_version, quantization)
);

-- ============================================================
-- Chat Sessions
-- ============================================================
CREATE TABLE IF NOT EXISTS ChatSession (
    id             TEXT PRIMARY KEY,
    document_scope TEXT NOT NULL DEFAULT 'ALL',
    created_at     TEXT NOT NULL
);

-- ============================================================
-- Conversation Context (Amendment — missing in original plan)
-- ============================================================
CREATE TABLE IF NOT EXISTS ConversationContext (
    session_id            TEXT PRIMARY KEY
                            REFERENCES ChatSession(id) ON DELETE CASCADE,
    turn_index            INTEGER NOT NULL DEFAULT 0,
    resolved_topics       TEXT,          -- JSON: ["TCP", "UDP"]
    entity_stack          TEXT,          -- JSON: [{text, type, turn, mention_count}]
    last_document_scope   TEXT,
    last_format_preference TEXT,
    updated_at            TEXT NOT NULL
);

-- ============================================================
-- Messages
-- ============================================================
CREATE TABLE IF NOT EXISTS Message (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES ChatSession(id) ON DELETE CASCADE,
    turn_index       INTEGER NOT NULL,
    role             TEXT NOT NULL CHECK(role IN ('user','system')),
    raw_query        TEXT,
    normalized_query TEXT,
    answer_json      TEXT,              -- serialized Answer schema
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_message_session ON Message(session_id);

-- ============================================================
-- Citations
-- ============================================================
CREATE TABLE IF NOT EXISTS Citation (
    id               TEXT PRIMARY KEY,
    message_id       TEXT NOT NULL REFERENCES Message(id) ON DELETE CASCADE,
    chunk_id         TEXT NOT NULL REFERENCES Chunk(id) ON DELETE CASCADE,
    document_id      TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number      INTEGER NOT NULL,
    section_heading  TEXT,
    highlighted_text TEXT,
    bounding_boxes   TEXT              -- JSON: [{x0,y0,x1,y1,page}]
);

CREATE INDEX IF NOT EXISTS idx_citation_message  ON Citation(message_id);
CREATE INDEX IF NOT EXISTS idx_citation_document ON Citation(document_id);

-- ============================================================
-- Bookmarks
-- ============================================================
CREATE TABLE IF NOT EXISTS Bookmark (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES Document(id) ON DELETE CASCADE,
    page_number INTEGER NOT NULL,
    note        TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookmark_document ON Bookmark(document_id);

-- ============================================================
-- Schema Version (for migration tracking)
-- ============================================================
CREATE TABLE IF NOT EXISTS SchemaVersion (
    version     TEXT NOT NULL,
    applied_at  TEXT NOT NULL
);

INSERT OR IGNORE INTO SchemaVersion(version, applied_at)
VALUES ('1.0.0', datetime('now'));
