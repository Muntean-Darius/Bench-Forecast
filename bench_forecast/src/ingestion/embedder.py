"""Embedding generation via Ollama's nomic-embed-text model.

All embeddings in Bench Forecast are produced locally through Ollama.
There is intentionally no Gemini / OpenAI fallback here.

Usage::

    from src.ingestion.embedder import OllamaEmbedder

    embedder = OllamaEmbedder()
    vectors  = embedder.embed_chunks(["chunk 1 text", "chunk 2 text"])

Environment variables (read via Config):
    OLLAMA_BASE_URL   — Ollama HTTP base URL (default: http://localhost:11434)
    EMBEDDING_MODEL   — Model name (default: nomic-embed-text)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Default embedding model — zero-cost, 768-dim, state-of-the-art retrieval
_DEFAULT_MODEL = "nomic-embed-text"
# Seconds to wait between retries on transient Ollama errors
_RETRY_DELAY = 2.0
_MAX_RETRIES = 3


class OllamaEmbedder:
    """Thin wrapper around the Ollama HTTP embeddings API.

    Uses the ``ollama`` Python client library which connects to a running
    Ollama daemon (``ollama serve``).  The model must have been pulled first::

        ollama pull nomic-embed-text

    Args:
        model:    Ollama model name.  Defaults to ``nomic-embed-text``.
        base_url: Ollama daemon base URL.  Reads ``OLLAMA_BASE_URL`` from
                  the environment if not supplied.
    """

    def __init__(
        self,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        from src.core.config import Config  # noqa: PLC0415

        self.model: str = (
            model
            or Config.__dict__.get("EMBEDDING_MODEL", None)  # type: ignore[attr-defined]
            or _DEFAULT_MODEL
        )
        self.base_url: str = base_url or Config.OLLAMA_BASE_URL

        try:
            import ollama as _ollama  # noqa: PLC0415

            self._client = _ollama.Client(host=self.base_url)
        except ModuleNotFoundError as exc:
            raise ImportError(
                "The 'ollama' Python package is required for embedding generation. "
                "Install it with: pip install ollama"
            ) from exc

        logger.info(
            "OllamaEmbedder initialised: model=%s url=%s",
            self.model,
            self.base_url,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed(self, text: str) -> list[float]:
        """Embed a single text string.

        Args:
            text: Plain-text input (one chunk).

        Returns:
            768-dimensional float vector (nomic-embed-text).

        Raises:
            RuntimeError: If Ollama is unreachable after all retries.
        """
        if not text or not text.strip():
            raise ValueError("Cannot embed empty text")

        return self._embed_with_retry(text)

    def embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        """Embed a list of text chunks sequentially.

        Ollama's Python client does not support batch embedding in a single
        call, so we iterate.  For large corpora, consider wrapping this in
        a ThreadPoolExecutor or async generator.

        Args:
            chunks: List of text chunks to embed.

        Returns:
            List of 768-dimensional float vectors, same order as *chunks*.
        """
        if not chunks:
            return []

        vectors: list[list[float]] = []
        for i, chunk in enumerate(chunks):
            logger.debug("Embedding chunk %d/%d (%d chars)", i + 1, len(chunks), len(chunk))
            vectors.append(self._embed_with_retry(chunk))

        logger.info(
            "Embedded %d chunk(s) with model '%s'",
            len(vectors),
            self.model,
        )
        return vectors

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _embed_with_retry(self, text: str) -> list[float]:
        """Call Ollama embeddings API with exponential-backoff retries."""
        last_error: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                response = self._client.embeddings(model=self.model, prompt=text)
                embedding: list[float] = response["embedding"]

                if not embedding:
                    raise ValueError(
                        f"Ollama returned an empty embedding for model '{self.model}'"
                    )

                return embedding

            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Ollama embedding attempt %d/%d failed: %s",
                    attempt,
                    _MAX_RETRIES,
                    exc,
                )
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_DELAY * attempt)

        raise RuntimeError(
            f"Ollama embedding failed after {_MAX_RETRIES} attempts. "
            f"Last error: {last_error}. "
            f"Make sure Ollama is running (`ollama serve`) and the model "
            f"'{self.model}' is pulled (`ollama pull {self.model}`)."
        ) from last_error

    def health_check(self) -> bool:
        """Return True if Ollama is reachable and the model responds.

        Embeds a short test string to verify end-to-end connectivity.
        Logs a warning and returns False on any failure.
        """
        try:
            vec = self._embed_with_retry("health check")
            ok = len(vec) > 0
            if ok:
                logger.info(
                    "OllamaEmbedder health check OK: model=%s dim=%d",
                    self.model,
                    len(vec),
                )
            return ok
        except Exception as exc:
            logger.warning("OllamaEmbedder health check failed: %s", exc)
            return False
