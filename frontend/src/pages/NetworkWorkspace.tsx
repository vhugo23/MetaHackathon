import { useState } from "react";
import { IncidentCard } from "../components/IncidentCard";
import { IncidentEmptyState } from "../components/IncidentEmptyState";
import { IncidentErrorState } from "../components/IncidentErrorState";
import { LoadingState } from "../components/LoadingState";
import { NetworkSummaryStrip } from "../components/network/NetworkSummaryStrip";
import { NodeDetailPanel } from "../components/network/NodeDetailPanel";
import { PhysicalLinkTable } from "../components/network/PhysicalLinkTable";
import { TopologyDiagram } from "../components/network/TopologyDiagram";
import type { NetworkStateError } from "../hooks/useNetworkState";
import {
  LIVE_REFRESH_INTERVAL_MS,
  useLiveNetworkWorkspace,
} from "../hooks/useLiveNetworkWorkspace";
import type { IncidentsState } from "../hooks/useIncidents";
import { deriveNetworkSummary, lab1OperationalIncidents } from "../network/derive";

const ERROR_TITLES: Record<NetworkStateError["kind"], string> = {
  unreachable: "API unreachable",
  malformed: "Malformed response",
  api: "Network state request failed",
};

function NoSnapshotState() {
  return (
    <div className="status-message" role="status">
      <p>
        <strong>No live network snapshot yet.</strong>
      </p>
      <p>
        Operational state appears after the Lab 1 collector/ingestion workflow has accepted a
        snapshot. Nothing is assumed about the network until then.
      </p>
    </div>
  );
}

function NetworkErrorState({ error, onRetry }: { error: NetworkStateError; onRetry: () => void }) {
  return (
    <div className="status-message status-message--error" role="alert">
      <p>
        <strong>{ERROR_TITLES[error.kind]}.</strong>
      </p>
      <p>{error.message}</p>
      <button type="button" onClick={onRetry}>
        Retry
      </button>
    </div>
  );
}

interface IncidentsSectionProps {
  incidents: IncidentsState;
  resolvingIds: ReadonlySet<string>;
  resolveErrors: Readonly<Record<string, string>>;
  onResolve: (incidentId: string) => void;
  onRefresh: () => void;
}

function IncidentsSection({
  incidents,
  resolvingIds,
  resolveErrors,
  onResolve,
  onRefresh,
}: IncidentsSectionProps) {
  return (
    <section className="workspace-section" aria-labelledby="workspace-incidents-heading">
      <h2 id="workspace-incidents-heading">Incidents</h2>
      {incidents.status === "loading" && <LoadingState />}
      {incidents.status === "error" && (
        <IncidentErrorState message={incidents.message} onRetry={onRefresh} />
      )}
      {incidents.status === "success" && incidents.refreshError !== undefined && (
        <p className="status-message status-message--inline" role="status">
          Incident refresh failed ({incidents.refreshError}). Showing the last loaded list.
        </p>
      )}
      {incidents.status === "success" && incidents.data.length === 0 && (
        <IncidentEmptyState onRefresh={onRefresh} isRefreshing={incidents.isRefreshing} />
      )}
      {incidents.status === "success" && incidents.data.length > 0 && (
        <div className="incident-list">
          {incidents.data.map((incident) => (
            <IncidentCard
              key={incident.incident_id}
              incident={incident}
              isResolving={resolvingIds.has(incident.incident_id)}
              resolveError={resolveErrors[incident.incident_id]}
              onResolve={onResolve}
            />
          ))}
        </div>
      )}
    </section>
  );
}

export function NetworkWorkspace() {
  const { network, incidents, refreshAll } = useLiveNetworkWorkspace();
  const { state } = network;
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);

  // Workspace scope: Lab 1 operational (ANOMALY on Lab 1 routers) incidents,
  // OPEN and RESOLVED history. Everything else lives, unchanged, at #tools.
  const incidentList = incidents.state.status === "success" ? incidents.state.data : null;
  const scopedIncidentsState: IncidentsState =
    incidents.state.status === "success"
      ? { ...incidents.state, data: lab1OperationalIncidents(incidents.state.data) }
      : incidents.state;
  const snapshot = state.status === "success" ? state.data : null;
  const summary = snapshot === null ? null : deriveNetworkSummary(snapshot, incidentList);
  const lastUpdatedAt =
    state.status === "success" || state.status === "no-snapshot" ? state.lastUpdatedAt : null;
  const isRefreshing =
    (state.status === "success" || state.status === "no-snapshot") && state.isRefreshing;

  return (
    <main>
      <header className="incident-dashboard__header">
        <div className="incident-dashboard__heading-group">
          <h1>Live Network</h1>
          <p className="page-description">
            Lab 1 leaf–spine fabric as last ingested. The topology is the fixed declared one; every
            status comes from GET /network/state and GET /incidents.
          </p>
        </div>
      </header>

      <div className="dashboard-toolbar">
        {lastUpdatedAt !== null && (
          <p>
            Last fetched{" "}
            <time dateTime={lastUpdatedAt} title={lastUpdatedAt}>
              {new Date(lastUpdatedAt).toLocaleString()}
            </time>
          </p>
        )}
        <p>
          Auto-refresh every {LIVE_REFRESH_INTERVAL_MS / 1000} s (re-reads the API only; it does not
          collect the network).
        </p>
        <span role="status" aria-live="polite">
          {isRefreshing ? "Refreshing network state…" : ""}
        </span>
        <button type="button" className="incident-dashboard__refresh" onClick={refreshAll}>
          Refresh all
        </button>
      </div>

      {state.status === "loading" && (
        <p className="status-message" role="status" aria-live="polite">
          Loading network state…
        </p>
      )}
      {state.status === "no-snapshot" && <NoSnapshotState />}
      {state.status === "error" && (
        <NetworkErrorState error={state.error} onRetry={network.refresh} />
      )}

      {state.status === "success" && summary !== null && snapshot !== null && (
        <div className="workspace-live" aria-busy={state.isRefreshing}>
          {state.refreshError !== null && (
            <p className="status-message status-message--inline" role="status">
              Refresh failed: {ERROR_TITLES[state.refreshError.kind]} ({state.refreshError.message}
              ). Showing the last received snapshot.
            </p>
          )}
          <NetworkSummaryStrip state={snapshot} summary={summary} />
          <div className="workspace-grid">
            <section className="workspace-section" aria-labelledby="workspace-topology-heading">
              <h2 id="workspace-topology-heading">Topology</h2>
              <TopologyDiagram
                state={snapshot}
                summary={summary}
                incidents={incidentList}
                selectedNodeId={selectedNodeId}
                onSelect={setSelectedNodeId}
              />
              <PhysicalLinkTable links={summary.links} adjacencies={summary.adjacencies} />
            </section>
            <section className="workspace-section" aria-labelledby="workspace-detail-heading">
              <h2 id="workspace-detail-heading">Node detail</h2>
              {selectedNodeId === null ? (
                <p className="detail-note">
                  Select a node in the topology to see what was observed.
                </p>
              ) : (
                <NodeDetailPanel
                  nodeId={selectedNodeId}
                  state={snapshot}
                  incidents={incidentList}
                />
              )}
            </section>
          </div>
        </div>
      )}

      <IncidentsSection
        incidents={scopedIncidentsState}
        resolvingIds={incidents.resolvingIds}
        resolveErrors={incidents.resolveErrors}
        onResolve={incidents.resolveIncident}
        onRefresh={incidents.refresh}
      />
    </main>
  );
}
