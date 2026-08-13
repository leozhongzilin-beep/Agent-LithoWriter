"""Embedder protocol + local sentence-transformers implementation.

The retrieval layer depends only on the `Embedder` protocol — swap the model
or provider without touching retrieval code. The default local implementation
loads sentence-transformers lazily, so importing this module never pulls the
heavy dependency; embedding fails with a clear `EmbedderUnavailable` only when
actually used without the library installed.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

import numpy as np

DEFAULT_MODEL = "BAAI/bge-small-zh-v1.5"


class EmbedderUnavailable(Exception):
    """Raised when an embedder cannot be built (missing library/model)."""


@runtime_checkable
class Embedder(Protocol):
    model_name: str

    def embed(self, texts: list[str]) -> np.ndarray:
        """Return normalized row-vectors (n, dim) for the texts."""
        ...


class LocalEmbedder:
    """Offline local embeddings via sentence-transformers (lazy model load)."""

    def __init__(self, model_name: str | None = None):
        self.model_name = (
            model_name or os.environ.get("KB_EMBED_MODEL", "") or DEFAULT_MODEL
        )
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbedderUnavailable(
                "sentence-transformers is not installed; `pip install "
                "sentence-transformers` to enable vector retrieval, or run "
                "without --embed (lexical search still works)"
            ) from exc
        try:
            self._model = SentenceTransformer(self.model_name)
        except Exception as exc:  # model download / load failure
            raise EmbedderUnavailable(
                f"failed to load embedding model {self.model_name!r}: {exc}"
            ) from exc

    def embed(self, texts: list[str]) -> np.ndarray:
        self._load()
        assert self._model is not None
        return self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )


def get_embedder(model_name: str | None = None) -> Embedder:
    """Factory: the default local embedder. Swap here for other providers."""
    return LocalEmbedder(model_name)
