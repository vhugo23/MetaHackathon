import type { BgpAdjacencyPresentation, PhysicalLinkPresentation } from "../../network/derive";
import { confidenceText, type DiagnosisLinkMark } from "../../network/diagnosisView";
import {
  adjacencyPresentation,
  diagnosisTypePresentation,
  interfaceReasonText,
  bgpEndpointPresentation,
  physicalPresentation,
} from "../../network/presentation";
import { StateBadge } from "./StateBadge";

interface PhysicalLinkTableProps {
  links: PhysicalLinkPresentation[];
  adjacencies: BgpAdjacencyPresentation[];
  /** Backend RCA marks keyed by declared-link id (separate from observed state). */
  diagnosisMarks?: ReadonlyMap<string, readonly DiagnosisLinkMark[]>;
}

/**
 * Text twin of the topology diagram: every declared link with its physical
 * (interface-derived) state, the separate BGP adjacency state for
 * router-to-router links, and, in its own column, any backend RCA mark. The
 * observed state is never replaced by the diagnosis. Fabric links (the four
 * leaf–spine links) come first; host access links follow, labelled.
 */
export function PhysicalLinkTable({ links, adjacencies, diagnosisMarks }: PhysicalLinkTableProps) {
  const adjacencyByLink = new Map(adjacencies.map((a) => [a.link.id, a]));
  const fabric = links.filter((entry) => entry.link.kind === "fabric");
  const access = links.filter((entry) => entry.link.kind === "access");

  function renderRow(entry: PhysicalLinkPresentation) {
    const adjacency = adjacencyByLink.get(entry.link.id);
    const marks = diagnosisMarks?.get(entry.link.id) ?? [];
    const isAccess = entry.link.kind === "access";
    return (
      <tr key={entry.link.id} className={isAccess ? "link-row link-row--access" : "link-row"}>
        <th scope="row" className="mono">
          {entry.link.a.node}
        </th>
        <td className="mono link-row__to">{entry.link.b.node}</td>
        <td className="mono link-row__interfaces">
          <span className="link-row__iface-a">{entry.link.a.interface}</span>
          <span className="link-row__arrow"> ↔ </span>
          <span className="link-row__iface-b">{entry.link.b.interface}</span>
          {isAccess && <span className="cell-note link-row__kind">Host access link</span>}
        </td>
        <td data-label="Physical">
          <StateBadge presentation={physicalPresentation(entry.state)} />
          <span className="cell-note">
            {entry.a.node} {entry.a.state}
            {entry.a.reason === "reported" ? "" : ` (${interfaceReasonText(entry.a.reason)})`};{" "}
            {entry.b.node} {entry.b.state}
            {entry.b.reason === "reported" ? "" : ` (${interfaceReasonText(entry.b.reason)})`}
          </span>
        </td>
        <td data-label="BGP">
          {adjacency === undefined ? (
            <span className="cell-note">Not applicable (host link)</span>
          ) : (
            <>
              <StateBadge presentation={adjacencyPresentation(adjacency.state)} />
              <span className="cell-note">
                {adjacency.a.node}: {bgpEndpointPresentation(adjacency.a.observation).label};{" "}
                {adjacency.b.node}: {bgpEndpointPresentation(adjacency.b.observation).label}
              </span>
            </>
          )}
        </td>
        <td data-label="RCA">
          {marks.length === 0 ? (
            <span className="cell-note">—</span>
          ) : (
            marks.map((mark, index) => (
              <span
                key={`${mark.type}:${index}`}
                className={`rca-marker rca-marker--${diagnosisTypePresentation(mark.type).tone}`}
                data-testid="rca-marker"
              >
                {mark.type === "INSUFFICIENT_EVIDENCE" ? "RCA" : "Probable root cause"}:{" "}
                {diagnosisTypePresentation(mark.type).label} · {confidenceText(mark.confidence)}
              </span>
            ))
          )}
        </td>
      </tr>
    );
  }

  return (
    <div className="data-table-wrap">
      <table className="data-table data-table--links">
        <caption className="visually-hidden">Declared physical links</caption>
        <thead>
          <tr>
            <th scope="col">From</th>
            <th scope="col">To</th>
            <th scope="col">Interface</th>
            <th scope="col">Physical (interfaces)</th>
            <th scope="col">BGP adjacency</th>
            <th scope="col">RCA</th>
          </tr>
        </thead>
        <tbody>{fabric.map(renderRow)}</tbody>
        <tbody className="data-table__access">{access.map(renderRow)}</tbody>
      </table>
    </div>
  );
}
