"""Embedding cache + similarity tests using the deterministic hashing backend."""

from __future__ import annotations

import sqlite3

import numpy as np

from support_admin import db, embeddings


def test_hashing_backend_self_similarity_is_one():
    backend = embeddings.HashingBackend(dim=128)
    [vec] = backend.embed(["PMS keeps disconnecting from PBX"])
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-5
    assert abs(embeddings.cosine_sim(vec, vec) - 1.0) < 1e-5


def test_similar_subjects_score_higher_than_unrelated():
    backend = embeddings.HashingBackend(dim=256)
    a, b, c = backend.embed(
        [
            "PMS keeps disconnecting from PBX",
            "PMS disconnecting from PBX again",
            "Minibar charges missing on folio",
        ]
    )
    sim_ab = embeddings.cosine_sim(a, b)
    sim_ac = embeddings.cosine_sim(a, c)
    assert sim_ab > sim_ac
    assert sim_ab > 0.5  # they share most tokens


def test_embedding_cache_persists(tmp_path):
    db_path = tmp_path / "emb.db"
    backend = embeddings.HashingBackend(dim=64)
    embedder = embeddings.Embedder(db_path=db_path, backend=backend)

    first = embedder.embed("Tiger PMS link drops every few hours")
    # Cache row should exist.
    with db.connect(db_path) as conn:
        rows = conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
        assert rows == 1

    # New backend instance with a sentinel that would explode if hit again.
    class ExplodingBackend:
        name = backend.name

        def embed(self, texts):  # pragma: no cover - shouldn't be called
            raise AssertionError("cache miss when a hit was expected")

    cached_embedder = embeddings.Embedder(db_path=db_path, backend=ExplodingBackend())
    second = cached_embedder.embed("Tiger PMS link drops every few hours")
    assert np.allclose(first, second)
