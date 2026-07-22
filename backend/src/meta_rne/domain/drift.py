"""Configuration drift value objects — `DriftDetector`'s output shape.

Pure data: no FastAPI, Pydantic, SQLAlchemy, or file I/O. Immutable
``@dataclass(frozen=True, slots=True)`` using ``tuple`` for collections, per
the existing Day 3A/3B engineering constraints. See docs/architecture.md
Section 8 and docs/domain-model.md's `DriftDetector` port signature
(``compare(baseline, current) -> DriftReport``, FR-04, Day 9 Gate 1).

`DriftDetector.compare` itself (the pure function that produces these
values by walking `NormalizedConfiguration` collections) is a later gate —
this module only defines the shape it returns.
"""

from dataclasses import dataclass


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


@dataclass(frozen=True, slots=True)
class DriftEntry:
    """One diff entry within a `DriftReport`.

    ``resource`` identifies the natural-identity-keyed item this entry is
    about (e.g. ``"acl:ACL-EXTERNAL-IN"``, ``"interface:GigabitEthernet0/1"``,
    ``"bgp_neighbor:10.0.0.1"``) — never a raw index, since diffing is by
    identity, not position (architecture.md Section 8).

    ``field`` is the specific scalar field that changed, for a `changed`
    entry (e.g. ``"admin_state"``); it is `None` for a whole-resource
    `added`/`removed` entry, where there is no single field to name.

    An `added` entry has ``old_value=None``; a `removed` entry has
    ``new_value=None``; a `changed` entry has both populated.
    """

    resource: str
    field: str | None
    old_value: str | None
    new_value: str | None

    def __post_init__(self) -> None:
        _require_non_empty(self.resource, "DriftEntry.resource")
        if self.old_value is None and self.new_value is None:
            raise ValueError("DriftEntry.old_value and DriftEntry.new_value must not both be None")


@dataclass(frozen=True, slots=True)
class DriftReport:
    """`DriftDetector.compare`'s structural diff output (FR-04, AC-05/AC-06).

    A device with only one submission has ``current == baseline``, so all
    three tuples are empty (AC-06) — there is no null/no-baseline case.
    """

    added: tuple[DriftEntry, ...]
    removed: tuple[DriftEntry, ...]
    changed: tuple[DriftEntry, ...]
