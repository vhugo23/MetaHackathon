import { ConfigurationSubmissionForm } from "../components/ConfigurationSubmissionForm";
import { IncidentCard } from "../components/IncidentCard";
import { IncidentEmptyState } from "../components/IncidentEmptyState";
import { IncidentErrorState } from "../components/IncidentErrorState";
import { LoadingState } from "../components/LoadingState";
import { useIncidents } from "../hooks/useIncidents";

function incidentCountLabel(count: number): string {
  return count === 1 ? "1 incident" : `${count} incidents`;
}

export function IncidentDashboard() {
  const { state, refresh, resolvingIds, resolveErrors, resolveIncident } = useIncidents();

  return (
    <main>
      <header className="incident-dashboard__header">
        <div className="incident-dashboard__heading-group">
          <h1>Network Incidents</h1>
          <p className="page-description">
            Configuration policy violations detected across managed devices.
          </p>
        </div>
      </header>

      <ConfigurationSubmissionForm
        onSubmissionSuccess={() => {
          refresh();
        }}
      />

      <div className="incident-dashboard__section">
        {state.status === "loading" && <LoadingState />}

        {state.status === "error" && (
          <IncidentErrorState message={state.message} onRetry={refresh} />
        )}

        {state.status === "success" && state.data.length === 0 && (
          <IncidentEmptyState onRefresh={refresh} isRefreshing={state.isRefreshing} />
        )}

        {state.status === "success" && state.data.length > 0 && (
          <div className="incident-dashboard__results" aria-busy={state.isRefreshing}>
            <div className="dashboard-toolbar">
              <p>{incidentCountLabel(state.data.length)}</p>
              <p>
                Last updated{" "}
                <time dateTime={state.lastUpdatedAt} title={state.lastUpdatedAt}>
                  {new Date(state.lastUpdatedAt).toLocaleString()}
                </time>
              </p>
              <span role="status" aria-live="polite">
                {state.isRefreshing ? "Refreshing incidents…" : ""}
              </span>
              <button
                type="button"
                className="incident-dashboard__refresh"
                onClick={refresh}
                disabled={state.isRefreshing}
              >
                Refresh
              </button>
            </div>
            <div className="incident-list">
              {state.data.map((incident) => (
                <IncidentCard
                  key={incident.incident_id}
                  incident={incident}
                  isResolving={resolvingIds.has(incident.incident_id)}
                  resolveError={resolveErrors[incident.incident_id]}
                  onResolve={resolveIncident}
                />
              ))}
            </div>
          </div>
        )}
      </div>
    </main>
  );
}
