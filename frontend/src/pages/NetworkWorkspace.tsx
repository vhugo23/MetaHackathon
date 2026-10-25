import { useState } from "react";
import { HeaderStatus } from "../components/HeaderStatus";
import { IncidentErrorState } from "../components/IncidentErrorState";
import { LoadingState } from "../components/LoadingState";
import { CorrelatedSymptomsPanel } from "../components/network/CorrelatedSymptomsPanel";
import { DiagnosisPanel } from "../components/network/DiagnosisPanel";
import { NetworkSummaryStrip } from "../components/network/NetworkSummaryStrip";
import { NodeDetailPanel } from "../components/network/NodeDetailPanel";
import { OpenIncidentsTable } from "../components/network/OpenIncidentsTable";
import { PhysicalLinkTable } from "../components/network/PhysicalLinkTable";
import { TopologyDiagram } from "../components/network/TopologyDiagram";
import type { NetworkStateError } from "../hooks/useNetworkState";
import {
  LIVE_REFRESH_INTERVAL_MS,
  useLiveNetworkWorkspace,
} from "../hooks/useLiveNetworkWorkspace";
import type { IncidentsState } from "../hooks/useIncidents";
import {
  deriveNetworkSummary,
  formatObservationLabel,
  lab1OperationalIncidents,
} from "../network/derive";
import { diagnosisMarksByLink, isDiagnosisAligned } from "../network/diagnosisView";
import { nodeById } from "../network/lab1Topology";

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
  onRefresh: () => void;
}

/**
 * Compact, read-only list of the open Lab 1 incidents. Resolution and the
 * full incident cards stay, unchanged, under Incidents & Tools (#tools).
 */
function IncidentsSection({ incidents, onRefresh }: IncidentsSectionProps) {
  return (
    <section className="panel panel--incidents" aria-labelledby="workspace-incidents-heading">
      <h2 id="workspace-incidents-heading" className="panel__title">
        Open Incidents
      </h2>
      {incidents.status === "loading" && <LoadingState />}
      {incidents.status === "error" && (
        <IncidentErrorState message={incidents.message} onRetry={onRefresh} />
      )}
      {incidents.status === "success" && incidents.refreshError !== undefined && (
        <p className="status-message status-message--inline" role="status">
          Incident refresh failed ({incidents.refreshError}). Showing the last loaded list.
        </p>
      )}
      {incidents.status === "success" && <OpenIncidentsTable incidents={incidents.data} />}
    </section>
  );
}

