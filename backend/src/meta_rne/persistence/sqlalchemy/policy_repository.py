"""SQLAlchemy/PostgreSQL ConfigurationPolicyRepository (Day 4B2).

Accepts an already-open ``Session`` — never creates, commits, rolls back,
or closes it. ``seed_if_missing`` treats the whole call as one all-or-
nothing operation: every policy is validated/staged inside a single
SAVEPOINT, so a conflict anywhere in the batch (raised from inside the
``with`` block) triggers SQLAlchemy's automatic ROLLBACK TO SAVEPOINT,
discarding anything else this call had already inserted — never a
partially-seeded batch. Semantic equivalence for the no-op case compares
only ``applies_to``/``required_acls`` (deserialized back into domain rule
tuples); ``created_at`` is insertion metadata and is never compared or
overwritten.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from meta_rne.domain.policy import ConfigurationPolicy
from meta_rne.persistence.errors import PersistenceError, PolicySeedConflictError
from meta_rne.persistence.serialization import (
    required_acl_rules_from_json,
    required_acl_rules_to_json,
)
from meta_rne.persistence.sqlalchemy.models import _ConfigurationPolicyModel


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("database timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _to_domain(model: _ConfigurationPolicyModel) -> ConfigurationPolicy:
    return ConfigurationPolicy(
        policy_id=model.policy_id,
        applies_to=model.applies_to,
        required_acls=required_acl_rules_from_json(model.required_acls),
        created_at=_to_utc(model.created_at),
    )


def _semantically_equal(existing: ConfigurationPolicy, candidate: ConfigurationPolicy) -> bool:
    return (existing.applies_to, existing.required_acls) == (
        candidate.applies_to,
        candidate.required_acls,
    )


class SqlAlchemyConfigurationPolicyRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_applicable_to_device(self, device_id: str) -> tuple[ConfigurationPolicy, ...]:
        stmt = (
            self._session.query(_ConfigurationPolicyModel)
            .filter(_ConfigurationPolicyModel.applies_to == device_id)
            .order_by(_ConfigurationPolicyModel.policy_id)
        )
        return tuple(_to_domain(model) for model in stmt.all())

    def seed_if_missing(self, policies: tuple[ConfigurationPolicy, ...]) -> None:
        if not policies:
            return
        try:
            with self._session.begin_nested():
                self._seed_batch(policies)
        except IntegrityError:
            # A concurrent transaction committed one of these policy_ids
            # between our SELECT and INSERT. The savepoint above already
            # rolled back this call's staged inserts; retry once inside a
            # fresh savepoint so the now-visible row is reconciled
            # (no-op or conflict) instead of re-inserted blindly.
            try:
                with self._session.begin_nested():
                    self._seed_batch(policies)
            except IntegrityError as exc:
                raise PersistenceError(
                    "unexpected persistence failure while seeding configuration policies"
                ) from exc

    def _seed_batch(self, policies: tuple[ConfigurationPolicy, ...]) -> None:
        for policy in policies:
            model = self._session.get(_ConfigurationPolicyModel, policy.policy_id)
            if model is None:
                self._session.add(
                    _ConfigurationPolicyModel(
                        policy_id=policy.policy_id,
                        applies_to=policy.applies_to,
                        required_acls=required_acl_rules_to_json(policy.required_acls),
                        created_at=policy.created_at,
                    )
                )
                self._session.flush()
                continue

            if not _semantically_equal(_to_domain(model), policy):
                raise PolicySeedConflictError(policy.policy_id)
            # semantically identical -> no-op; stored created_at untouched
