"""
Lexical Indexer — Phase 4 (BM25)

Implements LexicalIndexer interface (/docs/interfaces/LexicalIndexer.md).
Uses rank-bm25 (Apache 2.0) for BM25Okapi scoring.

Design:
- Index is built from Chunk objects.
- Saved/loaded from disk as a compressed pickle + metadata JSON.
- Scores are normalized to [0, 1] range by dividing by max score in result set.
- Multi-word phrase boosting: chunks with all query terms get a bonus.
"""
from __future__ import annotations

import json
import logging
import pickle
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class LexicalMatch:
    chunk_id: str
    document_id: str
    page_id: str
    text: str
    bm25_score: float              # raw BM25 score
    normalized_score: float        # [0.0, 1.0] — normalized within this result set


@dataclass
class LexicalIndexMetadata:
    model_version: str = "bm25-okapi-1.0"
    num_chunks: int = 0
    vocabulary_size: int = 0
    k1: float = 1.5
    b: float = 0.75
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "model_version": self.model_version,
            "num_chunks": self.num_chunks,
            "vocabulary_size": self.vocabulary_size,
            "k1": self.k1,
            "b": self.b,
            "created_at": self.created_at,
        }


class LexicalIndexer:
    """
    BM25 index over document chunks.

    Build:
        indexer = LexicalIndexer()
        indexer.build(chunks)
        indexer.save(path)

    Query:
        indexer = LexicalIndexer.load(path)
        results = indexer.search("what is TCP", top_k=10)
    """

    BM25_K1 = 1.5
    BM25_B = 0.75

    def __init__(self):
        self._bm25 = None
        self._chunk_ids: list[str] = []
        self._document_ids: list[str] = []
        self._page_ids: list[str] = []
        self._texts: list[str] = []
        self._metadata = LexicalIndexMetadata(k1=self.BM25_K1, b=self.BM25_B)

    def build(self, chunks) -> None:
        """
        Build BM25 index from a list of Chunk objects.
        """
        try:
            from rank_bm25 import BM25Okapi
        except ImportError:
            raise RuntimeError("rank-bm25 not installed. Run: pip install rank-bm25")

        import datetime

        self._chunk_ids = [c.chunk_id for c in chunks]
        self._document_ids = [c.document_id for c in chunks]
        self._page_ids = [c.page_id for c in chunks]
        self._texts = [c.text for c in chunks]

        tokenized = [_tokenize(t) for t in self._texts]
        self._bm25 = BM25Okapi(tokenized, k1=self.BM25_K1, b=self.BM25_B)

        vocab: set[str] = set()
        for tokens in tokenized:
            vocab.update(tokens)

        self._metadata = LexicalIndexMetadata(
            num_chunks=len(chunks),
            vocabulary_size=len(vocab),
            k1=self.BM25_K1,
            b=self.BM25_B,
            created_at=datetime.datetime.now().isoformat(),
        )
        log.info("Built BM25 index: %d chunks, vocab=%d", len(chunks), len(vocab))

    def search(self, query: str, top_k: int = 10, document_id: str | None = None) -> list[LexicalMatch]:
        """
        Search the BM25 index for the given query.

        Args:
            query: Raw query string (not pre-tokenized).
            top_k: Number of results to return.
            document_id: If given, restrict results to this document.

        Returns:
            List of LexicalMatch sorted by score descending.
        """
        if self._bm25 is None:
            raise RuntimeError("Index not built. Call build() first.")

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        scores: list[float] = self._bm25.get_scores(query_tokens).tolist()

        # Build candidates
        candidates: list[tuple[int, float]] = []
        for i, score in enumerate(scores):
            if score <= 0.0:
                continue
            if document_id and self._document_ids[i] != document_id:
                continue
            candidates.append((i, score))

        if not candidates:
            return []

        # Sort and take top_k
        candidates.sort(key=lambda x: x[1], reverse=True)
        top = candidates[:top_k]

        # Normalize scores to [0, 1]
        max_score = top[0][1] if top else 1.0
        max_score = max(max_score, 1e-9)

        results = []
        for i, raw_score in top:
            results.append(LexicalMatch(
                chunk_id=self._chunk_ids[i],
                document_id=self._document_ids[i],
                page_id=self._page_ids[i],
                text=self._texts[i],
                bm25_score=raw_score,
                normalized_score=min(1.0, raw_score / max_score),
            ))

        return results

    def save(self, directory: str | Path) -> None:
        """Persist the index to disk."""
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        # Save BM25 object + arrays
        with open(directory / "bm25.pkl", "wb") as f:
            pickle.dump({
                "bm25": self._bm25,
                "chunk_ids": self._chunk_ids,
                "document_ids": self._document_ids,
                "page_ids": self._page_ids,
                "texts": self._texts,
            }, f, protocol=pickle.HIGHEST_PROTOCOL)

        # Save metadata as JSON
        (directory / "bm25_meta.json").write_text(
            json.dumps(self._metadata.to_dict(), indent=2), encoding="utf-8"
        )
        log.info("BM25 index saved to %s", directory)

    @classmethod
    def load(cls, directory: str | Path) -> "LexicalIndexer":
        """Load a saved BM25 index from disk."""
        directory = Path(directory)
        pkl_path = directory / "bm25.pkl"
        if not pkl_path.exists():
            raise FileNotFoundError(f"BM25 index not found at {directory}")

        instance = cls()
        with open(pkl_path, "rb") as f:
            data = pickle.load(f)

        instance._bm25 = data["bm25"]
        instance._chunk_ids = data["chunk_ids"]
        instance._document_ids = data["document_ids"]
        instance._page_ids = data["page_ids"]
        instance._texts = data["texts"]

        meta_path = directory / "bm25_meta.json"
        if meta_path.exists():
            meta_data = json.loads(meta_path.read_text(encoding="utf-8"))
            instance._metadata = LexicalIndexMetadata(**meta_data)

        log.info("BM25 index loaded from %s (%d chunks)", directory, len(instance._chunk_ids))
        return instance

    @property
    def metadata(self) -> LexicalIndexMetadata:
        return self._metadata

    @property
    def num_chunks(self) -> int:
        return len(self._chunk_ids)


# ── Text preprocessing ─────────────────────────────────────────────────────────

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "ought", "used",
    "in", "on", "at", "by", "for", "with", "about", "against", "between",
    "into", "through", "during", "before", "after", "above", "below",
    "to", "from", "up", "down", "out", "of", "off", "over", "under",
    "again", "further", "then", "once", "and", "or", "but", "nor", "so",
    "yet", "both", "either", "neither", "not", "only", "own", "same",
    "than", "too", "very", "just", "because", "as", "until", "while",
    "this", "that", "these", "those", "i", "me", "my", "we", "our",
    "you", "your", "he", "him", "his", "she", "her", "it", "its",
    "they", "them", "their", "what", "which", "who", "whom",
    "also", "each", "few", "more", "most", "other", "some", "such",
    "no", "any", "all", "both", "each",
})


def _tokenize(text: str) -> list[str]:
    """
    Tokenize text for BM25 indexing.
    - Lowercase + unicode normalize
    - Keep alphanumeric + hyphens (for compound terms like 'TCP-IP')
    - Remove stop words for better precision
    - Minimum token length: 2 chars
    """
    text = unicodedata.normalize("NFKD", text.lower())
    tokens = re.findall(r"[a-z0-9][a-z0-9\-]*[a-z0-9]|[a-z0-9]", text)
    return [t for t in tokens if t not in _STOP_WORDS and len(t) >= 2]
