import type {
  NetworkBgpNeighbor,
  NetworkNode,
  NetworkStateResponse,
} from "../../api/networkStateTypes";
import type { IncidentResponse } from "../../api/types";
import { findNode, openIncidentsForDevice } from "../../network/derive";
import { LAB1_LINKS, nodeById } from "../../network/lab1Topology";
import { bgpSessionPresentation, interfaceStatePresentation } from "../../network/presentation";
import { StateBadge } from "./StateBadge";

interface NodeDetailPanelProps {
  nodeId: string;
  state: NetworkStateResponse;
  incidents: readonly IncidentResponse[] | null;
}

/** The declared peer of a router's BGP neighbor IP, from the fixed addressing contract. */
function expectedPeerName(routerId: string, neighborIp: string): string | null {
  for (const link of LAB1_LINKS) {
    if (link.kind !== "fabric") {
      continue;
    }
    if (link.a.node === routerId && link.b.address === neighborIp) {
      return link.b.node;
    }
    if (link.b.node === routerId && link.a.address === neighborIp) {
      return link.a.node;
    }
  }
  return null;
}

function formatUptime(ms: number): string {
  const totalSeconds = Math.floor(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours} h ${minutes} min`;
  }
  return minutes > 0 ? `${minutes} min ${seconds} s` : `${seconds} s`;
}

function neighborOptional(
  neighbor: NetworkBgpNeighbor,
  value: number | null,
  format: (v: number) => string,
): string {
  if (value !== null) {
    return format(value);
  }
  return neighbor.state === "Established" ? "not reported" : "n/a (not Established)";
}

function InterfacesSection({ node }: { node: NetworkNode }) {
  return (
    <section aria-labelledby={`detail-interfaces-${node.node_id}`}>
      <h4 id={`detail-interfaces-${node.node_id}`}>Interfaces</h4>
      {node.interfaces === null ? (
        <p className="detail-note">
          Unavailable: the interfaces facet was not observed (see Unavailable observations).
        </p>
      ) : node.interfaces.length === 0 ? (
        <p className="detail-note">Observed: the device reported no interfaces.</p>
      ) : (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Name</th>
                <th scope="col">oper_state</th>
                <th scope="col">Addresses</th>
              </tr>
            </thead>
            <tbody>
              {node.interfaces.map((iface) => (
                <tr key={iface.name}>
                  <th scope="row">{iface.name}</th>
                  <td>
                    <StateBadge presentation={interfaceStatePresentation(iface.oper_state)} />
                  </td>
                  <td className="mono">
                    {iface.addresses.length === 0 ? "none reported" : iface.addresses.join(", ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function BgpSection({ node }: { node: NetworkNode }) {
  return (
    <section aria-labelledby={`detail-bgp-${node.node_id}`}>
      <h4 id={`detail-bgp-${node.node_id}`}>BGP</h4>
      {node.bgp === null ? (
        <p className="detail-note">
          Unavailable: the BGP facet was not observed (see Unavailable observations).
        </p>
      ) : (
        <>
          <p className="detail-note">
            Local AS {node.bgp.local_as} · router ID {node.bgp.router_id} ·{" "}
            {node.bgp.established_count} of {node.bgp.neighbors.length} reported neighbors
            Established
          </p>
          <div className="data-table-wrap">
            <table className="data-table">
              <thead>
                <tr>
                  <th scope="col">Neighbor</th>
                  <th scope="col">State</th>
                  <th scope="col">Remote AS</th>
                  <th scope="col">Prefixes received</th>
                  <th scope="col">Uptime</th>
                </tr>
              </thead>
              <tbody>
                {node.bgp.neighbors.map((neighbor) => {
                  const peer = expectedPeerName(node.node_id, neighbor.neighbor_ip);
                  const rawDiffers =
                    neighbor.raw_state !== null && neighbor.raw_state !== neighbor.state;
                  return (
                    <tr key={neighbor.neighbor_ip}>
                      <th scope="row">
                        <span className="mono">{neighbor.neighbor_ip}</span>
                        {peer !== null && <span className="cell-note">{peer}</span>}
                      </th>
                      <td>
                        <StateBadge presentation={bgpSessionPresentation(neighbor.state)} />
                        {rawDiffers && (
                          <span className="cell-note">raw_state: {neighbor.raw_state}</span>
                        )}
                        {neighbor.raw_state === null && neighbor.state === "Unknown" && (
                          <span className="cell-note">raw_state not reported</span>
                        )}
                      </td>
                      <td>{neighbor.remote_as === null ? "not reported" : neighbor.remote_as}</td>
                      <td>
                        {neighborOptional(neighbor, neighbor.prefixes_received, (v) => String(v))}
                      </td>
                      <td>{neighborOptional(neighbor, neighbor.uptime_ms, formatUptime)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

function RoutesSection({ node }: { node: NetworkNode }) {
  return (
    <section aria-labelledby={`detail-routes-${node.node_id}`}>
      <h4 id={`detail-routes-${node.node_id}`}>Routes</h4>
      {node.routes === null ? (
        <p className="detail-note">
          Unavailable: the routes facet was not observed (see Unavailable observations).
        </p>
      ) : node.routes.length === 0 ? (
        <p className="detail-note">Observed: the device reported no routes.</p>
      ) : (
        <div className="data-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Prefix</th>
                <th scope="col">Protocol</th>
                <th scope="col">ECMP paths</th>
                <th scope="col">Next hops</th>
              </tr>
            </thead>
            <tbody>
              {node.routes.map((route) => (
                <tr key={`${route.prefix}|${route.protocol}`}>
                  <th scope="row" className="mono">
                    {route.prefix}
                  </th>
                  <td>{route.protocol}</td>
                  <td>{route.ecmp_path_count}</td>
                  <td className="mono">
                    {route.next_hops
                      .map((hop) =>
                        [hop.ip, hop.interface !== null ? `via ${hop.interface}` : null]
                          .filter((part) => part !== null)
                          .join(" "),
                      )
                      .join("; ")}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ReachabilitySection({ node }: { node: NetworkNode }) {
  return (
    <section aria-labelledby={`detail-reachability-${node.node_id}`}>
      <h4 id={`detail-reachability-${node.node_id}`}>Reachability</h4>
      {node.reachability === null ? (
        <p className="detail-note">
          Unavailable: the probe was not observed. This is not a reachability failure.
        </p>
      ) : (
        <ul className="detail-list">
          {node.reachability.map((probe) => (
            <li key={`${probe.from_node}->${probe.target}`}>
              {probe.from_node} → <span className="mono">{probe.target}</span>:{" "}
              <span className="mono">
                {probe.received}/{probe.sent}
              </span>{" "}
              replies ({probe.succeeded ? "succeeded" : "loss observed"})
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function NodeDetailPanel({ nodeId, state, incidents }: NodeDetailPanelProps) {
  const declared = nodeById(nodeId);
  const node = findNode(state, nodeId);
  const open = openIncidentsForDevice(incidents ?? [], declared?.deviceId ?? null);

  return (
    <aside className="node-detail" aria-label={`${nodeId} details`} tabIndex={0}>
      <div className="node-detail__head">
        <h3>{nodeId}</h3>
        <span className="node-detail__status">
          {node === null ? "Not observed" : "Observed in snapshot"}
        </span>
      </div>
      {node === null ? (
        <p className="detail-note">
          Not observed: this node is not present in the current snapshot. Nothing is assumed about
          it.
        </p>
      ) : (
        <>
          <p className="node-detail__kind">
            {node.role === "router"
              ? `${declared?.tier === "spine" ? "Spine" : "Leaf"} router`
              : "Host"}
            {node.bgp !== null && (
              <>
                {" · "}
                <span className="mono">AS {node.bgp.local_as}</span>
              </>
            )}
          </p>
          {node.device_id !== null && (
            <p className="detail-note">
              Registered with device ID <span className="mono">{node.device_id}</span>
            </p>
          )}

          {node.role === "router" && (
            <dl className="node-detail__stats" aria-label="Overview">
              <div>
                <dt>BGP Established</dt>
                <dd>
                  {node.bgp === null
                    ? "unavailable"
                    : `${node.bgp.established_count} / ${node.bgp.neighbors.length}`}
                </dd>
              </div>
              <div>
                <dt>Open incidents</dt>
                <dd>{open.length}</dd>
              </div>
            </dl>
          )}

          {open.length > 0 && (
            <p className="detail-incident">
              ⚠ {open.length} OPEN {open.length === 1 ? "incident" : "incidents"} on this device:{" "}
              {open.map((i) => `${i.rule_ref} (${i.severity}) ${i.affected_resource}`).join("; ")}
            </p>
          )}

          <InterfacesSection node={node} />
          {node.role === "router" && <BgpSection node={node} />}
          {node.role === "router" && <RoutesSection node={node} />}
          {node.role === "host" && <ReachabilitySection node={node} />}
          {node.role === "host" && (
            <p className="detail-note">Not applicable to a host: BGP sessions and routes.</p>
          )}

          <section aria-labelledby={`detail-unavailable-${node.node_id}`}>
            <h4 id={`detail-unavailable-${node.node_id}`}>Unavailable observations</h4>
            {node.unavailable.length === 0 ? (
              <p className="detail-note">None: every applicable facet was observed.</p>
            ) : (
              <ul className="detail-list">
                {node.unavailable.map((item) => (
                  <li key={item.facet}>
                    <strong>{item.facet}</strong>: {item.reason}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </aside>
  );
}
