"""Pure Slice 1 policy seed builder (Day 4B2).

No clock read, no Session, no persistence — just constructs the approved
fixture ``ConfigurationPolicy`` value(s) for a given ``created_at``. Naive
or non-UTC timestamps are rejected by ``ConfigurationPolicy``'s own
``__post_init__`` validation (domain-model.md Section 6); this function
adds no validation of its own.
"""

from datetime import datetime

from meta_rne.domain.config import AclDirection
from meta_rne.domain.policy import ConfigurationPolicy, RequiredAclRule, Severity


def build_slice1_policies(created_at: datetime) -> tuple[ConfigurationPolicy, ...]:
    return (
        ConfigurationPolicy(
            policy_id="policy-acl-external-in",
            applies_to="spine-01",
            required_acls=(
                RequiredAclRule(
                    acl_name="ACL-EXTERNAL-IN",
                    interface_name="GigabitEthernet0/1",
                    direction=AclDirection.IN,
                    severity=Severity.MEDIUM,
                    recommendation="Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
                ),
            ),
            created_at=created_at,
        ),
    )
