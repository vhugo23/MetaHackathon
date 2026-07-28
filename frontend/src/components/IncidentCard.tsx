import {
  isPolicyViolationIncidentEvidenceResponse,
  type IncidentResponse,
  type PolicyViolationIncidentEvidenceResponse,
} from "../api/types";

interface IncidentCardProps {
  incident: IncidentResponse;
  isResolving: boolean;
  resolveError: string | undefined;
  onResolve: (incidentId: string) => void;
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

export function IncidentCard({
  incident,
  isResolving,
  resolveError,
  onResolve,
}: IncidentCardProps) {
  const isOpen = incident.status === "OPEN";
  // `IncidentResponse.evidence` is a rule_ref-discriminated union (Day
  // 11B1) — only a POLICY_VIOLATION incident whose evidence actually
  // passes the shared runtime guard is narrowed to the policy-specific
  // shape this component already knows how to render. Day 11B2 will add
  // CPU/link-flap/BGP evidence rendering; until then, a non-POLICY_VIOLATION
  // incident simply renders no evidence-details block.
  const policyEvidence: PolicyViolationIncidentEvidenceResponse | undefined =
    incident.source === "POLICY_VIOLATION" &&
    isPolicyViolationIncidentEvidenceResponse(incident.evidence)
      ? incident.evidence
      : undefined;

  return (
    <article
      className={`incident-card severity--${incident.severity.toLowerCase()} status--${incident.status.toLowerCase()}`}
    >
      <dl className="incident-card__identity">
        <div>
          <dt>Device</dt>
          <dd>{incident.device_id}</dd>
        </div>
        <div>
          <dt>Affected resource</dt>
          <dd>{incident.affected_resource}</dd>
        </div>
      </dl>

      <div className="incident-card__badges">
        <span className={`badge badge--severity severity--${incident.severity.toLowerCase()}`}>
          {incident.severity}
        </span>
        <span className={`badge badge--status status--${incident.status.toLowerCase()}`}>
          {incident.status}
        </span>
      </div>

      <p className="incident-card__recommendation">{incident.recommendation}</p>

      <dl className="incident-card__fields">
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
        <div>
          <dt>Updated</dt>
          <dd>
            <time dateTime={incident.updated_at} title={incident.updated_at}>
              {formatTimestamp(incident.updated_at)}
            </time>
          </dd>
        </div>
        {incident.resolved_at && (
          <div>
            <dt>Resolved</dt>
            <dd>
              <time dateTime={incident.resolved_at} title={incident.resolved_at}>
                {formatTimestamp(incident.resolved_at)}
              </time>
            </dd>
          </div>
        )}
      </dl>

      {isOpen && (
        <div className="incident-card__actions">
          <button
            type="button"
            className="incident-card__resolve-button"
            disabled={isResolving}
            onClick={() => onResolve(incident.incident_id)}
          >
            {isResolving ? "Resolving…" : "Resolve incident"}
          </button>
          {resolveError && (
            <p className="incident-card__resolve-error" role="alert">
              {resolveError}
            </p>
          )}
        </div>
      )}

      {policyEvidence && (
        <details>
          <summary>Evidence</summary>
          <dl className="incident-card__fields">
            <div>
              <dt>Violation type</dt>
              <dd>{humanize(policyEvidence.violation_type, VIOLATION_TYPE_LABELS)}</dd>
            </div>
            <div>
              <dt>Expected ACL</dt>
              <dd>{policyEvidence.expected_acl_name}</dd>
            </div>
            <div>
              <dt>Actual ACL</dt>
              <dd>{policyEvidence.actual_acl_name ?? "None"}</dd>
            </div>
            <div>
              <dt>Interface</dt>
              <dd>{policyEvidence.interface_name}</dd>
            </div>
            <div>
              <dt>Direction</dt>
              <dd>{humanize(policyEvidence.direction, DIRECTION_LABELS)}</dd>
            </div>
            <div>
              <dt>Source snapshot</dt>
              <dd>{policyEvidence.source_snapshot_id}</dd>
            </div>
            <div>
              <dt>Fingerprint</dt>
              <dd>{incident.fingerprint}</dd>
            </div>
          </dl>
        </details>
      )}
    </article>
  );
}
