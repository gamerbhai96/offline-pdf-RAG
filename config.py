"""
Production Configuration — Phase 19

Central configuration for all pipeline parameters.
Reads from environment variables with sensible defaults.
Never raises; always returns a valid config.

Usage:
    from config import load_config
    cfg = load_config()
    pipeline = DocumentPipeline(cfg.pipeline_config())
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ODIConfig:
    # Storage
    store_dir: Path = field(default_factory=lambda: Path(os.getenv("ODI_STORE_DIR", ".odi_store")))
    db_name: str = "odi.db"

    # Embedding model
    model_id: str = field(default_factory=lambda: os.getenv("ODI_MODEL_ID", "bge-small-en-v1.5"))
    model_path: Optional[Path] = field(
        default_factory=lambda: (
            Path(os.getenv("ODI_MODEL_PATH")) if os.getenv("ODI_MODEL_PATH") else None
        )
    )
    embedding_dimension: int = field(
        default_factory=lambda: int(os.getenv("ODI_EMBEDDING_DIM", "384"))
    )

    # Chunking
    max_tokens_per_chunk: int = field(
        default_factory=lambda: int(os.getenv("ODI_MAX_TOKENS", "512"))
    )
    overlap_tokens: int = field(
        default_factory=lambda: int(os.getenv("ODI_OVERLAP_TOKENS", "64"))
    )

    # Retrieval
    hybrid_alpha: float = field(
        default_factory=lambda: float(os.getenv("ODI_HYBRID_ALPHA", "0.5"))
    )
    top_k: int = field(default_factory=lambda: int(os.getenv("ODI_TOP_K", "20")))
    validation_threshold: float = field(
        default_factory=lambda: float(os.getenv("ODI_VALIDATION_THRESHOLD", "0.25"))
    )

    # Server
    host: str = field(default_factory=lambda: os.getenv("ODI_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("ODI_PORT", "8000")))
    log_level: str = field(default_factory=lambda: os.getenv("ODI_LOG_LEVEL", "info"))

    # OCR
    force_ocr: bool = field(
        default_factory=lambda: os.getenv("ODI_FORCE_OCR", "0").lower() in ("1", "true", "yes")
    )

    # Limits
    max_upload_mb: int = field(
        default_factory=lambda: int(os.getenv("ODI_MAX_UPLOAD_MB", "200"))
    )

    def pipeline_config(self):
        from core.pipeline import PipelineConfig
        return PipelineConfig(
            index_dir=self.store_dir / "index",
            model_path=self.model_path,
            embedding_model_id=self.model_id,
            embedding_dimension=self.embedding_dimension,
            max_tokens_per_chunk=self.max_tokens_per_chunk,
            overlap_tokens=self.overlap_tokens,
            hybrid_alpha=self.hybrid_alpha,
            top_k_dense=self.top_k,
            top_k_bm25=self.top_k,
            validation_threshold=self.validation_threshold,
            force_ocr=self.force_ocr,
        )

    @property
    def db_path(self) -> Path:
        return self.store_dir / self.db_name

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def __post_init__(self):
        self.store_dir = Path(self.store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    def summary(self) -> str:
        model = str(self.model_path) if self.model_path else "stub (no ONNX model)"
        return (
            f"ODI Config:\n"
            f"  Store      : {self.store_dir}\n"
            f"  Model      : {self.model_id} @ {model}\n"
            f"  Dim        : {self.embedding_dimension}\n"
            f"  Max tokens : {self.max_tokens_per_chunk} (overlap={self.overlap_tokens})\n"
            f"  Alpha      : {self.hybrid_alpha} (dense/BM25 blend)\n"
            f"  Top-K      : {self.top_k}\n"
            f"  Validation : {self.validation_threshold}\n"
            f"  Server     : http://{self.host}:{self.port}\n"
        )


_config: Optional[ODIConfig] = None


def load_config() -> ODIConfig:
    """Return singleton config. Reads .env if python-dotenv is available."""
    global _config
    if _config is not None:
        return _config

    try:
        from dotenv import load_dotenv
        load_dotenv(override=False)
    except ImportError:
        pass

    _config = ODIConfig()
    return _config


def reset_config() -> None:
    """Reset singleton (for testing)."""
    global _config
    _config = None
