import { useState, type FormEvent } from "react";
import { selectLatestTelemetrySample } from "../api/telemetry";
import {
  isBgpDownEvidenceResponse,
  isCpuHighEvidenceResponse,
  isLinkFlapEvidenceResponse,
  type IncidentResponse,
} from "../api/types";
import { useTelemetryWorkspace } from "../hooks/useTelemetryWorkspace";

function formatTimestamp(iso: string): string {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleString();
}

/**
 * Dispatches on the incident's own `rule_ref`, confirmed by the matching
 * exported evidence guard (`types.ts`) before reading any evidence field —
 * never an unsafe cast, `any`, or ad hoc property-presence guess. An
 * unrecognized future `rule_ref`, or a `rule_ref`/evidence mismatch, falls
 * back to a generic message rather than guessing at a shape this client
 * does not know.
 */
function AnomalyEvidenceSummary({ incident }: { incident: IncidentResponse }) {
  const { evidence } = incident;

  if (incident.rule_ref === "RULE-CPU-HIGH" && isCpuHighEvidenceResponse(evidence)) {
    const latest = evidence.samples[evidence.samples.length - 1];
    return (
      <p className="telemetry-anomaly__evidence">
        {latest
          ? `Latest CPU utilization: ${latest.cpu_utilization_pct}% across ${evidence.samples.length} evidence sample(s).`
          : `${evidence.samples.length} evidence sample(s).`}
      </p>
    );
  }

  if (incident.rule_ref === "RULE-LINK-FLAP" && isLinkFlapEvidenceResponse(evidence)) {
    return (
      <p className="telemetry-anomaly__evidence">
        {`Interface ${evidence.interface_name}: ${evidence.transitions.length} recorded transition(s).`}
      </p>
    );
  }

  if (incident.rule_ref === "RULE-BGP-DOWN" && isBgpDownEvidenceResponse(evidence)) {
    return (
      <p className="telemetry-anomaly__evidence">
        {`Neighbor ${evidence.neighbor_ip}: ${evidence.previous_state} -> ${evidence.state}.`}
      </p>
    );
  }

  return <p className="telemetry-anomaly__evidence">Evidence details are unavailable.</p>;
}

function AnomalyIncidentItem({ incident }: { incident: IncidentResponse }) {
  return (
    <li className="telemetry-anomaly">
      <div className="incident-card__badges">
        <span className={`badge badge--severity severity--${incident.severity.toLowerCase()}`}>
          {incident.severity}
        </span>
        <span className={`badge badge--status status--${incident.status.toLowerCase()}`}>
          {incident.status}
        </span>
      </div>
      <dl className="incident-card__fields">
        <div>
          <dt>Rule</dt>
          <dd>{incident.rule_ref}</dd>
        </div>
        <div>
          <dt>Affected resource</dt>
          <dd>{incident.affected_resource}</dd>
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
      <p className="telemetry-anomaly__recommendation">{incident.recommendation}</p>
      <AnomalyEvidenceSummary incident={incident} />
    </li>
  );
}

