import type { KeyboardEvent } from "react";
import type { IncidentResponse } from "../../api/types";
import type { NetworkStateResponse } from "../../api/networkStateTypes";
import {
  findNode,
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

const NODE_W = 190;
const NODE_H = 100;
const VIEW_W = 840;
const VIEW_H = 640;

const POSITIONS: Record<string, { x: number; y: number }> = {
  "spine-1": { x: 115, y: 70 },
  "spine-2": { x: 725, y: 70 },
  "leaf-1": { x: 115, y: 320 },
  "leaf-2": { x: 725, y: 320 },
  "host-1": { x: 115, y: 570 },
  "host-2": { x: 725, y: 570 },
};

const ROLE_LABELS = {
  spine: "Spine router",
  leaf: "Leaf router",
  host: "Host",
} as const;

type LinkTone = "ok" | "bad" | "control" | "unknown";

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

/**
 * Visual class of a declared link. A failed physical link (red) is kept
 * distinct from a degraded BGP session over interfaces that are UP (amber,
 * dashed control-plane treatment); anything not positively observed is
 * grey and dashed.
 */
function linkTone(physical: Presentation, adjacency: Presentation | undefined): LinkTone {
  if (physical.tone === "bad") {
    return "bad";
  }
  if (physical.tone !== "ok") {
    return "unknown";
  }
  if (adjacency === undefined || adjacency.tone === "ok") {
    return "ok";
  }
  return adjacency.tone === "warn" ? "control" : "unknown";
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

  function nodeLines(node: TopologyNode): { role: string; line: string; aria: string } {
    if (node.role === "router") {
      const bgp = summarizeRouterBgp(state, node.id);
      const localAs = findNode(state, node.id)?.bgp?.local_as;
      const role = `${ROLE_LABELS[node.tier]}${localAs === undefined ? "" : ` · AS ${localAs}`}`;
      const line = `BGP ${bgp.established}/${bgp.expected} Established${bgp.uncertain ? " ?" : ""}`;
      const aria = `${bgp.established} of ${bgp.expected} expected BGP adjacencies Established${
        bgp.uncertain ? ", some not explicitly observed" : ""
      }`;
      return { role, line, aria };
    }
    const reach = reachabilityByHost.get(node.id);
    if (reach === undefined || reach.probe === null) {
      return {
        role: ROLE_LABELS.host,
        line: "reachability unavailable",
        aria: "reachability unavailable",
      };
    }
    return {
      role: ROLE_LABELS.host,
      line: `→ ${reach.probe.target} ${reach.probe.received}/${reach.probe.sent}`,
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
          const adjacency = adjacencyByLink.get(entry.link.id);
          const adjacencyView =
            adjacency === undefined ? undefined : adjacencyPresentation(adjacency.state);
          const tone = linkTone(presentation, adjacencyView);
          const marks = diagnosisMarks?.get(entry.link.id) ?? [];
          const markText = marks
            .map((mark) => `root-cause analysis: ${diagnosisTypePresentation(mark.type).label}`)
            .join("; ");
          return (
            <line
              key={entry.link.id}
              className={[
                "topology-link",
                `topology-link--${tone}`,
                entry.link.kind === "access" ? "topology-link--access" : "",
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

        {/* Link status chips and interface names. */}
        {summary.links.map((entry) => {
          const from = POSITIONS[entry.link.a.node]!;
          const to = POSITIONS[entry.link.b.node]!;
          const adjacency = adjacencyByLink.get(entry.link.id);
          const isVertical = from.x === to.x;
          // Diagonal fabric links cross at their midpoint, so their chips sit
          // on the leaf side of the crossing, never on top of each other.
          const mid = isVertical ? lerp(from, to, 0.5) : lerp(from, to, 0.33);
          const physical = physicalPresentation(entry.state);
          const adjacencyView =
            adjacency === undefined ? undefined : adjacencyPresentation(adjacency.state);
          const tone = linkTone(physical, adjacencyView);
          const marks = diagnosisMarks?.get(entry.link.id) ?? [];
          // Healthy links carry only a minimal chip ("✓ UP", "✓ Established");
          // anything else keeps the full, prefixed detail. The Physical Links
          // table and Selected Node hold the complete observations.
          const quiet = tone === "ok" && marks.length === 0;
          const rows = quiet ? [labelFor(physical)] : [`Link ${labelFor(physical)}`];
          if (adjacencyView !== undefined) {
            rows.push(quiet ? labelFor(adjacencyView) : `BGP ${labelFor(adjacencyView)}`);
          }
          for (const mark of marks) {
            rows.push(DIAGNOSIS_LABEL_ROWS[mark.type]);
          }
          const chipWidth = quiet ? 100 : 140;
          const rowGap = 17;
          const height = rows.length * rowGap + 8;
          const aEnd = pointBeyondBox(from, to, 16);
          const bEnd = pointBeyondBox(to, from, 16);
          return (
            <g key={`label-${entry.link.id}`} className="topology-label-group">
              <rect
                className={`topology-label topology-label--${tone}${quiet ? " topology-label--quiet" : ""}`}
                x={mid.x - chipWidth / 2}
                y={mid.y - height / 2}
                width={chipWidth}
                height={height}
                rx={5}
              />
              {rows.map((row, index) => (
                <text
                  key={row}
                  className="topology-label__text"
                  x={mid.x}
                  y={mid.y - height / 2 + 16 + index * rowGap}
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
          const { role, line, aria } = nodeLines(node);
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
          const left = pos.x - NODE_W / 2 + 16;
          const top = pos.y - NODE_H / 2;
          return (
            <g
              key={node.id}
              className={[
                "topology-node",
                `topology-node--${node.role}`,
                `topology-node--${node.tier}`,
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
                y={top}
                width={NODE_W}
                height={NODE_H}
                rx={8}
              />
              <text className="topology-node__name" x={left} y={top + 28}>
                {node.id}
              </text>
              <text className="topology-node__role" x={left} y={top + 49}>
                {role}
              </text>
              <text className="topology-node__line" x={left} y={top + 70}>
                {line}
              </text>
              {badges !== "" && (
                <text className="topology-node__badges" x={left} y={top + 90}>
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
