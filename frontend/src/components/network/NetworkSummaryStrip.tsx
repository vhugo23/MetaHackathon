import type { NetworkSummary } from "../../network/derive";
import {
  ecmpPresentation,
  reachabilityPresentation,
  statusPresentation,
} from "../../network/presentation";
import { StateBadge } from "./StateBadge";

interface NetworkSummaryStripProps {
  summary: NetworkSummary;
}

function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : pluralForm}`;
}

/**
 * Five compact operational KPIs. Every figure is a count of observations
 * already derived from GET /network/state (and, for the last card, the
 * separately fetched incident list); nothing here is a score or a root-cause
 * statement, and missing/unknown data is never counted as good.
 */
export function NetworkSummaryStrip({ summary }: NetworkSummaryStripProps) {
  const { bgp } = summary;
  const health = statusPresentation(summary.status);
  const failingLinks = summary.links.filter((entry) => entry.state === "down").length;

  const healthNote =
    summary.status === "healthy"
      ? "No degraded observations"
      : summary.status === "unknown"
        ? "Missing or unknown data is never counted as good"
        : [
            failingLinks > 0 ? plural(failingLinks, "failing link") : null,
            summary.openIncidentCount !== null && summary.openIncidentCount > 0
              ? plural(summary.openIncidentCount, "incident")
              : null,
          ]
            .filter((part) => part !== null)
            .join(" · ") || "Degraded observations present";

  const bgpNote =
    bgp.degraded === 0 && bgp.unknown === 0
      ? "All expected adjacencies observed"
      : [
          bgp.degraded > 0 ? `${bgp.degraded} degraded` : null,
          bgp.unknown > 0 ? `${bgp.unknown} unknown / not observed` : null,
        ]
          .filter((part) => part !== null)
          .join(" · ");

  const observedEcmp = summary.ecmp.filter((entry) => entry.pathCount !== null);
  const worstEcmp = observedEcmp.reduce<(typeof observedEcmp)[number] | null>(
    (worst, entry) =>
      worst === null || (entry.pathCount ?? 0) < (worst.pathCount ?? 0) ? entry : worst,
    null,
  );

  const reachable = summary.reachability.filter((entry) => entry.status === "succeeded").length;

  return (
    <section className="summary-strip" aria-label="Network summary">
      <div className={`summary-card summary-card--health summary-card--${health.tone}`}>
        <h3>Network Health</h3>
        <p className="summary-card__value summary-card__value--status">
          <span aria-hidden="true" className="summary-card__glyph">
            {health.glyph}
          </span>
          {health.label}
        </p>
        <p className="summary-card__note">{healthNote}</p>
      </div>

      <div className="summary-card">
        <h3>BGP Sessions</h3>
        <p className="summary-card__value">
          {bgp.established} / {bgp.expected} <span className="summary-card__unit">Established</span>
        </p>
        <p className="summary-card__note">{bgpNote}</p>
      </div>

      <div className="summary-card">
        <h3>ECMP</h3>
        <p className="summary-card__value">
          {worstEcmp === null ? (
            <>
              {"— "}
              <span className="summary-card__unit">Not observed</span>
            </>
          ) : (
            <>
              {worstEcmp.pathCount} / {worstEcmp.expectedPaths}{" "}
              <span className="summary-card__unit">Paths available</span>
            </>
          )}
        </p>
        <ul className="summary-card__list">
          {summary.ecmp.map((entry) => (
            <li
              key={entry.node}
              title={`${entry.node} to ${entry.towardsHost} (${entry.prefix}), expected ${entry.expectedPaths}`}
            >
              <strong>{entry.node}</strong>:{" "}
              {entry.pathCount === null
                ? "not observed"
                : `${entry.pathCount} ${entry.pathCount === 1 ? "path" : "paths"}`}{" "}
              <StateBadge presentation={ecmpPresentation(entry.status)} />
            </li>
          ))}
        </ul>
      </div>

      <div className="summary-card">
        <h3>Reachability</h3>
        <p className="summary-card__value">
          {reachable} / {summary.reachability.length}{" "}
          <span className="summary-card__unit">Hosts reachable</span>
        </p>
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
        <h3>Open Incidents</h3>
        <p className="summary-card__value">
          {summary.openIncidentCount === null ? "Unavailable" : summary.openIncidentCount}
          {summary.openIncidentCount !== null && summary.openIncidentCount > 0 && (
            <>
              {" "}
              <span className="summary-card__unit">Active</span>
            </>
          )}
        </p>
        <p className="summary-card__note">
          {summary.openIncidentCount === null
            ? "Incident list not loaded; no incidents is never assumed."
            : "Lab 1 anomaly incidents"}
        </p>
      </div>
    </section>
  );
}
