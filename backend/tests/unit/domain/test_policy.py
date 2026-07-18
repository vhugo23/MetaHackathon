from datetime import UTC, datetime, timedelta, timezone

import pytest

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity

UTC_NOW = datetime(2026, 7, 18, 10, 0, 0, tzinfo=UTC)


def _rule(**overrides: object) -> RequiredAclRule:
    defaults: dict[str, object] = {
        "acl_name": "ACL-EXTERNAL-IN",
        "interface_name": "GigabitEthernet0/1",
        "direction": AclDirection.IN,
        "severity": Severity.MEDIUM,
        "recommendation": "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
    }
    defaults.update(overrides)
    return RequiredAclRule(**defaults)  # type: ignore[arg-type]


def test_configuration_policy__naive_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError):
        ConfigurationPolicy(
            policy_id="policy-acl-external-in",
            applies_to="spine-01",
            required_acls=(_rule(),),
            created_at=datetime(2026, 7, 18, 10, 0, 0),
        )


def test_configuration_policy__non_utc_offset_created_at__raises_value_error() -> None:
    with pytest.raises(ValueError):
        ConfigurationPolicy(
            policy_id="policy-acl-external-in",
            applies_to="spine-01",
            required_acls=(_rule(),),
            created_at=datetime(2026, 7, 18, 10, 0, 0, tzinfo=timezone(timedelta(hours=2))),
        )


def test_configuration_policy__empty_policy_id__raises_value_error() -> None:
    with pytest.raises(ValueError):
        ConfigurationPolicy(
            policy_id="",
            applies_to="spine-01",
            required_acls=(_rule(),),
            created_at=UTC_NOW,
        )


def test_configuration_policy__whitespace_only_applies_to__raises_value_error() -> None:
    with pytest.raises(ValueError):
        ConfigurationPolicy(
            policy_id="policy-acl-external-in",
            applies_to="   ",
            required_acls=(_rule(),),
            created_at=UTC_NOW,
        )


def test_required_acl_rule__empty_acl_name__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _rule(acl_name="")


def test_required_acl_rule__empty_interface_name__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _rule(interface_name="")


def test_required_acl_rule__whitespace_only_recommendation__raises_value_error() -> None:
    with pytest.raises(ValueError):
        _rule(recommendation="   ")
