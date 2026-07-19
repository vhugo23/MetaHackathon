"""In-memory ConfigurationPolicyRepository (Day 4B2) — a fast
conformance-test double, never used in production (ADR-0002).

``seed_if_missing`` compares only semantic content (``applies_to``,
``required_acls``) — ``created_at`` is insertion metadata and never
participates in the conflict check, and is never overwritten once stored.
All policies in one call are validated before any is inserted, so a
conflict anywhere in the batch leaves no partial subset inserted from
that call.
"""

from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.persistence.errors import PolicySeedConflictError
from meta_rne.persistence.memory.store import InMemoryStore


def _semantically_equal(existing: ConfigurationPolicy, candidate: ConfigurationPolicy) -> bool:
    return (existing.applies_to, existing.required_acls) == (
        candidate.applies_to,
        candidate.required_acls,
    )


class InMemoryConfigurationPolicyRepository:
    def __init__(self, store: InMemoryStore) -> None:
        self._store = store

    def get_applicable_to_device(self, device_id: str) -> tuple[ConfigurationPolicy, ...]:
        matches = [p for p in self._store.policies.values() if p.applies_to == device_id]
        return tuple(sorted(matches, key=lambda p: p.policy_id))

    def seed_if_missing(self, policies: tuple[ConfigurationPolicy, ...]) -> None:
        for policy in policies:
            existing = self._store.policies.get(policy.policy_id)
            if existing is not None and not _semantically_equal(existing, policy):
                raise PolicySeedConflictError(policy.policy_id)

        for policy in policies:
            if policy.policy_id not in self._store.policies:
                self._store.policies[policy.policy_id] = policy
