"""Persisted ConfigurationSnapshot domain object (Day 4B1).

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Immutability
enforcement at insertion time (rejecting a second ``add`` for the same
``snapshot_id``) is a repository-level concern (Day 4B2) — this dataclass
only validates its own field-level invariants at construction time,
including that ``raw_text_hash`` actually matches ``raw_config_text`` via
``compute_raw_text_hash``. See docs/domain-model.md Section 4.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from meta_rne.domain.config import NormalizedConfiguration, VendorType

_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def compute_raw_text_hash(raw_config_text: str) -> str:
    """SHA-256 hex digest (lowercase, 64 characters) of ``raw_config_text``
    encoded as UTF-8."""
    return hashlib.sha256(raw_config_text.encode("utf-8")).hexdigest()


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_utc(value: datetime, field_name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must be timezone-aware, got a naive datetime")
    if value.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"{field_name} must be UTC, got offset {value.utcoffset()}")


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    snapshot_id: str
    device_id: str
    vendor: VendorType
    raw_config_text: str
    raw_text_hash: str
    normalized_config: NormalizedConfiguration
    submitted_at: datetime

    def __post_init__(self) -> None:
        _require_non_empty(self.snapshot_id, "ConfigurationSnapshot.snapshot_id")
        _require_non_empty(self.device_id, "ConfigurationSnapshot.device_id")
        _require_non_empty(self.raw_config_text, "ConfigurationSnapshot.raw_config_text")
        _require_utc(self.submitted_at, "ConfigurationSnapshot.submitted_at")

        if not _HASH_PATTERN.fullmatch(self.raw_text_hash):
            raise ValueError(
                "ConfigurationSnapshot.raw_text_hash must be a lowercase 64-character "
                "hexadecimal SHA-256 digest"
            )
        expected = compute_raw_text_hash(self.raw_config_text)
        if self.raw_text_hash != expected:
            raise ValueError(
                "ConfigurationSnapshot.raw_text_hash does not match "
                "compute_raw_text_hash(raw_config_text)"
            )
