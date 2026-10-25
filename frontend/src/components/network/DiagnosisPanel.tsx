import type { Diagnosis, DiagnosisEvidence, EvidenceRole } from "../../api/networkDiagnosisTypes";
import type { NetworkStateResponse } from "../../api/networkStateTypes";
import type { NetworkDiagnosisViewState } from "../../hooks/useNetworkDiagnosis";
import {
  EVIDENCE_ROLE_ORDER,
  confidenceText,
  describeEvidence,
  groupEvidenceByRole,
  isDiagnosisAligned,
  resourceText,
} from "../../network/diagnosisView";
import { diagnosisTypePresentation } from "../../network/presentation";

const ROLE_HEADINGS: Record<EvidenceRole, string> = {
  SUPPORTS: "Supporting",
  CONTRADICTS: "Contradicting",
  UNKNOWN: "Unknown",
};

const INSUFFICIENT_EVIDENCE_COPY =
  "Symptoms are present, but the current observations do not support a definitive " +
  "physical-link or BGP-session attribution.";

/**
 * How many facts of each role are listed before the rest sit behind a
 * "Show remaining" disclosure. The API's own (deterministic) order is kept;
 * this only decides where the preview ends, never what is shown or ranked.
 */
const EVIDENCE_PREVIEW_COUNT: Record<EvidenceRole, number> = {
  SUPPORTS: 4,
  CONTRADICTS: 3,
  UNKNOWN: 2,
};

function EvidenceItem({ item }: { item: DiagnosisEvidence }) {
  const line = describeEvidence(item);
  return (
    <li>
      <span className="diagnosis-evidence__kind">{line.label}</span>
      {line.facts.map((fact) => (
        <span key={fact} className="diagnosis-evidence__fact">
          {fact}
        </span>
      ))}
    </li>
  );
}

function evidenceKey(item: DiagnosisEvidence, index: number): string {
  return `${item.kind}:${item.node_id}:${item.subject}:${index}`;
}

function EvidenceGroup({ role, items }: { role: EvidenceRole; items: DiagnosisEvidence[] }) {
  const limit = EVIDENCE_PREVIEW_COUNT[role];
  const preview = items.slice(0, limit);
  const rest = items.slice(limit);
  return (
    <section className={`diagnosis-evidence-group diagnosis-evidence-group--${role.toLowerCase()}`}>
      <h5>
        {ROLE_HEADINGS[role]} ({items.length})
      </h5>
      {items.length > 0 && (
        <ul>
          {preview.map((item, index) => (
            <EvidenceItem key={evidenceKey(item, index)} item={item} />
          ))}
        </ul>
      )}
      {rest.length > 0 && (
        <details className="evidence-more">
          <summary>Show remaining {rest.length}</summary>
          <ul>
            {rest.map((item, index) => (
              <EvidenceItem key={evidenceKey(item, limit + index)} item={item} />
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}

/**
 * Plain-language association drawn only from the evidence the backend itself
 * marked as supporting: it names which symptom kinds are present, never a
 * mechanism ("causing"), and invents nothing the evidence does not carry.
 */
function associationText(diagnosis: Diagnosis): string {
  if (diagnosis.diagnosis_type === "INSUFFICIENT_EVIDENCE") {
    return INSUFFICIENT_EVIDENCE_COPY;
  }
  const supporting = diagnosis.evidence.filter((item) => item.role === "SUPPORTS");
  const parts: string[] = [];
  if (supporting.some((item) => item.kind === "BGP_SESSION")) {
    parts.push("BGP session loss");
  }
  if (supporting.some((item) => item.kind === "ECMP_PATHS")) {
    parts.push("reduced ECMP redundancy");
  }
  return parts.length > 0
    ? `Associated with ${parts.join(" and ")}.`
    : "Associated with the correlated symptoms beside the topology.";
}

function DiagnosisCard({ diagnosis }: { diagnosis: Diagnosis }) {
  const type = diagnosis.diagnosis_type;
  const presentation = diagnosisTypePresentation(type);
  const groups = groupEvidenceByRole(diagnosis.evidence);
  const isInsufficient = type === "INSUFFICIENT_EVIDENCE";
  const isBgp = type === "BGP_SESSION_FAILURE";
  const symptomCount = diagnosis.linked_incidents.length;

  return (
    <li
      className={`diagnosis-card diagnosis-card--${presentation.tone}`}
      data-diagnosis-type={type}
    >
      <div className="diagnosis-card__main">
        <p className="diagnosis-card__eyebrow">Probable Root Cause</p>
        <h3 className="diagnosis-card__title">
          <span aria-hidden="true" className="diagnosis-card__glyph">
            {presentation.glyph}
          </span>
          {presentation.label}
        </h3>
        <p className="diagnosis-card__resource">
          <span className="diagnosis-card__label">
            {isBgp ? "Declared adjacency" : "Declared link"}
          </span>
          <span className="diagnosis-card__endpoints mono">{resourceText(diagnosis.resource)}</span>
        </p>
        <p className="diagnosis-card__association">{associationText(diagnosis)}</p>
      </div>

      <div className="diagnosis-card__aside">
        <p className="diagnosis-card__confidence">{confidenceText(diagnosis.confidence)}</p>
        <p className="diagnosis-card__counts">
          {symptomCount} correlated {symptomCount === 1 ? "symptom" : "symptoms"}
          <span aria-hidden="true"> · </span>
          {diagnosis.evidence.length} evidence {diagnosis.evidence.length === 1 ? "item" : "items"}
        </p>
        <p className="diagnosis-card__ids">
          <span title="Backend diagnosis type">{type}</span>
          <span className="diagnosis-card__resource-id">{diagnosis.resource.resource_id}</span>
        </p>
      </div>

      <details className="diagnosis-details" open={isInsufficient}>
        <summary>View evidence ({diagnosis.evidence.length})</summary>
        <p className="diagnosis-details__tally">
          {groups.SUPPORTS.length} supporting · {groups.CONTRADICTS.length} contradicting ·{" "}
          {groups.UNKNOWN.length} unknown
        </p>
        <div className="diagnosis-details__groups">
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
        </div>
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
          <p className="diagnosis-calm" role="status">
            <span className="diagnosis-calm__title">No active root-cause diagnosis</span>
            <span className="diagnosis-calm__note">
              No current network observations require RCA attribution.
            </span>
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
    <section className="diagnosis-panel" aria-label="Probable Root Cause">
      {body}
    </section>
  );
}
