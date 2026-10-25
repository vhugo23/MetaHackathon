import type { LinkedIncident } from "../../api/networkDiagnosisTypes";
import type { NetworkStateResponse } from "../../api/networkStateTypes";
import type { NetworkDiagnosisViewState } from "../../hooks/useNetworkDiagnosis";
import { humanizeResource, isDiagnosisAligned, resourceText } from "../../network/diagnosisView";
import { diagnosisTypePresentation } from "../../network/presentation";

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

function severityClass(severity: string): string {
  return `symptom__severity symptom__severity--${severity.toLowerCase().replace(/[^a-z]/g, "")}`;
}

function Symptom({ incident }: { incident: LinkedIncident }) {
  return (
    <li className="symptom" data-testid="correlated-symptom">
      <div className="symptom__top">
        <span className={severityClass(incident.severity)}>{incident.severity}</span>
        <span className="symptom__status">{incident.status}</span>
      </div>
      <p className="symptom__device mono">{incident.device_id}</p>
      <p className="symptom__resource" title={incident.affected_resource}>
        {humanizeResource(incident.affected_resource)}
      </p>
      <p className="symptom__meta">
        {incident.occurrence_count} {incident.occurrence_count === 1 ? "occurrence" : "occurrences"}
        {" · last seen "}
        <time dateTime={incident.last_seen_at} title={incident.last_seen_at}>
          {formatTimestamp(incident.last_seen_at)}
        </time>
      </p>
      <p className="symptom__id mono">Incident {incident.incident_id}</p>
    </li>
  );
}

interface CorrelatedSymptomsPanelProps {
  diagnosis: NetworkDiagnosisViewState;
  snapshot: NetworkStateResponse;
}

/**
 * The incidents the backend linked to each diagnosis, as symptoms. They are
 * only ever shown for a diagnosis that belongs to the displayed snapshot's
 * collection, and they are never presented as causes.
 */
export function CorrelatedSymptomsPanel({ diagnosis, snapshot }: CorrelatedSymptomsPanelProps) {
  const hasSymptoms =
    diagnosis.status === "success" &&
    isDiagnosisAligned(diagnosis.data, snapshot) &&
    diagnosis.data.diagnoses.length > 0;
  let body;
  if (diagnosis.status !== "success" || !isDiagnosisAligned(diagnosis.data, snapshot)) {
    body = (
      <p className="detail-note">
        Symptom correlation is unavailable until a diagnosis for this snapshot is received.
      </p>
    );
  } else if (diagnosis.data.diagnoses.length === 0) {
    body = (
      <p className="detail-note">
        No correlated symptoms: there is no active root-cause diagnosis for this snapshot.
      </p>
    );
  } else {
    const { diagnoses } = diagnosis.data;
    body = diagnoses.map((item, index) => (
      <div
        key={`${item.diagnosis_type}:${item.resource.resource_id}:${index}`}
        className="symptom-group"
      >
        {diagnoses.length > 1 && (
          <h3 className="symptom-group__title">
            {diagnosisTypePresentation(item.diagnosis_type).label} ·{" "}
            <span className="mono">{resourceText(item.resource)}</span>
          </h3>
        )}
        <p className="symptom-group__count">Correlated symptoms ({item.linked_incidents.length})</p>
        {item.linked_incidents.length === 0 ? (
          <p className="detail-note">No matching open incident records</p>
        ) : (
          <ul className="symptom-list">
            {item.linked_incidents.map((incident) => (
              <Symptom key={incident.incident_id} incident={incident} />
            ))}
          </ul>
        )}
      </div>
    ));
  }

  return (
    <section
      className={`panel panel--symptoms${hasSymptoms ? "" : " panel--compact"}`}
      aria-labelledby="workspace-symptoms-heading"
    >
      <h2 id="workspace-symptoms-heading" className="panel__title">
        Correlated Symptoms
      </h2>
      {body}
    </section>
  );
}
