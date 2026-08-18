"""
Embedding Engine — Phase 3

Implements EmbeddingEngine interface (/docs/interfaces/EmbeddingEngine.md).

Design rules (non-negotiable from architecture):
- EmbeddingEngine OWNS all query/passage prefix logic. Callers NEVER add prefixes manually.
- Models run via ONNX Runtime (no PyTorch required at inference time).
- Embeddings are L2-normalized before return.
- A lightweight stub mode (random unit vectors) enables testing without model files.
- Model registry drives prefix and normalization settings per model_id.
"""
from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np

log = logging.getLogger(__name__)

REGISTRY_PATH = Path(__file__).parent.parent.parent / "models" / "registry.json"
DEFAULT_DIMENSION = 384


# ── Registry helpers ────────────────────────────────────────────────────────────

def _load_registry() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"models": []}


def _get_model_config(model_id: str) -> dict | None:
    reg = _load_registry()
    for m in reg.get("models", []):
        if m["model_id"] == model_id:
            return m
    return None


# ── Data types ─────────────────────────────────────────────────────────────────

@dataclass
class EmbeddingResult:
    chunk_id: str
    embedding: np.ndarray          # shape: (dimension,), L2-normalized
    model_id: str
    model_version: str
    preprocessing_version: str

    def to_list(self) -> list[float]:
        return self.embedding.tolist()


# ── ONNX inference session wrapper ─────────────────────────────────────────────

class ONNXEmbeddingSession:
    """Wraps an ONNX Runtime InferenceSession for a sentence embedding model."""

    def __init__(self, model_path: str | Path, providers: list[str] | None = None):
        try:
            import onnxruntime as ort
            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 4) // 2)
            self._session = ort.InferenceSession(
                str(model_path),
                sess_options=sess_opts,
                providers=providers or ["CPUExecutionProvider"],
            )
            input_names = [i.name for i in self._session.get_inputs()]
            self._has_token_type = "token_type_ids" in input_names
            log.info("Loaded ONNX model from %s", model_path)
        except ImportError:
            raise RuntimeError("onnxruntime not installed. Run: pip install onnxruntime")

    def run(self, input_ids: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        """Run inference and return token embeddings (batch_size, seq_len, hidden)."""
        feeds: dict = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if self._has_token_type:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        outputs = self._session.run(None, feeds)
        return outputs[0]  # last_hidden_state


def _mean_pool(token_embeddings: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
    """Mean-pool token embeddings weighted by attention mask."""
    mask = attention_mask[..., np.newaxis].astype(np.float32)
    summed = (token_embeddings * mask).sum(axis=1)
    count = mask.sum(axis=1).clip(min=1e-9)
    return summed / count


def _l2_normalize(v: np.ndarray) -> np.ndarray:
    """L2-normalize a batch of vectors."""
    norms = np.linalg.norm(v, axis=-1, keepdims=True).clip(min=1e-12)
    return v / norms


# ── Main embedding engine ───────────────────────────────────────────────────────

class EmbeddingEngine:
    """
    Produces L2-normalized dense embeddings for text chunks and queries.

    Stub mode (stub=True): returns random unit vectors without loading any model.
    ONNX mode: requires model_path to an ONNX INT8 sentence transformer.

    The EmbeddingEngine owns all prefix logic. Callers pass raw text only.
    """

    PREPROCESSING_VERSION = "1.0"

    def __init__(
        self,
        model_id: str = "bge-small-en-v1.5",
        model_path: str | Path | None = None,
        stub: bool = False,
        dimension: int = DEFAULT_DIMENSION,
        max_length: int = 512,
    ):
        self.model_id = model_id
        self.stub = stub
        self.dimension = dimension
        self.max_length = max_length
        self._session: ONNXEmbeddingSession | None = None
        self._tokenizer = None

        # Load model config from registry
        cfg = _get_model_config(model_id) or {}
        self._query_prefix: str = cfg.get("query_prefix") or ""
        self._passage_prefix: str = cfg.get("passage_prefix") or ""
        self.model_version: str = cfg.get("model_version", "unknown")
        # Only use registry dimension when caller did not specify a custom one
        if cfg.get("dimension") and dimension == DEFAULT_DIMENSION:
            self.dimension = cfg["dimension"]

        if not stub and model_path:
            self._load(Path(model_path))

    def _load(self, model_path: Path) -> None:
        """Load ONNX session and HuggingFace tokenizer."""
        self._session = ONNXEmbeddingSession(model_path)
        try:
            from tokenizers import Tokenizer
            tok_path = model_path.parent / "tokenizer.json"
            if tok_path.exists():
                self._tokenizer = Tokenizer.from_file(str(tok_path))
                self._tokenizer.enable_truncation(max_length=self.max_length)
                self._tokenizer.enable_padding()
                log.info("Loaded tokenizer from %s", tok_path)
            else:
                raise RuntimeError(f"tokenizer.json not found at {tok_path}")
        except ImportError:
            raise RuntimeError("tokenizers library not installed. Cannot use real embeddings.")

    def is_loaded(self) -> bool:
        return self.stub or (self._session is not None)

    # ── Public API ─────────────────────────────────────────────────────────────

    def embed_passages(self, texts: Sequence[str], chunk_ids: Sequence[str]) -> list[EmbeddingResult]:
        """
        Embed passage texts (e.g., chunks from documents).
        Passage prefix is applied internally.
        """
        prefixed = [self._passage_prefix + t for t in texts]
        embeddings = self._encode(prefixed)
        return [
            EmbeddingResult(
                chunk_id=chunk_ids[i],
                embedding=embeddings[i],
                model_id=self.model_id,
                model_version=self.model_version,
                preprocessing_version=self.PREPROCESSING_VERSION,
            )
            for i in range(len(texts))
        ]

    def embed_query(self, query: str) -> np.ndarray:
        """
        Embed a query string for retrieval.
        Query prefix is applied internally. Returns (dimension,) L2-normalized vector.
        """
        prefixed = self._query_prefix + query
        result = self._encode([prefixed])
        return result[0]

    def embed_queries(self, queries: Sequence[str]) -> np.ndarray:
        """Batch embed multiple queries. Returns (N, dimension) array."""
        prefixed = [self._query_prefix + q for q in queries]
        return self._encode(prefixed)

    # ── Internal ───────────────────────────────────────────────────────────────

    def _encode(self, texts: list[str]) -> np.ndarray:
        """Encode a list of texts → (N, dimension) L2-normalized array."""
        if self.stub:
            return self._stub_encode(len(texts))

        if self._session is None:
            raise RuntimeError(
                f"EmbeddingEngine not loaded. Call with model_path or use stub=True."
            )

        if self._tokenizer is None:
            raise RuntimeError("Tokenizer not loaded, cannot encode text.")

        encoded = self._tokenizer.encode_batch(texts)
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        token_embeddings = self._session.run(input_ids, attention_mask)
        pooled = _mean_pool(token_embeddings, attention_mask)
        return _l2_normalize(pooled)

    def _stub_encode(self, n: int) -> np.ndarray:
        """Return random unit vectors of correct dimension. Used in tests without real model files."""
        rng = np.random.default_rng(seed=42)
        vecs = rng.standard_normal((n, self.dimension)).astype(np.float32)
        return _l2_normalize(vecs)

    @property
    def query_prefix(self) -> str:
        return self._query_prefix

    @property
    def passage_prefix(self) -> str:
        return self._passage_prefix
