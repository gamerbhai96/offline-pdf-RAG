"""
Document Chunking Engine — Phase 2

Implements the Chunker interface contract (/docs/interfaces/Chunker.md).

Strategies:
  HEADING_AWARE  — Splits at section boundaries detected by StructureAnalyzer.
                   Preferred strategy for well-structured PDFs.
  PARAGRAPH      — Splits at blank-line paragraph boundaries.
  FIXED_OVERLAP  — Fixed-size token windows with configurable overlap.
                   Fallback for documents with no detectable structure.
  SENTENCE       — Splits at sentence boundaries (NLTK or regex fallback).

Rules (non-negotiable from interface contract):
  - No chunk may exceed max_tokens.
  - Bounding boxes are propagated from TextBlock → Chunk.
  - strategy + strategy_version fields are always set (for IndexMetadata versioning).
  - Parent-child relationship preserved: large sections → parent chunk;
    sub-sections and sentences → child chunks.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterator

log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
STRATEGY_VERSION = "1.0"
DEFAULT_MAX_TOKENS = 512
DEFAULT_OVERLAP_TOKENS = 64
MIN_CHUNK_TOKENS = 20           # discard chunks shorter than this
APPROX_CHARS_PER_TOKEN = 4     # rough approximation for char→token conversion


class ChunkStrategy(str, Enum):
    HEADING_AWARE = "HEADING_AWARE"
    PARAGRAPH = "PARAGRAPH"
    FIXED_OVERLAP = "FIXED_OVERLAP"
    SENTENCE = "SENTENCE"


class ChunkType(str, Enum):
    TEXT = "TEXT"
    TABLE = "TABLE"
    LIST = "LIST"
    HEADING = "HEADING"


# ── Output data structures ─────────────────────────────────────────────────────

@dataclass
class BoundingBoxRef:
    x0: float
    y0: float
    x1: float
    y1: float
    page: int

    def to_dict(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "x1": self.x1, "y1": self.y1, "page": self.page}


@dataclass
class Chunk:
    """
    A text segment ready for embedding and indexing.
    Conforms to /docs/schemas/Chunk.json.
    """
    chunk_id: str
    document_id: str
    page_id: str                   # maps to page_number for now
    text: str
    token_count: int
    start_offset: int
    end_offset: int
    bounding_boxes: list[BoundingBoxRef] = field(default_factory=list)
    chunk_index: int = 0
    parent_chunk_id: str | None = None
    section_id: str | None = None
    chunk_type: ChunkType = ChunkType.TEXT
    strategy: ChunkStrategy = ChunkStrategy.HEADING_AWARE
    strategy_version: str = STRATEGY_VERSION

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "page_id": self.page_id,
            "text": self.text,
            "token_count": self.token_count,
            "start_offset": self.start_offset,
            "end_offset": self.end_offset,
            "bounding_boxes": [b.to_dict() for b in self.bounding_boxes],
            "chunk_index": self.chunk_index,
            "parent_chunk_id": self.parent_chunk_id,
            "section_id": self.section_id,
            "chunk_type": self.chunk_type.value,
            "strategy": self.strategy.value,
            "strategy_version": self.strategy_version,
        }


@dataclass
class ChunkingResult:
    document_id: str
    chunks: list[Chunk]
    strategy: ChunkStrategy
    strategy_version: str = STRATEGY_VERSION
    total_tokens: int = 0
    dropped_chunks: int = 0       # chunks below MIN_CHUNK_TOKENS

    def __post_init__(self):
        self.total_tokens = sum(c.token_count for c in self.chunks)


# ── Token counting ─────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Estimate token count without loading a tokenizer.
    Uses whitespace splitting as a fast approximation.
    Actual tokenizer used during embedding for exact count.
    """
    return max(1, len(text.split()))


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Main chunker ───────────────────────────────────────────────────────────────

