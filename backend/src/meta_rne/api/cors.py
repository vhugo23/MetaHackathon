"""CORS origin configuration (Day 6A).

``cors_allowed_origins: tuple[str, ...] = ()`` is ``create_app``'s Python-
level default — an empty tuple disables CORS entirely (no ``CORSMiddleware``
registered at all), so importing/composing ``create_app`` without setting
anything remains non-permissive. Production reads the explicit
``META_RNE_CORS_ALLOWED_ORIGINS`` environment variable (comma-separated);
unset or empty stays disabled, matching the Python-level default.
"""

import os

_CORS_ALLOWED_ORIGINS_ENV_VAR = "META_RNE_CORS_ALLOWED_ORIGINS"


def parse_cors_allowed_origins(raw_value: str) -> tuple[str, ...]:
    """Splits on commas, trims surrounding whitespace from each entry,
    drops empty entries, and preserves every remaining origin exactly as
    given (no normalization beyond that trimming — e.g. no trailing-slash
    stripping, no case-folding)."""
    return tuple(entry.strip() for entry in raw_value.split(",") if entry.strip())


def cors_allowed_origins_from_environment() -> tuple[str, ...]:
    return parse_cors_allowed_origins(os.environ.get(_CORS_ALLOWED_ORIGINS_ENV_VAR, ""))
