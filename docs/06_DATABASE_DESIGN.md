# Database Design — Maestro

PostgreSQL, accessed via SQLAlchemy models with Alembic migrations. No manual schema edits — every change is a migration, mirroring real-team practice.

## 1. Entity-Relationship Diagram

```mermaid
erDiagram
    DEVICES ||--o{ DEVICE_CONFIGS : has
    DEVICES ||--o{ BGP_SESSIONS : participates_in
    DEVICES ||--o{ ALERTS : triggers
    SERVICES ||--o{ SERVICE_REGISTRY : registers_as
    SERVICES ||--o{ DNS_RECORDS : resolves_to
    ALERTS ||--o{ REMEDIATION_ACTIONS : triggers
    ALERTS ||--o| INCIDENTS : escalates_to
    INCIDENTS ||--o{ POSTMORTEMS : produces
    USERS ||--o{ REMEDIATION_ACTIONS : approves

    DEVICES {
        uuid id PK
        string hostname
        string vendor
        string role
        string mgmt_ip
        string oob_ip
        timestamp last_seen
    }
    DEVICE_CONFIGS {
        uuid id PK
        uuid device_id FK
        text config_yaml
        string status
        timestamp pushed_at
        boolean rolled_back
    }
    BGP_SESSIONS {
        uuid id PK
        uuid device_id FK
        string peer_ip
        int local_as
        int peer_as
        string state
        timestamp last_state_change
    }
    SERVICES {
        uuid id PK
        string name
        string owner_team
    }
    SERVICE_REGISTRY {
        uuid id PK
        uuid service_id FK
        string instance_ip
        string health_status
        timestamp registered_at
    }
    DNS_RECORDS {
        uuid id PK
        uuid service_id FK
        string fqdn
        string record_type
        string value
        int ttl
    }
    ALERTS {
        uuid id PK
        string severity
        string source
        text description
        timestamp fired_at
        timestamp resolved_at
    }
    REMEDIATION_ACTIONS {
        uuid id PK
        uuid alert_id FK
        string action_type
        string executed_by
        boolean auto_executed
        uuid approved_by FK
        timestamp executed_at
        string result
    }
    INCIDENTS {
        uuid id PK
        uuid alert_id FK
        string severity
        timestamp declared_at
        timestamp resolved_at
        string status
    }
    POSTMORTEMS {
        uuid id PK
        uuid incident_id FK
        text summary
        text root_cause
        text action_items
        timestamp published_at
    }
    USERS {
        uuid id PK
        string name
        string role
    }
```

## 2. Design Notes

- **`DEVICE_CONFIGS.rolled_back`** exists specifically to make the Phase 1 rollback mechanism auditable — every push attempt is a row, not just successful ones.
- **`BGP_SESSIONS.state`** (established/idle/active/connect) is polled and stored so route-flap history is queryable after the fact, not just visible live in Grafana.
- **`SERVICE_REGISTRY` is the source of truth DNS syncs from** — this is the concrete implementation of "loss of service discovery" as a data problem, not just a network problem.
- **`REMEDIATION_ACTIONS.auto_executed` + `approved_by`** implements the human-in-the-loop tiering directly in the schema — every action's provenance is queryable.
- **`INCIDENTS` and `POSTMORTEMS`** are deliberately separate from `ALERTS` — not every alert becomes an incident, mirroring real severity-based escalation practice (see `16_INCIDENT_RESPONSE_PLAYBOOKS.md`).

## 3. Indexing & Performance Notes

- Index `alerts.fired_at`, `bgp_sessions.last_state_change`, and `device_configs.pushed_at` — all time-range-queried heavily by dashboards.
- `service_registry.health_status` indexed for the DNS sync agent's polling query.
- At this project's scale, performance tuning is a documentation/design exercise (explain query plans, note where a read replica or caching layer would go at real scale) rather than a load-bearing requirement — noted honestly rather than over-engineered.
