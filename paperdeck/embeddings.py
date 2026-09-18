from __future__ import annotations

import hashlib
import math
import re
from array import array
from typing import Protocol, Sequence


class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        ...


class HashingProvider:
    name = "hashing-512"
    dim = 512

    _token = re.compile(r"[a-z0-9]+")

    def _features(self, text: str) -> list[str]:
        words = self._token.findall(text.lower())
        bigrams = [f"{a}_{b}" for a, b in zip(words, words[1:])]
        return words + bigrams

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for feature in self._features(text):
                digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
                index = int.from_bytes(digest[:4], "little") % self.dim
                sign = 1.0 if digest[4] & 1 else -1.0
                vec[index] += sign
            vectors.append(_l2_normalize(vec))
        return vectors


class FastEmbedProvider:
    def __init__(self, model_name: str = "BAAI/bge-small-en-v1.5"):
        from fastembed import TextEmbedding

        self._model = TextEmbedding(model_name=model_name)
        self.name = f"fastembed:{model_name}"
        self.dim = 0

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = [list(map(float, v)) for v in self._model.embed(list(texts))]
        if vectors and not self.dim:
            self.dim = len(vectors[0])
        return [_l2_normalize(v) for v in vectors]


class SentenceTransformerProvider:
    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        self.name = f"sentence-transformers:{model_name}"
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )
        return [list(map(float, v)) for v in vectors]


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


_PROVIDER_CACHE: dict[tuple[str, str | None], EmbeddingProvider] = {}


def get_provider(
    prefer: str = "auto", model_name: str | None = None
) -> EmbeddingProvider:
    key = (prefer, model_name)
    cached = _PROVIDER_CACHE.get(key)
    if cached is not None:
        return cached

    provider: EmbeddingProvider
    if prefer in ("auto", "fastembed"):
        try:
            provider = FastEmbedProvider(model_name or "BAAI/bge-small-en-v1.5")
        except ImportError:
            if prefer == "fastembed":
                raise
            provider = _next_provider(prefer, model_name)
    elif prefer in ("sentence-transformers", "st"):
        provider = SentenceTransformerProvider(model_name or "all-MiniLM-L6-v2")
    elif prefer == "hashing":
        provider = HashingProvider()
    else:
        raise ValueError(
            f"Unknown embedding provider '{prefer}'. "
            "Valid: auto, fastembed, sentence-transformers, hashing."
        )

    _PROVIDER_CACHE[key] = provider
    return provider


def _next_provider(prefer: str, model_name: str | None) -> EmbeddingProvider:
    try:
        return SentenceTransformerProvider(model_name or "all-MiniLM-L6-v2")
    except ImportError:
        return HashingProvider()


def pack_vector(vec: Sequence[float]) -> bytes:
    return array("f", vec).tobytes()


def unpack_vector(blob: bytes, dim: int) -> list[float]:
    arr = array("f")
    arr.frombytes(blob)
    return list(arr[:dim])


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