export function NetworkWorkspace() {
  const { network, diagnosis, incidents, refreshAll } = useLiveNetworkWorkspace();
  const { state } = network;
  const [manualNodeId, setManualNodeId] = useState<string | null>(null);

  // Workspace scope: Lab 1 operational (ANOMALY on Lab 1 routers) incidents,
  // OPEN and RESOLVED history. Everything else lives, unchanged, at #tools.
  const incidentList = incidents.state.status === "success" ? incidents.state.data : null;
  const scopedIncidentsState: IncidentsState =
    incidents.state.status === "success"
      ? { ...incidents.state, data: lab1OperationalIncidents(incidents.state.data) }
      : incidents.state;
  const snapshot = state.status === "success" ? state.data : null;
  const summary = snapshot === null ? null : deriveNetworkSummary(snapshot, incidentList);
  // Only a diagnosis that belongs to the displayed collection may mark links.
  const diagnosisMarks =
    snapshot !== null &&
    summary !== null &&
    diagnosis.state.status === "success" &&
    isDiagnosisAligned(diagnosis.state.data, snapshot)
      ? diagnosisMarksByLink(
          diagnosis.state.data.diagnoses,
          summary.links.map((entry) => entry.link),
        )
      : undefined;
  // Until the user picks a node, the first node named by an aligned backend
  // diagnosis resource is shown in Selected Node. A manual choice always wins;
  // a healthy (empty) diagnosis selects nothing.
  const diagnosedNodeId =
    snapshot !== null && diagnosis.state.status === "success" && diagnosisMarks !== undefined
      ? (diagnosis.state.data.diagnoses[0]?.resource.endpoint_a.node_id ?? null)
      : null;
  const selectedNodeId =
    manualNodeId ??
    (diagnosedNodeId !== null && nodeById(diagnosedNodeId) !== undefined ? diagnosedNodeId : null);
  const fabricLinkCount =
    summary === null ? 0 : summary.links.filter((entry) => entry.link.kind === "fabric").length;
  const accessLinkCount = summary === null ? 0 : summary.links.length - fabricLinkCount;
  const lastUpdatedAt =
    state.status === "success" || state.status === "no-snapshot" ? state.lastUpdatedAt : null;
  const isRefreshing =
    (state.status === "success" || state.status === "no-snapshot") && state.isRefreshing;

  const connectionBroken =
    state.status === "error" || (state.status === "success" && state.refreshError !== null);
  const connection =
    state.status === "loading"
      ? "Connecting…"
      : state.status === "error"
        ? ERROR_TITLES[state.error.kind]
        : connectionBroken
          ? "Connection issue"
          : "Connected";
  const connectionTone = connectionBroken ? "bad" : state.status === "loading" ? "unknown" : "ok";

  return (
    <main className="workspace">
      <HeaderStatus>
        <ul className="header-status" aria-label="Connection status">
          <li className="header-status__item">
            <span
              aria-hidden="true"
              className={`header-status__dot header-status__dot--${connectionTone}`}
            />
            {connection}
          </li>
          <li className="header-status__item">Lab 1</li>
          <li className="header-status__item">Auto-refresh {LIVE_REFRESH_INTERVAL_MS / 1000}s</li>
          {snapshot !== null && (
            <li
              className="header-status__item"
              title="Age of the snapshot when the API served it (retrieved_at − collected_at)"
            >
              {formatObservationLabel(snapshot)}
            </li>
          )}
        </ul>
      </HeaderStatus>

      <header className="page-head">
        <div className="page-head__title">
          <h1>Live Network</h1>
          <p className="page-head__sub">Lab 1 leaf–spine fabric · Read-only operational view</p>
          {snapshot !== null && (
            <p className="page-head__meta">
              Collection <span className="mono">{snapshot.collection_id}</span> · collected{" "}
              <time dateTime={snapshot.collected_at} title={snapshot.collected_at}>
                {new Date(snapshot.collected_at).toLocaleString()}
              </time>
            </p>
          )}
          {snapshot === null && lastUpdatedAt !== null && (
            <p className="page-head__meta">
              Last fetched{" "}
              <time dateTime={lastUpdatedAt} title={lastUpdatedAt}>
                {new Date(lastUpdatedAt).toLocaleString()}
              </time>
            </p>
          )}
        </div>
        <div className="page-head__actions">
          <span role="status" aria-live="polite" className="page-head__refreshing">
            {isRefreshing ? "Refreshing network state…" : ""}
          </span>
          <button type="button" className="incident-dashboard__refresh" onClick={refreshAll}>
            Refresh all
          </button>
        </div>
      </header>

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
          <NetworkSummaryStrip summary={summary} />
          <DiagnosisPanel
            diagnosis={diagnosis.state}
            snapshot={snapshot}
            onRetry={diagnosis.refresh}
          />
          <div className="workspace-main">
            <section className="panel panel--topology" aria-labelledby="workspace-topology-heading">
              <h2 id="workspace-topology-heading" className="panel__title">
                Network Topology
              </h2>
              <TopologyDiagram
                state={snapshot}
                summary={summary}
                incidents={incidentList}
                selectedNodeId={selectedNodeId}
                onSelect={setManualNodeId}
                diagnosisMarks={diagnosisMarks}
              />
            </section>
            <CorrelatedSymptomsPanel diagnosis={diagnosis.state} snapshot={snapshot} />
          </div>
          <div className="workspace-lower">
            <section className="panel panel--links" aria-labelledby="workspace-links-heading">
              <h2 id="workspace-links-heading" className="panel__title">
                Physical Links
                <span className="panel__count">
                  {fabricLinkCount} fabric links · {accessLinkCount} host access links
                </span>
              </h2>
              <PhysicalLinkTable
                links={summary.links}
                adjacencies={summary.adjacencies}
                diagnosisMarks={diagnosisMarks}
              />
            </section>
            <section
              className={`panel panel--node${selectedNodeId === null ? " panel--compact" : ""}`}
              aria-labelledby="workspace-detail-heading"
            >
              <h2 id="workspace-detail-heading" className="panel__title">
                Selected Node
              </h2>
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

      <IncidentsSection incidents={scopedIncidentsState} onRefresh={incidents.refresh} />
    </main>
  );
}