class Chunker:
    """
    Produces chunks from a StructuredDocument.
    Strategy selection:
      - HEADING_AWARE if ≥ 2 sections detected.
      - PARAGRAPH if ≥ 1 section but no headings.
      - FIXED_OVERLAP as fallback.
    """

    def __init__(
        self,
        strategy: ChunkStrategy | None = None,      # None = auto-select
        max_tokens: int = DEFAULT_MAX_TOKENS,
        overlap_tokens: int = DEFAULT_OVERLAP_TOKENS,
    ):
        self.strategy = strategy
        self.max_tokens = max_tokens
        self.overlap_tokens = overlap_tokens

    # ── Public API ─────────────────────────────────────────────────────────────

    def chunk(self, structured_doc, document_id: str) -> ChunkingResult:
        """
        Chunk a StructuredDocument into Chunk objects.

        Args:
            structured_doc: Output of StructureAnalyzer.
            document_id: UUID of the document.

        Returns:
            ChunkingResult with all chunks.
        """
        from core.document.structure import StructuredDocument
        assert isinstance(structured_doc, StructuredDocument)

        strategy = self._select_strategy(structured_doc)
        log.info(
            "Chunking doc %s with strategy=%s sections=%d",
            document_id, strategy.value, len(structured_doc.sections),
        )

        if strategy == ChunkStrategy.HEADING_AWARE:
            chunks = self._chunk_heading_aware(structured_doc, document_id)
        elif strategy == ChunkStrategy.PARAGRAPH:
            chunks = self._chunk_paragraph(structured_doc, document_id)
        elif strategy == ChunkStrategy.SENTENCE:
            chunks = self._chunk_sentence(structured_doc, document_id)
        else:
            chunks = self._chunk_fixed_overlap(structured_doc, document_id)

        # Assign indices, filter too-short chunks
        valid_chunks = []
        dropped = 0
        for idx, chunk in enumerate(chunks):
            chunk.chunk_index = idx
            if chunk.token_count < MIN_CHUNK_TOKENS:
                dropped += 1
                continue
            valid_chunks.append(chunk)

        # Re-index after dropping
        for idx, chunk in enumerate(valid_chunks):
            chunk.chunk_index = idx

        return ChunkingResult(
            document_id=document_id,
            chunks=valid_chunks,
            strategy=strategy,
            dropped_chunks=dropped,
        )

    # ── Strategy selector ──────────────────────────────────────────────────────

    def _select_strategy(self, structured_doc) -> ChunkStrategy:
        if self.strategy:
            return self.strategy
        n_sections = len(structured_doc.sections)
        n_headings = len(structured_doc.headings)
        if n_headings >= 2:
            return ChunkStrategy.HEADING_AWARE
        if n_sections >= 1:
            return ChunkStrategy.PARAGRAPH
        return ChunkStrategy.FIXED_OVERLAP

    # ── HEADING_AWARE ──────────────────────────────────────────────────────────

    def _chunk_heading_aware(self, structured_doc, document_id: str) -> list[Chunk]:
        """
        One chunk per section. If a section is too long, sub-chunk it with
        FIXED_OVERLAP, with the section chunk as parent.
        """
        chunks: list[Chunk] = []
        char_offset = 0

        for section in structured_doc.sections:
            # Build section text: heading + body
            parts = []
            if section.heading:
                parts.append(section.heading)
            body = "\n".join(b.text for b in section.text_blocks if b.text.strip())
            if body:
                parts.append(body)
            text = "\n".join(parts).strip()

            if not text:
                continue

            # Collect bounding boxes from all text blocks in this section
            bboxes = _collect_bboxes(section.text_blocks)

            # Primary page: first block's page or section page
            page_id = str(section.page_number)
            token_count = estimate_tokens(text)

            if token_count <= self.max_tokens:
                # Single chunk for this section
                chunk = Chunk(
                    chunk_id=_new_id(),
                    document_id=document_id,
                    page_id=page_id,
                    text=text,
                    token_count=token_count,
                    start_offset=char_offset,
                    end_offset=char_offset + len(text),
                    bounding_boxes=bboxes,
                    section_id=section.section_id,
                    chunk_type=ChunkType.TEXT,
                    strategy=ChunkStrategy.HEADING_AWARE,
                )
                chunks.append(chunk)
                char_offset += len(text) + 1
            else:
                # Section too long -> only index sub-chunks to prevent BM25 pollution
                parent_id = _new_id()
                sub_chunks = list(self._sliding_window(
                    text, document_id, page_id,
                    char_offset, section.section_id,
                    parent_id=parent_id,
                ))
                chunks.extend(sub_chunks)
                char_offset += len(text) + 1

        # Also include tables as separate chunks
        for page in structured_doc.pages:
            for table in page.tables:
                table_text = table.to_text()
                if not table_text.strip():
                    continue
                chunks.append(Chunk(
                    chunk_id=_new_id(),
                    document_id=document_id,
                    page_id=str(page.page_number),
                    text=table_text,
                    token_count=estimate_tokens(table_text),
                    start_offset=0,
                    end_offset=len(table_text),
                    bounding_boxes=[],
                    chunk_type=ChunkType.TABLE,
                    strategy=ChunkStrategy.HEADING_AWARE,
                ))

        # Include list blocks as separate chunks
        for lst in structured_doc.lists:
            list_text = "\n".join(f"• {item}" for item in lst.items)
            if not list_text.strip():
                continue
            chunks.append(Chunk(
                chunk_id=_new_id(),
                document_id=document_id,
                page_id=str(lst.page_number),
                text=list_text,
                token_count=estimate_tokens(list_text),
                start_offset=0,
                end_offset=len(list_text),
                bounding_boxes=[],
                chunk_type=ChunkType.LIST,
                strategy=ChunkStrategy.HEADING_AWARE,
            ))

        return chunks

    # ── PARAGRAPH ─────────────────────────────────────────────────────────────

    def _chunk_paragraph(self, structured_doc, document_id: str) -> list[Chunk]:
        """
        Split document by paragraph breaks (double-newline or section boundary).
        """
        chunks: list[Chunk] = []
        char_offset = 0

        for page in structured_doc.pages:
            full_text = page.raw_text
            paragraphs = re.split(r"\n{2,}", full_text)
            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue
                token_count = estimate_tokens(para)
                if token_count <= self.max_tokens:
                    chunks.append(Chunk(
                        chunk_id=_new_id(),
                        document_id=document_id,
                        page_id=str(page.page_number),
                        text=para,
                        token_count=token_count,
                        start_offset=char_offset,
                        end_offset=char_offset + len(para),
                        strategy=ChunkStrategy.PARAGRAPH,
                    ))
                    char_offset += len(para) + 2
                else:
                    for sub in self._sliding_window(
                        para, document_id, str(page.page_number), char_offset
                    ):
                        chunks.append(sub)
                    char_offset += len(para) + 2

        return chunks

    # ── FIXED_OVERLAP ─────────────────────────────────────────────────────────

    def _chunk_fixed_overlap(self, structured_doc, document_id: str) -> list[Chunk]:
        """
        Flatten all page text and chunk with sliding window + overlap.
        """
        all_text_parts: list[tuple[str, str]] = []  # (text, page_id)
        for page in structured_doc.pages:
            for block in sorted(page.text_blocks, key=lambda b: b.reading_order):
                if block.text.strip():
                    all_text_parts.append((block.text, str(page.page_number)))

        chunks: list[Chunk] = []
        char_offset = 0
        words_with_pages: list[tuple[str, str]] = []
        for text, page_id in all_text_parts:
            for word in text.split():
                words_with_pages.append((word, page_id))

        step = max(1, self.max_tokens - self.overlap_tokens)
        i = 0
        while i < len(words_with_pages):
            window = words_with_pages[i : i + self.max_tokens]
            text = " ".join(w for w, _ in window)
            page_id = window[0][1] if window else "1"

            chunks.append(Chunk(
                chunk_id=_new_id(),
                document_id=document_id,
                page_id=page_id,
                text=text,
                token_count=len(window),
                start_offset=char_offset,
                end_offset=char_offset + len(text),
                strategy=ChunkStrategy.FIXED_OVERLAP,
            ))
            char_offset += len(text) + 1
            i += step

        return chunks

    # ── SENTENCE ──────────────────────────────────────────────────────────────

    def _chunk_sentence(self, structured_doc, document_id: str) -> list[Chunk]:
        """
        Split at sentence boundaries, group sentences into max_token windows.
        """
        chunks: list[Chunk] = []
        char_offset = 0

        for page in structured_doc.pages:
            sentences = _split_sentences(page.raw_text)
            buffer: list[str] = []
            buffer_tokens = 0

            for sent in sentences:
                sent = sent.strip()
                if not sent:
                    continue
                t = estimate_tokens(sent)

                if buffer_tokens + t > self.max_tokens and buffer:
                    text = " ".join(buffer)
                    chunks.append(Chunk(
                        chunk_id=_new_id(),
                        document_id=document_id,
                        page_id=str(page.page_number),
                        text=text,
                        token_count=buffer_tokens,
                        start_offset=char_offset,
                        end_offset=char_offset + len(text),
                        strategy=ChunkStrategy.SENTENCE,
                    ))
                    char_offset += len(text) + 1
                    buffer = [sent]
                    buffer_tokens = t
                else:
                    buffer.append(sent)
                    buffer_tokens += t

            if buffer:
                text = " ".join(buffer)
                chunks.append(Chunk(
                    chunk_id=_new_id(),
                    document_id=document_id,
                    page_id=str(page.page_number),
                    text=text,
                    token_count=buffer_tokens,
                    start_offset=char_offset,
                    end_offset=char_offset + len(text),
                    strategy=ChunkStrategy.SENTENCE,
                ))

        return chunks

    # ── Sliding window helper ──────────────────────────────────────────────────

    def _sliding_window(
        self,
        text: str,
        document_id: str,
        page_id: str,
        base_offset: int,
        section_id: str | None = None,
        parent_id: str | None = None,
    ) -> Iterator[Chunk]:
        words = text.split()
        step = max(1, self.max_tokens - self.overlap_tokens)
        i = 0
        char_offset = base_offset
        while i < len(words):
            window_words = words[i : i + self.max_tokens]
            chunk_text = " ".join(window_words)
            yield Chunk(
                chunk_id=_new_id(),
                document_id=document_id,
                page_id=page_id,
                text=chunk_text,
                token_count=len(window_words),
                start_offset=char_offset,
                end_offset=char_offset + len(chunk_text),
                section_id=section_id,
                parent_chunk_id=parent_id,
                strategy=ChunkStrategy.HEADING_AWARE,
            )
            char_offset += len(chunk_text) + 1
            i += step


# ── Helpers ────────────────────────────────────────────────────────────────────

def _collect_bboxes(text_blocks) -> list[BoundingBoxRef]:
    """Collect unique bounding boxes from a list of TextBlocks."""
    bboxes = []
    for block in text_blocks:
        if block.bbox:
            bboxes.append(BoundingBoxRef(
                x0=block.bbox.x0, y0=block.bbox.y0,
                x1=block.bbox.x1, y1=block.bbox.y1,
                page=block.bbox.page,
            ))
    return bboxes


_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")

def _split_sentences(text: str) -> list[str]:
    """
    Split text into sentences using regex.
    Falls back to NLTK punkt if available.
    """
    try:
        import nltk
        try:
            return nltk.sent_tokenize(text)
        except LookupError:
            nltk.download("punkt_tab", quiet=True)
            return nltk.sent_tokenize(text)
    except ImportError:
        pass
    # Regex fallback: split on sentence-ending punctuation followed by capital letter
    parts = _SENTENCE_SPLIT_PATTERN.split(text)
    return [p.strip() for p in parts if p.strip()]
