import type {
  Diagnosis,
  DiagnosisEvidence,
  EvidenceRole,
  LinkedIncident,
} from "../../api/networkDiagnosisTypes";
import type { NetworkStateResponse } from "../../api/networkStateTypes";
import type { NetworkDiagnosisViewState } from "../../hooks/useNetworkDiagnosis";
import {
  CONFIDENCE_UNAVAILABLE_TEXT,
  EVIDENCE_ROLE_ORDER,
  describeEvidence,
  groupEvidenceByRole,
  isDiagnosisAligned,
  resourceText,
} from "../../network/diagnosisView";
import { diagnosisTypePresentation } from "../../network/presentation";
import { StateBadge } from "./StateBadge";

const ROLE_HEADINGS: Record<EvidenceRole, string> = {
  SUPPORTS: "Supports",
  CONTRADICTS: "Contradicts",
  UNKNOWN: "Unknown",
};

const INSUFFICIENT_EVIDENCE_COPY =
  "Symptoms are present, but the current observations do not support a definitive " +
  "physical-link or BGP-session attribution.";

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function EvidenceGroup({ role, items }: { role: EvidenceRole; items: DiagnosisEvidence[] }) {
  if (items.length === 0) {
    return null;
  }
  return (
    <section className={`diagnosis-evidence-group diagnosis-evidence-group--${role.toLowerCase()}`}>
      <h5>
        {ROLE_HEADINGS[role]} ({items.length})
      </h5>
      <ul>
        {items.map((item, index) => {
          const line = describeEvidence(item);
          return (
            <li key={`${item.kind}:${item.node_id}:${item.subject}:${index}`}>
              <span className="diagnosis-evidence__kind">{line.label}</span>
              {line.facts.map((fact) => (
                <span key={fact} className="diagnosis-evidence__fact">
                  {fact}
                </span>
              ))}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function CorrelatedSymptoms({ incidents }: { incidents: LinkedIncident[] }) {
  return (
    <section className="diagnosis-symptoms" aria-label="Correlated symptoms">
      <h4>Correlated symptoms ({incidents.length})</h4>
      {incidents.length === 0 ? (
        <p className="detail-note">No matching open incident records</p>
      ) : (
        <ul>
          {incidents.map((incident) => (
            <li key={incident.incident_id} data-testid="correlated-symptom">
              <span className="diagnosis-symptom__resource">
                {incident.device_id} · {incident.affected_resource}
              </span>
              <span className="cell-note">
                {incident.severity} · {incident.status} · seen {incident.occurrence_count}× · last
                seen{" "}
                <time dateTime={incident.last_seen_at} title={incident.last_seen_at}>
                  {formatTimestamp(incident.last_seen_at)}
                </time>
              </span>
              <span className="cell-note">Incident {incident.incident_id}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function DiagnosisCard({ diagnosis }: { diagnosis: Diagnosis }) {
  const type = diagnosis.diagnosis_type;
  const presentation = diagnosisTypePresentation(type);
  const groups = groupEvidenceByRole(diagnosis.evidence);
  const isInsufficient = type === "INSUFFICIENT_EVIDENCE";
  const isBgp = type === "BGP_SESSION_FAILURE";

  return (
    <li className="diagnosis-card" data-diagnosis-type={type}>
      <div className="diagnosis-card__head">
        <h4>
          <StateBadge presentation={presentation} />
        </h4>
        <span className="diagnosis-card__raw" title="Backend diagnosis type">
          {type}
        </span>
      </div>

      <dl className="diagnosis-card__facts">
        <div>
          <dt>Confidence</dt>
          <dd>{diagnosis.confidence ?? CONFIDENCE_UNAVAILABLE_TEXT}</dd>
        </div>
        <div>
          <dt>{isBgp ? "Declared adjacency" : "Declared link"}</dt>
          <dd>
            {resourceText(diagnosis.resource)}
            <span className="cell-note diagnosis-card__resource-id">
              {diagnosis.resource.resource_id}
            </span>
          </dd>
        </div>
        <div>
          <dt>Evidence</dt>
          <dd>
            {groups.SUPPORTS.length} supporting · {groups.CONTRADICTS.length} contradicting ·{" "}
            {groups.UNKNOWN.length} unknown
          </dd>
        </div>
      </dl>

      {isInsufficient && <p className="detail-note">{INSUFFICIENT_EVIDENCE_COPY}</p>}

      <CorrelatedSymptoms incidents={diagnosis.linked_incidents} />

      <details className="diagnosis-details" open={isInsufficient}>
        <summary>Evidence details ({diagnosis.evidence.length})</summary>
        {EVIDENCE_ROLE_ORDER.map((role) => (
          <EvidenceGroup key={role} role={role} items={groups[role]} />
        ))}
        <section className="diagnosis-evidence-group">
          <h5>Symptom keys ({diagnosis.symptom_keys.length})</h5>
          <ul>
            {diagnosis.symptom_keys.map((key) => (
              <li key={`${key.device_id}:${key.affected_resource}`}>
                <span className="diagnosis-evidence__fact">
                  {key.device_id} · {key.affected_resource}
                </span>
              </li>
            ))}
          </ul>
        </section>
      </details>
    </li>
  );
}

interface DiagnosisPanelProps {
  diagnosis: NetworkDiagnosisViewState;
  /** The network snapshot currently displayed in the workspace. */
  snapshot: NetworkStateResponse;
  onRetry: () => void;
}

/**
 * Backend-authoritative RCA for the displayed snapshot. It only renders what
 * `GET /network/diagnosis` returned, and only when that response belongs to
 * the displayed snapshot's collection; failures stay local to this panel.
 */
export function DiagnosisPanel({ diagnosis, snapshot, onRetry }: DiagnosisPanelProps) {
  let body;
  if (diagnosis.status === "loading") {
    body = (
      <p className="status-message status-message--inline" role="status">
        Loading diagnosis…
      </p>
    );
  } else if (diagnosis.status === "error") {
    body = (
      <div className="status-message status-message--error status-message--inline" role="alert">
        <p>
          <strong>Diagnosis unavailable.</strong> {diagnosis.error.message}
        </p>
        <button type="button" onClick={onRetry}>
          Retry diagnosis
        </button>
      </div>
    );
  } else if (diagnosis.status === "no-snapshot") {
    body = (
      <p className="status-message status-message--inline" role="status">
        <strong>Diagnosis unavailable.</strong> The diagnosis service reports no ingested network
        state. It will be retried automatically.
      </p>
    );
  } else {
    const { data, refreshError } = diagnosis;
    const aligned = isDiagnosisAligned(data, snapshot);
    body = (
      <>
        {refreshError !== null && (
          <p className="status-message status-message--inline" role="status">
            Diagnosis refresh failed ({refreshError.message}). Showing the last received diagnosis.
          </p>
        )}
        {!aligned && (
          <p className="status-message status-message--inline" role="status">
            <strong>Diagnosis updating.</strong> Diagnosis unavailable for the current snapshot (
            {snapshot.collection_id}); the last received diagnosis belongs to a different collection
            and is not shown.
          </p>
        )}
        {aligned && data.diagnoses.length === 0 && (
          <p className="detail-note" role="status">
            <strong>No active root-cause diagnosis</strong> for this snapshot. Incidents and other
            conditions are reported separately.
          </p>
        )}
        {aligned && data.diagnoses.length > 0 && (
          <ol className="diagnosis-list">
            {data.diagnoses.map((item, index) => (
              <DiagnosisCard
                key={`${item.diagnosis_type}:${item.resource.resource_id}:${index}`}
                diagnosis={item}
              />
            ))}
          </ol>
        )}
      </>
    );
  }

  return (
    <section className="workspace-section diagnosis-panel" aria-labelledby="workspace-rca-heading">
      <h2 id="workspace-rca-heading">Root-cause analysis</h2>
      {body}
    </section>
  );
}
