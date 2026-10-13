import type { KeyboardEvent } from "react";
import type { IncidentResponse } from "../../api/types";
import type { NetworkStateResponse } from "../../api/networkStateTypes";
import {
  openIncidentsForDevice,
  summarizeRouterBgp,
  type NetworkSummary,
} from "../../network/derive";
import { LAB1_NODES, type TopologyNode } from "../../network/lab1Topology";
import type { DiagnosisLinkMark } from "../../network/diagnosisView";
import {
  adjacencyPresentation,
  diagnosisTypePresentation,
  physicalPresentation,
  type Presentation,
} from "../../network/presentation";

const NODE_W = 170;
const NODE_H = 88;
const VIEW_W = 760;
const VIEW_H = 480;

const POSITIONS: Record<string, { x: number; y: number }> = {
  "spine-1": { x: 150, y: 60 },
  "spine-2": { x: 610, y: 60 },
  "leaf-1": { x: 150, y: 240 },
  "leaf-2": { x: 610, y: 240 },
  "host-1": { x: 150, y: 410 },
  "host-2": { x: 610, y: 410 },
};

interface Point {
  x: number;
  y: number;
}

function lerp(from: Point, to: Point, t: number): Point {
  return { x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t };
}

/** A point just outside `from`'s box along the line toward `to`. */
function pointBeyondBox(from: Point, to: Point, gap: number): Point {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  const ux = dx / length;
  const uy = dy / length;
  const exit = Math.min(
    ux === 0 ? Infinity : NODE_W / 2 / Math.abs(ux),
    uy === 0 ? Infinity : NODE_H / 2 / Math.abs(uy),
  );
  return { x: from.x + ux * (exit + gap), y: from.y + uy * (exit + gap) };
}

interface TopologyDiagramProps {
  state: NetworkStateResponse;
  summary: NetworkSummary;
  incidents: readonly IncidentResponse[] | null;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  /**
   * Backend RCA marks keyed by declared-link id. Only links that already
   * exist in the declared topology can be marked; never a new edge.
   */
  diagnosisMarks?: ReadonlyMap<string, readonly DiagnosisLinkMark[]>;
}

const DIAGNOSIS_LABEL_ROWS: Record<DiagnosisLinkMark["type"], string> = {
  PHYSICAL_LINK_FAILURE: "RCA: link failure",
  BGP_SESSION_FAILURE: "RCA: BGP session",
  INSUFFICIENT_EVIDENCE: "RCA: insufficient",
};

function labelFor(p: Presentation): string {
  return `${p.glyph} ${p.label}`;
}

