"""
Vector Index — Phase 5

Implements VectorIndex interface (/docs/interfaces/VectorIndex.md).

Two implementations with automatic selection:
  HNSWIndex     — hnswlib (Apache 2.0). Fast ANN. Requires C++ build tools to install.
  BruteForceIndex — Pure NumPy cosine search. Always available. Used as fallback.

The index stores:
  - Embeddings as float32 numpy array.
  - chunk_id list (parallel to embeddings).
  - Metadata JSON (model_id, dimension, metric, hnsw params).

Per-document indexing: each document has its own index file(s).
This keeps RAM bounded on Android and simplifies deletion.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class VectorMatch:
    chunk_id: str
    dense_score: float          # cosine similarity [0, 1] (after normalization)
    rank: int                   # 1-indexed rank in this result set


# ── Brute-force index (pure NumPy, always available) ───────────────────────────

class BruteForceIndex:
    """
    Exact cosine similarity search using NumPy matrix multiplication.
    Assumes embeddings are L2-normalized (unit vectors), so cosine sim = dot product.

    Suitable for:
    - Testing without hnswlib
    - Small corpora (< 5,000 chunks)
    - Low-end Android devices
    """

    INDEX_TYPE = "brute_force"

    def __init__(self, dimension: int, metric: str = "cosine"):
        self.dimension = dimension
        self.metric = metric
        self._embeddings: np.ndarray | None = None    # shape: (N, D)
        self._chunk_ids: list[str] = []
        self._metadata: dict = {}

    def add(self, chunk_ids: list[str], embeddings: np.ndarray) -> None:
        """
        Add embeddings to the index.
        embeddings: (N, D) float32 array, L2-normalized.
        """
        assert embeddings.shape[1] == self.dimension, (
            f"Dimension mismatch: expected {self.dimension}, got {embeddings.shape[1]}"
        )
        if self._embeddings is None:
            self._embeddings = embeddings.astype(np.float32)
        else:
            self._embeddings = np.vstack([self._embeddings, embeddings.astype(np.float32)])
        self._chunk_ids.extend(chunk_ids)

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[VectorMatch]:
        """
        Find top_k nearest chunks by cosine similarity.
        query_vector: (D,) L2-normalized vector.
        """
        if self._embeddings is None or len(self._chunk_ids) == 0:
            return []

        q = query_vector.astype(np.float32).reshape(1, -1)
        scores = (self._embeddings @ q.T).squeeze()          # (N,) cosine similarities

        if scores.ndim == 0:
            scores = scores.reshape(1)

        k = min(top_k, len(scores))
        top_indices = np.argpartition(scores, -k)[-k:]
        top_indices = top_indices[np.argsort(scores[top_indices])[::-1]]

        results = []
        for rank, idx in enumerate(top_indices, 1):
            results.append(VectorMatch(
                chunk_id=self._chunk_ids[int(idx)],
                dense_score=float(np.clip(scores[int(idx)], 0.0, 1.0)),
                rank=rank,
            ))
        return results

    @property
    def num_chunks(self) -> int:
        return len(self._chunk_ids)

    def save(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        if self._embeddings is not None:
            np.save(str(directory / "embeddings.npy"), self._embeddings)
        (directory / "chunk_ids.json").write_text(
            json.dumps(self._chunk_ids), encoding="utf-8"
        )
        (directory / "vector_meta.json").write_text(
            json.dumps({
                "index_type": self.INDEX_TYPE,
                "dimension": self.dimension,
                "metric": self.metric,
                "num_chunks": self.num_chunks,
            }), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: str | Path) -> "BruteForceIndex":
        directory = Path(directory)
        meta = json.loads((directory / "vector_meta.json").read_text(encoding="utf-8"))
        idx = cls(dimension=meta["dimension"], metric=meta.get("metric", "cosine"))
        emb_path = directory / "embeddings.npy"
        if emb_path.exists():
            idx._embeddings = np.load(str(emb_path))
        ids_path = directory / "chunk_ids.json"
        if ids_path.exists():
            idx._chunk_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        return idx


# ── HNSW index wrapper (optional — requires hnswlib) ──────────────────────────

class HNSWIndex:
    """
    Approximate nearest neighbour search via hnswlib (Apache 2.0).
    Falls back to BruteForceIndex if hnswlib is not installed.

    HNSW parameters (calibrated in Phase 17):
      M = 16          — connections per node (higher = better recall, more RAM)
      ef_construction = 200   — quality of index construction
      ef_search = 50          — quality of query-time search
    """

    INDEX_TYPE = "hnsw"
    DEFAULT_M = 16
    DEFAULT_EF_CONSTRUCTION = 200
    DEFAULT_EF_SEARCH = 50

    def __init__(
        self,
        dimension: int,
        metric: str = "cosine",
        M: int = DEFAULT_M,
        ef_construction: int = DEFAULT_EF_CONSTRUCTION,
        ef_search: int = DEFAULT_EF_SEARCH,
    ):
        self.dimension = dimension
        self.metric = metric
        self.M = M
        self.ef_construction = ef_construction
        self.ef_search = ef_search
        self._index = None
        self._chunk_ids: list[str] = []

        space = "cosine" if metric == "cosine" else "l2"
        try:
            import hnswlib
            self._index = hnswlib.Index(space=space, dim=dimension)
            self._index.init_index(
                max_elements=100_000,
                ef_construction=ef_construction,
                M=M,
            )
            self._index.set_ef(ef_search)
            self._hnswlib_available = True
        except ImportError:
            log.warning(
                "hnswlib not installed (requires C++ build tools on Windows). "
                "Falling back to BruteForceIndex."
            )
            self._hnswlib_available = False
            self._fallback = BruteForceIndex(dimension, metric)

    def add(self, chunk_ids: list[str], embeddings: np.ndarray) -> None:
        if not self._hnswlib_available:
            self._fallback.add(chunk_ids, embeddings)
            self._chunk_ids.extend(chunk_ids)
            return

        start_id = len(self._chunk_ids)
        ids = list(range(start_id, start_id + len(chunk_ids)))
        self._index.add_items(embeddings.astype(np.float32), ids)
        self._chunk_ids.extend(chunk_ids)

    def search(self, query_vector: np.ndarray, top_k: int = 10) -> list[VectorMatch]:
        if not self._hnswlib_available:
            return self._fallback.search(query_vector, top_k)

        if not self._chunk_ids:
            return []

        k = min(top_k, len(self._chunk_ids))
        labels, distances = self._index.knn_query(
            query_vector.astype(np.float32).reshape(1, -1), k=k
        )
        results = []
        for rank, (label, dist) in enumerate(zip(labels[0], distances[0]), 1):
            # hnswlib cosine distance = 1 - cosine_similarity
            score = float(np.clip(1.0 - dist, 0.0, 1.0))
            results.append(VectorMatch(
                chunk_id=self._chunk_ids[int(label)],
                dense_score=score,
                rank=rank,
            ))
        return results

    @property
    def num_chunks(self) -> int:
        return len(self._chunk_ids)

    def save(self, directory: str | Path) -> None:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)

        if not self._hnswlib_available:
            self._fallback.save(directory)
            return

        self._index.save_index(str(directory / "hnsw.bin"))
        (directory / "chunk_ids.json").write_text(
            json.dumps(self._chunk_ids), encoding="utf-8"
        )
        (directory / "vector_meta.json").write_text(
            json.dumps({
                "index_type": self.INDEX_TYPE,
                "dimension": self.dimension,
                "metric": self.metric,
                "num_chunks": self.num_chunks,
                "M": self.M,
                "ef_construction": self.ef_construction,
                "ef_search": self.ef_search,
            }), encoding="utf-8"
        )

    @classmethod
    def load(cls, directory: str | Path) -> "HNSWIndex":
        directory = Path(directory)
        meta = json.loads((directory / "vector_meta.json").read_text(encoding="utf-8"))
        idx = cls(
            dimension=meta["dimension"],
            metric=meta.get("metric", "cosine"),
            M=meta.get("M", cls.DEFAULT_M),
            ef_construction=meta.get("ef_construction", cls.DEFAULT_EF_CONSTRUCTION),
            ef_search=meta.get("ef_search", cls.DEFAULT_EF_SEARCH),
        )
        if idx._hnswlib_available:
            idx._index.load_index(str(directory / "hnsw.bin"))
        else:
            idx._fallback = BruteForceIndex.load(directory)
        ids_path = directory / "chunk_ids.json"
        if ids_path.exists():
            idx._chunk_ids = json.loads(ids_path.read_text(encoding="utf-8"))
        return idx


# ── Factory function ───────────────────────────────────────────────────────────

def create_vector_index(
    dimension: int,
    prefer_hnsw: bool = True,
    metric: str = "cosine",
    **kwargs,
) -> HNSWIndex | BruteForceIndex:
    """
    Create the best available vector index.
    prefer_hnsw=True (default): use HNSW if hnswlib is installed, else BruteForce.
    """
    if prefer_hnsw:
        return HNSWIndex(dimension=dimension, metric=metric, **kwargs)
    return BruteForceIndex(dimension=dimension, metric=metric)
