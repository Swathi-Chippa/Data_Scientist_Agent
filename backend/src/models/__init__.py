"""Local reasoning-model integrations."""

from .client import (
    ChatResponse,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DEFAULT_TIMEOUT_S,
    ModelResponseError,
    ModelUnavailableError,
    OllamaClient,
)

__all__ = [
    "ChatResponse",
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "DEFAULT_TIMEOUT_S",
    "ModelResponseError",
    "ModelUnavailableError",
    "OllamaClient",
]
