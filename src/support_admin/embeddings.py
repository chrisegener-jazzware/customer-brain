"""Embed ticket subjects with caching.

Backend selection:
- If ``OPENAI_API_KEY`` is set → OpenAI ``text-embedding-3-small`` (1536-dim).
- Else → local ``sentence-transformers/all-MiniLM-L6-v2`` (384-dim).

Embeddings are cached per-text in SQLite (see ``db.py``) and L2-normalized so
cosine similarity reduces to a dot product.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Protocol

import numpy as np

from . import db
from .config import get_settings

log = logging.getLogger(__name__)

OPENAI_MODEL = "text-embedding-3-small"
LOCAL_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _hash_text(text: str, model: str) -> str:
    h = hashlib.sha256()
    h.update(model.encode("utf-8"))
    h.update(b"\x00")
    h.update(text.encode("utf-8"))
    return h.hexdigest()


def _normalize(vec: np.ndarray) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    norm = float(np.linalg.norm(arr))
    if norm == 0.0:
        return arr
    return arr / norm


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.size == 0 or b.size == 0 or a.size != b.size:
        return 0.0
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


# ---- backends -----------------------------------------------------------


class _Backend(Protocol):
    name: str

    def embed(self, texts: list[str]) -> list[np.ndarray]: ...


@dataclass
class OpenAIBackend:
    api_key: str
    model: str = OPENAI_MODEL

    @property
    def name(self) -> str:
        return f"openai:{self.model}"

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        from openai import OpenAI  # lazy import

        client = OpenAI(api_key=self.api_key)
        resp = client.embeddings.create(model=self.model, input=texts)
        return [_normalize(np.asarray(item.embedding, dtype=np.float32)) for item in resp.data]


@dataclass
class LocalBackend:
    model_name: str = LOCAL_MODEL
    _model: object | None = None

    @property
    def name(self) -> str:
        return f"local:{self.model_name}"

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # lazy heavy import

            log.info("Loading local embedding model %s", self.model_name)
            self._model = SentenceTransformer(self.model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        model = self._load()
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32)
        return [np.asarray(v, dtype=np.float32) for v in vecs]


@dataclass
class HashingBackend:
    """Deterministic, dependency-free fallback for tests / offline mode.

    NOT for production semantics — only used when neither OpenAI nor
    sentence-transformers is available. Uses a token-hashing trick so that
    overlapping tokens still produce high cosine similarity.
    """

    dim: int = 256
    model_name: str = "hashing-256"

    @property
    def name(self) -> str:
        return f"hashing:{self.dim}"

    def _embed_one(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        # Hash whole tokens AND character 4-grams so semantically similar
        # subjects (heavy token overlap) score ≥ 0.85 cosine. Production
        # backends override this anyway.
        features: list[str] = list(_tokens(text))
        norm_text = (text or "").lower()
        for i in range(max(0, len(norm_text) - 3)):
            features.append("#" + norm_text[i : i + 4])
        for token in features:
            digest = hashlib.md5(token.encode("utf-8")).digest()
            idx = int.from_bytes(digest[:4], "little") % self.dim
            sign = 1.0 if (digest[4] & 1) else -1.0
            vec[idx] += sign
        return _normalize(vec)

    def embed(self, texts: list[str]) -> list[np.ndarray]:
        return [self._embed_one(t) for t in texts]


def _tokens(text: str) -> list[str]:
    return [t for t in (text or "").lower().split() if t]


# ---- service ------------------------------------------------------------


class Embedder:
    """Public facade — handles backend selection and SQLite cache."""

    def __init__(
        self,
        *,
        db_path: Path | None = None,
        backend: _Backend | None = None,
    ) -> None:
        settings = get_settings()
        self.db_path = Path(db_path) if db_path is not None else settings.db_path
        self.backend = backend if backend is not None else _autoselect_backend()

    @property
    def model(self) -> str:
        return self.backend.name

    def embed(self, text: str) -> np.ndarray:
        return self.embed_many([text])[0]

    def embed_many(self, texts: Iterable[str]) -> list[np.ndarray]:
        text_list = [t or "" for t in texts]
        if not text_list:
            return []

        results: list[np.ndarray | None] = [None] * len(text_list)
        misses: list[tuple[int, str, str]] = []  # (index, text, hash)

        with db.connect(self.db_path) as conn:
            for i, text in enumerate(text_list):
                h = _hash_text(text, self.model)
                cached = db.get_cached_embedding(conn, h, self.model)
                if cached is not None:
                    results[i] = cached
                else:
                    misses.append((i, text, h))

            if misses:
                miss_texts = [m[1] for m in misses]
                fresh = self.backend.embed(miss_texts)
                for (idx, _text, h), vec in zip(misses, fresh):
                    arr = np.asarray(vec, dtype=np.float32)
                    db.put_cached_embedding(conn, h, self.model, arr)
                    results[idx] = arr

        # ``results`` is now fully populated.
        return [r for r in results if r is not None]


def _autoselect_backend() -> _Backend:
    settings = get_settings()
    if settings.use_openai_embeddings:
        try:
            import openai  # noqa: F401

            return OpenAIBackend(api_key=settings.openai_api_key)
        except Exception:
            log.warning("OPENAI_API_KEY set but openai package unavailable; falling back")
    try:
        import sentence_transformers  # noqa: F401

        return LocalBackend()
    except Exception:
        log.warning(
            "sentence-transformers unavailable; using deterministic hashing backend "
            "(install extras: pip install '.[embeddings-local]')"
        )
        return HashingBackend()


# Convenience singleton (lazy)
_default: Embedder | None = None


def get_embedder() -> Embedder:
    global _default
    if _default is None:
        _default = Embedder()
    return _default


def reset_embedder_cache() -> None:
    global _default
    _default = None
