import type { NetworkStateResponse } from "../../api/networkStateTypes";
import { formatDuration, observationAgeMs, type NetworkSummary } from "../../network/derive";
import {
  ecmpPresentation,
  reachabilityPresentation,
  statusPresentation,
} from "../../network/presentation";
import { StateBadge } from "./StateBadge";

interface NetworkSummaryStripProps {
  state: NetworkStateResponse;
  summary: NetworkSummary;
}

export function NetworkSummaryStrip({ state, summary }: NetworkSummaryStripProps) {
  const { bgp } = summary;
  const collected = new Date(state.collected_at);

  return (
    <section className="summary-strip" aria-label="Network summary">
      <div className="summary-card">
        <h3>Presentation status</h3>
        <p className="summary-card__value">
          <StateBadge presentation={statusPresentation(summary.status)} />
        </p>
        <p className="summary-card__note">
          Frontend summary of the observations below, not a score. Missing or unknown data never
          counts as good.
        </p>
      </div>

      <div className="summary-card">
        <h3>BGP adjacencies</h3>
        <p className="summary-card__value">
          {bgp.established} / {bgp.expected} Established
        </p>
        <p className="summary-card__note">
          {bgp.degraded === 0 && bgp.unknown === 0
            ? "All expected leaf–spine adjacencies observed."
            : [
                bgp.degraded > 0 ? `${bgp.degraded} degraded` : null,
                bgp.unknown > 0 ? `${bgp.unknown} unknown / not observed` : null,
              ]
                .filter((part) => part !== null)
                .join(" · ")}
        </p>
      </div>

      <div className="summary-card">
        <h3>ECMP redundancy</h3>
        <ul className="summary-card__list">
          {summary.ecmp.map((entry) => (
            <li key={entry.node}>
              <strong>{entry.node}</strong>:{" "}
              {entry.pathCount === null
                ? "not observed"
                : `${entry.pathCount} ${entry.pathCount === 1 ? "path" : "paths"}`}{" "}
              <StateBadge presentation={ecmpPresentation(entry.status)} />
              <span className="summary-card__note">
                {" "}
                to {entry.towardsHost} ({entry.prefix}), expected {entry.expectedPaths}
              </span>
            </li>
          ))}
        </ul>
      </div>

      <div className="summary-card">
        <h3>Host reachability</h3>
        <ul className="summary-card__list">
          {summary.reachability.map((entry) => (
            <li key={entry.host}>
              {entry.probe === null ? (
                <>
                  <strong>{entry.host}</strong>: probe unavailable{" "}
                  <StateBadge presentation={reachabilityPresentation(entry.status)} />
                </>
              ) : (
                <>
                  <strong>{entry.host}</strong> → {entry.probe.target}{" "}
                  <span className="mono">
                    {entry.probe.received}/{entry.probe.sent}
                  </span>{" "}
                  <StateBadge presentation={reachabilityPresentation(entry.status)} />
                </>
              )}
            </li>
          ))}
        </ul>
      </div>

      <div className="summary-card">
        <h3>Open incidents</h3>
        <p className="summary-card__value">
          {summary.openIncidentCount === null ? "Unavailable" : summary.openIncidentCount}
        </p>
        <p className="summary-card__note">
          {summary.openIncidentCount === null
            ? "Incident list not loaded; no incidents is never assumed."
            : "Lab 1 anomaly incidents from GET /incidents; independent of the network snapshot."}
        </p>
      </div>

      <div className="summary-card">
        <h3>Observation</h3>
        <p className="summary-card__value">
          Observed{" "}
          <span title="Server retrieved_at − collected_at">
            {formatDuration(observationAgeMs(state))} ago
          </span>
        </p>
        <p className="summary-card__note">
          Collected{" "}
          <time dateTime={state.collected_at} title={state.collected_at}>
            {collected.toLocaleString()}
          </time>
        </p>
      </div>
    </section>
  );
}
