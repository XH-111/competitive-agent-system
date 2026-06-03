import hashlib
import logging
import math
import os

logger = logging.getLogger(__name__)


class EmbeddingService:
    """Small embedding facade.

    The default local_hash provider is deterministic and dependency-free so the
    workflow can run before external embedding credentials are configured.
    """

    def __init__(self, *, provider: str | None = None, dimensions: int | None = None):
        self.provider = provider or os.getenv("EMBEDDING_PROVIDER", "local_hash")
        self.dimensions = dimensions or self._int_env("EMBEDDING_DIMENSIONS", 128)

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        try:
            vectors = [self._hash_embedding(text) for text in texts]
            if any(len(vector) != self.dimensions for vector in vectors):
                raise ValueError("Embedding vectors have inconsistent dimensions.")
            return vectors
        except Exception as exc:  # noqa: BLE001
            logger.exception("embedding_failed")
            raise RuntimeError(f"Embedding failed: {exc}") from exc

    def _hash_embedding(self, text: str) -> list[float]:
        vector = [0.0 for _ in range(self.dimensions)]
        tokens = [token for token in text.lower().split() if token]
        if not tokens:
            return vector
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [round(value / norm, 6) for value in vector]

    @staticmethod
    def _int_env(name: str, default: int) -> int:
        try:
            parsed = int(os.getenv(name, str(default)))
        except ValueError:
            return default
        return parsed if parsed > 0 else default