export function TopologyDiagram({
  state,
  summary,
  incidents,
  selectedNodeId,
  onSelect,
  diagnosisMarks,
}: TopologyDiagramProps) {
  const adjacencyByLink = new Map(summary.adjacencies.map((a) => [a.link.id, a]));
  const reachabilityByHost = new Map(summary.reachability.map((r) => [r.host, r]));

  function nodeLines(node: TopologyNode): { lines: string[]; aria: string } {
    if (node.role === "router") {
      const bgp = summarizeRouterBgp(state, node.id);
      const bgpLine = `BGP ${bgp.established}/${bgp.expected} Established${bgp.uncertain ? " ?" : ""}`;
      const aria = `${bgp.established} of ${bgp.expected} expected BGP adjacencies Established${
        bgp.uncertain ? ", some not explicitly observed" : ""
      }`;
      return { lines: [`${node.tier} · ${node.deviceId ?? ""}`, bgpLine], aria };
    }
    const reach = reachabilityByHost.get(node.id);
    if (reach === undefined || reach.probe === null) {
      return { lines: ["host", "reachability unavailable"], aria: "reachability unavailable" };
    }
    const line = `→ ${reach.probe.target} ${reach.probe.received}/${reach.probe.sent}`;
    return {
      lines: ["host", line],
      aria: `reachability ${reach.probe.received} of ${reach.probe.sent} replies from ${reach.probe.target}`,
    };
  }

  function onKeyDown(event: KeyboardEvent<SVGGElement>, nodeId: string) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(nodeId);
    }
  }

  return (
    <div className="topology-scroll">
      <svg
        className="topology"
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        role="group"
        aria-label="Lab 1 physical topology"
      >
        {/* Links: drawn first so node boxes sit on top of the line ends. */}
        {summary.links.map((entry) => {
          const a = POSITIONS[entry.link.a.node]!;
          const b = POSITIONS[entry.link.b.node]!;
          const presentation = physicalPresentation(entry.state);
          const marks = diagnosisMarks?.get(entry.link.id) ?? [];
          const markText = marks
            .map((mark) => `root-cause analysis: ${diagnosisTypePresentation(mark.type).label}`)
            .join("; ");
          return (
            <line
              key={entry.link.id}
              className={[
                "topology-link",
                `topology-link--${presentation.tone}`,
                marks.length > 0 ? "topology-link--diagnosed" : "",
                ...marks.map((mark) => `topology-link--rca-${mark.type.toLowerCase()}`),
              ]
                .filter(Boolean)
                .join(" ")}
              data-diagnosis={marks.map((mark) => mark.type).join(" ") || undefined}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              data-testid={`link-${entry.link.id}`}
            >
              <title>
                {`${entry.link.a.node}:${entry.link.a.interface} ↔ ${entry.link.b.node}:${entry.link.b.interface} — physical ${presentation.label}${
                  markText === "" ? "" : ` — ${markText}`
                }`}
              </title>
            </line>
          );
        })}

        {/* Link labels and interface names. */}
        {summary.links.map((entry) => {
          const from = POSITIONS[entry.link.a.node]!;
          const to = POSITIONS[entry.link.b.node]!;
          const adjacency = adjacencyByLink.get(entry.link.id);
          const isVertical = from.x === to.x;
          const mid = isVertical
            ? { x: from.x, y: entry.link.kind === "fabric" ? 150 : 325 }
            : lerp(from, to, 0.35);
          const physical = physicalPresentation(entry.state);
          const rows = [`Link ${labelFor(physical)}`];
          if (adjacency !== undefined) {
            rows.push(`BGP ${labelFor(adjacencyPresentation(adjacency.state))}`);
          }
          for (const mark of diagnosisMarks?.get(entry.link.id) ?? []) {
            rows.push(DIAGNOSIS_LABEL_ROWS[mark.type]);
          }
          const height = rows.length * 16 + 6;
          const aEnd = pointBeyondBox(from, to, 12);
          const bEnd = pointBeyondBox(to, from, 12);
          return (
            <g key={`label-${entry.link.id}`} className="topology-label-group">
              <rect
                className={`topology-label topology-label--${physical.tone}`}
                x={mid.x - 62}
                y={mid.y - height / 2}
                width={124}
                height={height}
                rx={4}
              />
              {rows.map((row, index) => (
                <text
                  key={row}
                  className="topology-label__text"
                  x={mid.x}
                  y={mid.y - height / 2 + 15 + index * 16}
                  textAnchor="middle"
                >
                  {row}
                </text>
              ))}
              <text className="topology-iface" x={aEnd.x} y={aEnd.y} textAnchor="middle">
                {entry.link.a.interface}
              </text>
              <text className="topology-iface" x={bEnd.x} y={bEnd.y} textAnchor="middle">
                {entry.link.b.interface}
              </text>
            </g>
          );
        })}

        {LAB1_NODES.map((node) => {
          const pos = POSITIONS[node.id]!;
          const { lines, aria } = nodeLines(node);
          const open = openIncidentsForDevice(incidents ?? [], node.deviceId);
          const selected = selectedNodeId === node.id;
          const badges = [
            selected ? "◆ selected" : null,
            open.length > 0 ? `⚠ ${open.length} open` : null,
          ]
            .filter((badge) => badge !== null)
            .join("  ");
          const ariaLabel = [
            node.id,
            node.role,
            node.deviceId ? `device ${node.deviceId}` : null,
            aria,
            open.length > 0
              ? `${open.length} open ${open.length === 1 ? "incident" : "incidents"}`
              : null,
          ]
            .filter((part) => part !== null)
            .join(", ");
          return (
            <g
              key={node.id}
              className={[
                "topology-node",
                `topology-node--${node.role}`,
                selected ? "topology-node--selected" : "",
                open.length > 0 ? "topology-node--incident" : "",
              ]
                .filter(Boolean)
                .join(" ")}
              role="button"
              tabIndex={0}
              aria-label={ariaLabel}
              aria-pressed={selected}
              onClick={() => onSelect(node.id)}
              onKeyDown={(event) => onKeyDown(event, node.id)}
              data-testid={`node-${node.id}`}
            >
              <rect
                className="topology-node__box"
                x={pos.x - NODE_W / 2}
                y={pos.y - NODE_H / 2}
                width={NODE_W}
                height={NODE_H}
                rx={8}
              />
              <text
                className="topology-node__name"
                x={pos.x - NODE_W / 2 + 12}
                y={pos.y - NODE_H / 2 + 20}
              >
                {node.id}
              </text>
              {lines.map((line, index) => (
                <text
                  key={line}
                  className="topology-node__line"
                  x={pos.x - NODE_W / 2 + 12}
                  y={pos.y - NODE_H / 2 + 38 + index * 16}
                >
                  {line}
                </text>
              ))}
              {badges !== "" && (
                <text
                  className="topology-node__badges"
                  x={pos.x - NODE_W / 2 + 12}
                  y={pos.y - NODE_H / 2 + 76}
                >
                  {badges}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
