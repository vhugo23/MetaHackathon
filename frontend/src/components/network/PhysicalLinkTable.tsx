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
 * (interface-derived) state and, for router-to-router links, the separate
 * BGP adjacency state with each endpoint's actual session state.
 */
export function PhysicalLinkTable({ links, adjacencies, diagnosisMarks }: PhysicalLinkTableProps) {
  const adjacencyByLink = new Map(adjacencies.map((a) => [a.link.id, a]));

  return (
    <div className="data-table-wrap">
      <table className="data-table">
        <caption>Declared physical links</caption>
        <thead>
          <tr>
            <th scope="col">Link</th>
            <th scope="col">Physical (interfaces)</th>
            <th scope="col">BGP adjacency</th>
          </tr>
        </thead>
        <tbody>
          {links.map((entry) => {
            const adjacency = adjacencyByLink.get(entry.link.id);
            const marks = diagnosisMarks?.get(entry.link.id) ?? [];
            return (
              <tr key={entry.link.id}>
                <th scope="row">
                  {entry.link.a.node}:{entry.link.a.interface} ↔ {entry.link.b.node}:
                  {entry.link.b.interface}
                  {marks.map((mark, index) => (
                    <span
                      key={`${mark.type}:${index}`}
                      className="cell-note rca-marker"
                      data-testid="rca-marker"
                    >
                      {mark.type === "INSUFFICIENT_EVIDENCE" ? "RCA" : "Probable root cause"}:{" "}
                      {diagnosisTypePresentation(mark.type).label} ·{" "}
                      {confidenceText(mark.confidence)}
                    </span>
                  ))}
                </th>
                <td>
                  <StateBadge presentation={physicalPresentation(entry.state)} />
                  <span className="cell-note">
                    {entry.a.node} {entry.a.state}
                    {entry.a.reason === "reported"
                      ? ""
                      : ` (${interfaceReasonText(entry.a.reason)})`}
                    ; {entry.b.node} {entry.b.state}
                    {entry.b.reason === "reported"
                      ? ""
                      : ` (${interfaceReasonText(entry.b.reason)})`}
                  </span>
                </td>
                <td>
                  {adjacency === undefined ? (
                    <span className="cell-note">Not applicable (host link)</span>
                  ) : (
                    <>
                      <StateBadge presentation={adjacencyPresentation(adjacency.state)} />
                      <span className="cell-note">
                        {adjacency.a.node}: {bgpEndpointPresentation(adjacency.a.observation).label}
                        ; {adjacency.b.node}:{" "}
                        {bgpEndpointPresentation(adjacency.b.observation).label}
                      </span>
                    </>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