export function TelemetryPanel() {
  const { state, loadDevice, refresh } = useTelemetryWorkspace();
  const [deviceIdInput, setDeviceIdInput] = useState("");

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    loadDevice(deviceIdInput);
  }

  const latestSample = selectLatestTelemetrySample(state.samples);
  const hasDevice = state.deviceId !== undefined;

  return (
    <section className="telemetry-workspace">
      <div className="telemetry-workspace__intro">
        <h2>Device telemetry</h2>
        <p className="telemetry-workspace__description">
          Load telemetry and anomaly incidents already recorded for a device by the telemetry
          simulator.
        </p>
      </div>

      <form className="telemetry-workspace__form" onSubmit={handleSubmit}>
        <div className="form-field">
          <label htmlFor="telemetry-device-id">Telemetry device</label>
          <input
            id="telemetry-device-id"
            type="text"
            value={deviceIdInput}
            onChange={(event) => {
              setDeviceIdInput(event.target.value);
            }}
            placeholder="spine-01 or leaf-02"
          />
        </div>
        <button type="submit">Load telemetry</button>
      </form>

      {hasDevice && (
        <div className="telemetry-workspace__results" aria-busy={state.isRefreshing}>
          <div className="telemetry-workspace__toolbar">
            <p>
              Device: <strong>{state.deviceId}</strong>
            </p>
            {state.lastRefreshedAt && (
              <p>
                Last refreshed{" "}
                <time dateTime={state.lastRefreshedAt} title={state.lastRefreshedAt}>
                  {formatTimestamp(state.lastRefreshedAt)}
                </time>
              </p>
            )}
            <span role="status" aria-live="polite">
              {state.isRefreshing ? "Refreshing telemetry…" : ""}
            </span>
            <button
              type="button"
              className="telemetry-workspace__refresh"
              onClick={refresh}
              disabled={state.isRefreshing}
            >
              Refresh telemetry
            </button>
          </div>

          {state.isInitialLoading && (
            <p className="status-message" role="status" aria-live="polite">
              Loading telemetry…
            </p>
          )}

          {!state.isInitialLoading && (
            <>
              {state.telemetryError && (
                <p className="status-message status-message--error" role="alert">
                  {state.telemetryError}
                </p>
              )}
              {state.incidentError && (
                <p className="status-message status-message--error" role="alert">
                  {state.incidentError}
                </p>
              )}

              {!state.telemetryError &&
                (latestSample ? (
                  <div className="telemetry-summary">
                    <dl className="telemetry-summary__metrics">
                      <div>
                        <dt>Sampled at</dt>
                        <dd>
                          <time
                            dateTime={latestSample.sampled_at}
                            title={latestSample.sampled_at}
                          >
                            {formatTimestamp(latestSample.sampled_at)}
                          </time>
                        </dd>
                      </div>
                      <div>
                        <dt>CPU utilization</dt>
                        <dd>{latestSample.cpu_utilization_pct}%</dd>
                      </div>
                      <div>
                        <dt>Memory utilization</dt>
                        <dd>{latestSample.memory_utilization_pct}%</dd>
                      </div>
                      <div>
                        <dt>Interface error rate</dt>
                        <dd>{latestSample.interface_error_rate}</dd>
                      </div>
                    </dl>

                    <div className="telemetry-state-group">
                      <h3>Interfaces</h3>
                      {latestSample.interface_states.length === 0 ? (
                        <p>No interface state has been recorded for this device.</p>
                      ) : (
                        <ul className="telemetry-state-list">
                          {latestSample.interface_states.map((iface) => (
                            <li key={iface.name}>
                              <span className="telemetry-state-list__name">{iface.name}</span>
                              <span className="telemetry-state-list__value">
                                {iface.oper_state}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>

                    <div className="telemetry-state-group">
                      <h3>BGP sessions</h3>
                      {latestSample.bgp_sessions.length === 0 ? (
                        <p>No BGP session state has been recorded for this device.</p>
                      ) : (
                        <ul className="telemetry-state-list">
                          {latestSample.bgp_sessions.map((session) => (
                            <li key={session.neighbor_ip}>
                              <span className="telemetry-state-list__name">
                                {session.neighbor_ip}
                              </span>
                              <span className="telemetry-state-list__value">
                                {session.state}
                              </span>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>

                    <div className="telemetry-history">
                      <div className="telemetry-history__scroll">
                        <table>
                          <caption>Recent telemetry samples</caption>
                          <thead>
                            <tr>
                              <th scope="col">Sampled at</th>
                              <th scope="col">CPU</th>
                              <th scope="col">Memory</th>
                              <th scope="col">Interface error rate</th>
                            </tr>
                          </thead>
                          <tbody>
                            {state.samples.map((sample, index) => (
                              <tr key={`${sample.sampled_at}-${index}`}>
                                <td>
                                  <time dateTime={sample.sampled_at} title={sample.sampled_at}>
                                    {formatTimestamp(sample.sampled_at)}
                                  </time>
                                </td>
                                <td>{sample.cpu_utilization_pct}%</td>
                                <td>{sample.memory_utilization_pct}%</td>
                                <td>{sample.interface_error_rate}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </div>
                ) : (
                  <p>No telemetry has been recorded for this device.</p>
                ))}

              {!state.incidentError && (
                <div className="telemetry-anomalies">
                  <h3>Anomaly incidents</h3>
                  {state.anomalyIncidents.length === 0 ? (
                    <p>No anomaly incidents exist for this device.</p>
                  ) : (
                    <ul className="telemetry-anomaly-list">
                      {state.anomalyIncidents.map((incident) => (
                        <AnomalyIncidentItem key={incident.incident_id} incident={incident} />
                      ))}
                    </ul>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
