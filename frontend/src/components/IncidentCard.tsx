import type { IncidentResponse } from "../api/types";

interface IncidentCardProps {
  incident: IncidentResponse;
}

const VIOLATION_TYPE_LABELS: Record<string, string> = {
  MISSING_REQUIRED_ACL: "Missing required ACL",
  TARGET_INTERFACE_MISSING: "Target interface missing",
};

const DIRECTION_LABELS: Record<string, string> = {
  in: "Inbound",
  out: "Outbound",
};

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function humanize(code: string, labels: Record<string, string>): string {
  return labels[code] ?? code;
}

export function IncidentCard({ incident }: IncidentCardProps) {
  const { evidence } = incident;

  return (
    <article className={`incident-card severity--${incident.severity.toLowerCase()}`}>
      <header className="incident-card__header">
        <span className="badge">{incident.severity}</span>
        <span className="badge badge--status">{incident.status}</span>
      </header>

      <dl className="incident-card__fields">
        <div>
          <dt>Device</dt>
          <dd>{incident.device_id}</dd>
        </div>
        <div>
          <dt>Affected resource</dt>
          <dd>{incident.affected_resource}</dd>
        </div>
        <div>
          <dt>Rule</dt>
          <dd>{incident.rule_ref}</dd>
        </div>
        <div>
          <dt>Occurrences</dt>
          <dd>{incident.occurrence_count}</dd>
        </div>
        <div>
          <dt>Last seen</dt>
          <dd>
            <time dateTime={incident.last_seen_at} title={incident.last_seen_at}>
              {formatTimestamp(incident.last_seen_at)}
            </time>
          </dd>
        </div>
      </dl>

      <p className="incident-card__recommendation">{incident.recommendation}</p>

      <details>
        <summary>Evidence</summary>
        <dl className="incident-card__fields">
          <div>
            <dt>Violation type</dt>
            <dd>{humanize(evidence.violation_type, VIOLATION_TYPE_LABELS)}</dd>
          </div>
          <div>
            <dt>Expected ACL</dt>
            <dd>{evidence.expected_acl_name}</dd>
          </div>
          <div>
            <dt>Actual ACL</dt>
            <dd>{evidence.actual_acl_name ?? "None"}</dd>
          </div>
          <div>
            <dt>Interface</dt>
            <dd>{evidence.interface_name}</dd>
          </div>
          <div>
            <dt>Direction</dt>
            <dd>{humanize(evidence.direction, DIRECTION_LABELS)}</dd>
          </div>
          <div>
            <dt>Source snapshot</dt>
            <dd>{evidence.source_snapshot_id}</dd>
          </div>
          <div>
            <dt>Fingerprint</dt>
            <dd>{incident.fingerprint}</dd>
          </div>
        </dl>
      </details>
    </article>
  );
}
