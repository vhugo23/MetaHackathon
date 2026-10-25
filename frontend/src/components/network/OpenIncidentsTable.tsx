import type { IncidentResponse } from "../../api/types";
import { humanizeResource } from "../../network/diagnosisView";

interface OpenIncidentsTableProps {
  /** The Lab 1 operational incidents (any status); only OPEN ones are listed. */
  incidents: readonly IncidentResponse[];
}

function severityClass(severity: string): string {
  return `symptom__severity symptom__severity--${severity.toLowerCase().replace(/[^a-z]/g, "")}`;
}

/**
 * Compact, read-only list of the open Lab 1 incidents. Incident management
 * (resolve, evidence, fingerprints) stays in Incidents & Tools.
 */
export function OpenIncidentsTable({ incidents }: OpenIncidentsTableProps) {
  const open = incidents.filter((incident) => incident.status === "OPEN");
  const resolvedCount = incidents.length - open.length;

  return (
    <>
      {open.length === 0 ? (
        <p className="detail-note">No open incidents.</p>
      ) : (
        <div className="data-table-wrap">
          <table className="data-table data-table--incidents">
            <caption className="visually-hidden">Open Incidents</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Device</th>
                <th scope="col">Resource</th>
                <th scope="col">Status</th>
                <th scope="col">Occurrences</th>
              </tr>
            </thead>
            <tbody>
              {open.map((incident) => (
                <tr key={incident.incident_id} data-testid="open-incident-row">
                  <td data-label="Severity">
                    <span className={severityClass(incident.severity)}>{incident.severity}</span>
                  </td>
                  <th scope="row" className="mono" data-label="Device">
                    {incident.device_id}
                  </th>
                  <td data-label="Resource" title={incident.affected_resource}>
                    {humanizeResource(incident.affected_resource)}
                  </td>
                  <td data-label="Status">{incident.status}</td>
                  <td data-label="Occurrences">{incident.occurrence_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="panel__footnote">
        {resolvedCount > 0 &&
          `${resolvedCount} resolved ${resolvedCount === 1 ? "incident" : "incidents"} in history. `}
        <a href="#tools">Manage incidents in the tools view</a>
      </p>
    </>
  );
}
