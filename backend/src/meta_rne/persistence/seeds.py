"""Pure Slice 1 (+ Day 8A-D) policy seed builder (Day 4B2).

No clock read, no Session, no persistence — just constructs the approved
fixture ``ConfigurationPolicy`` value(s) for a given ``created_at``. Naive
or non-UTC timestamps are rejected by ``ConfigurationPolicy``'s own
``__post_init__`` validation (domain-model.md Section 6); this function
adds no validation of its own.

Day 8A-D extends the returned tuple to two exact-match, device-specific
policies — the original spine-01 (Cisco) policy, unchanged, and a second,
independent leaf-02 (Arista) policy expressing the same logical
required-ACL requirement. This is deliberately **not** a shared/wildcard
applicability mechanism: each policy still matches exactly one
``device_id``, and ``PolicyEvaluator`` (unchanged) still does plain
``applies_to == device_id`` equality. The function keeps its Day 4B2 name
(accepted technical debt) rather than being renamed or split into a
second seed call.
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
        ConfigurationPolicy(
            policy_id="policy-acl-external-in-leaf-02",
            applies_to="leaf-02",
            required_acls=(
                RequiredAclRule(
                    acl_name="ACL-EXTERNAL-IN",
                    interface_name="Ethernet1",
                    direction=AclDirection.IN,
                    severity=Severity.MEDIUM,
                    recommendation="Assign ACL-EXTERNAL-IN inbound to Ethernet1",
                ),
            ),
            created_at=created_at,
        ),
    )
