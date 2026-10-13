import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { NetworkStateResponse } from "../api/networkStateTypes";
import type { IncidentResponse } from "../api/types";
import { LAB1_LINKS } from "../network/lab1Topology";
import { diagnosisResponse } from "../test/diagnosisFixtures";
import {
  BASELINE_NETWORK_STATE,
  DEGRADED_NETWORK_STATE,
  allBgpUnavailableState,
  failureIncidents,
  jsonResponse,
  notFoundNoSnapshotResponse,
  recoveredNetworkState,
  resolvedFailureIncidents,
  withNode,
} from "../test/networkFixtures";
import { NetworkWorkspace } from "./NetworkWorkspace";

type Responder = () => Response | Promise<Response>;

function stubApi(routes: { network: Responder; incidents: Responder; diagnosis?: Responder }) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const path = new URL(url).pathname;
    if (path === "/network/state") {
      return Promise.resolve(routes.network());
    }
    if (path === "/network/diagnosis") {
      // Default: an empty diagnosis for whatever the test serves as state.
      return Promise.resolve(
        routes.diagnosis === undefined
          ? jsonResponse(diagnosisResponse([], { collection_id: "unaligned-default" }))
          : routes.diagnosis(),
      );
    }
    if (path === "/incidents") {
      return Promise.resolve(routes.incidents());
    }
    throw new Error(`unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function stubOk(state: NetworkStateResponse, incidents: IncidentResponse[] = []) {
  return stubApi({
    network: () => jsonResponse(state),
    incidents: () => jsonResponse(incidents),
  });
}

function card(name: string): HTMLElement {
  return screen.getByRole("heading", { name }).closest(".summary-card") as HTMLElement;
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("healthy baseline", () => {
  test("renders six nodes and every declared physical link", async () => {
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("group", { name: "Lab 1 physical topology" });

    for (const id of ["spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2"]) {
      expect(screen.getByTestId(`node-${id}`)).toBeInTheDocument();
    }
    expect(LAB1_LINKS).toHaveLength(6);
    for (const link of LAB1_LINKS) {
      expect(screen.getByTestId(`link-${link.id}`)).toBeInTheDocument();
    }
    const table = screen.getByRole("table", { name: "Declared physical links" });
    expect(within(table).getAllByRole("row")).toHaveLength(7); // header + 6
  });

  test("summary strip: 4 / 4 Established, ECMP 2+2, reachability 3/3, 0 open, observation age", async () => {
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    expect(card("BGP adjacencies")).toHaveTextContent("4 / 4 Established");
    const ecmp = card("ECMP redundancy");
    expect(ecmp).toHaveTextContent("leaf-1: 2 paths");
    expect(ecmp).toHaveTextContent("leaf-2: 2 paths");
    const reach = card("Host reachability");
    expect(reach).toHaveTextContent("host-1 → 10.1.2.10 3/3");
    expect(reach).toHaveTextContent("host-2 → 10.1.1.10 3/3");
    expect(card("Open incidents")).toHaveTextContent("0");
    expect(card("Observation")).toHaveTextContent("Observed 3 s ago");
    expect(card("Observation")).toHaveTextContent("Collected");
    expect(card("Presentation status")).toHaveTextContent("Healthy");
  });

  test("does not render remediation language (RCA lives only in its own panel)", async () => {
    stubOk(DEGRADED_NETWORK_STATE, failureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(document.body.textContent).not.toMatch(/remediat/i);
  });
});

describe("degraded state with two device-scoped incidents", () => {
  test("shows 3 / 4, reduced ECMP, the real Active state, and no RCA grouping", async () => {
    stubOk(DEGRADED_NETWORK_STATE, failureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    expect(card("BGP adjacencies")).toHaveTextContent("3 / 4 Established");
    expect(card("BGP adjacencies")).toHaveTextContent("1 degraded");
    expect(card("ECMP redundancy")).toHaveTextContent("leaf-1: 1 path");
    expect(card("ECMP redundancy")).toHaveTextContent("Reduced");
    expect(card("Host reachability")).toHaveTextContent("3/3");
    expect(card("Open incidents")).toHaveTextContent("2");
    expect(card("Presentation status")).toHaveTextContent("Degraded");

    // The failed physical link no longer presents as UP; its BGP shows the real states.
    const table = screen.getByRole("table", { name: "Declared physical links" });
    const failedRow = within(table).getByRole("row", { name: /leaf-1:eth1 ↔ spine-1:eth1/ });
    expect(failedRow).toHaveTextContent("DOWN");
    expect(failedRow).toHaveTextContent("Degraded");
    expect(failedRow).toHaveTextContent("leaf-1: Active; spine-1: Active");

    // Two separate incident cards (no merging into one root cause).
    expect(await screen.findAllByRole("article")).toHaveLength(2);
  });

  test("routers with OPEN incidents are indicated on the node (text, not colour only)", async () => {
    stubOk(DEGRADED_NETWORK_STATE, failureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    await screen.findAllByRole("article");

    expect(screen.getByTestId("node-leaf-1")).toHaveAccessibleName(/1 open incident/);
    expect(screen.getByTestId("node-leaf-1")).toHaveTextContent("⚠ 1 open");
    expect(screen.getByTestId("node-spine-1")).toHaveTextContent("⚠ 1 open");
    expect(screen.getByTestId("node-spine-2")).not.toHaveTextContent("open");
    expect(screen.getByTestId("node-host-1")).not.toHaveTextContent("open");
  });

  test("incident cards expose severity, resource, rule, occurrences and status", async () => {
    stubOk(DEGRADED_NETWORK_STATE, failureIncidents());
    render(<NetworkWorkspace />);
    const [first] = await screen.findAllByRole("article");
    expect(first).toHaveTextContent("lab1-leaf-1");
    expect(first).toHaveTextContent("bgp-neighbor:10.255.0.0");
    expect(first).toHaveTextContent("Critical");
    expect(first).toHaveTextContent("OPEN");
    expect(first).toHaveTextContent("RULE-BGP-DOWN");
    expect(first).toHaveTextContent("Occurrences");
    expect(within(first!).getByRole("button", { name: "Resolve incident" })).toBeInTheDocument();
  });
});

describe("incident scope and endpoint skew in the workspace", () => {
  const unrelated = {
    ...failureIncidents()[0]!,
    incident_id: "unrelated-1",
    device_id: "spine-01",
    affected_resource: "bgp-neighbor:10.9.9.9",
  };

  test("network recovered while incidents still say OPEN: both shown, network stays recovered", async () => {
    stubOk(recoveredNetworkState(), failureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    await screen.findAllByRole("article");
    expect(card("Presentation status")).toHaveTextContent("Healthy");
    expect(card("BGP adjacencies")).toHaveTextContent("4 / 4 Established");
    expect(card("Open incidents")).toHaveTextContent("2");
  });

  test("unrelated incidents are neither listed nor counted, and do not change status", async () => {
    stubOk(BASELINE_NETWORK_STATE, [unrelated]);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(await screen.findByText("No incidents detected.")).toBeInTheDocument();
    expect(screen.queryByRole("article")).toBeNull();
    expect(card("Open incidents")).toHaveTextContent("0");
    expect(card("Presentation status")).toHaveTextContent("Healthy");
  });

  test("Lab 1 OPEN incidents appear in the workspace list", async () => {
    stubOk(DEGRADED_NETWORK_STATE, [...failureIncidents(), unrelated]);
    render(<NetworkWorkspace />);
    expect(await screen.findAllByRole("article")).toHaveLength(2);
  });
});

describe("recovery", () => {
  test("4 / 4 again, ECMP restored, incidents RESOLVED but still listed", async () => {
    stubOk(recoveredNetworkState(), resolvedFailureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(card("BGP adjacencies")).toHaveTextContent("4 / 4 Established");
    expect(card("ECMP redundancy")).toHaveTextContent("leaf-1: 2 paths");
    expect(card("Open incidents")).toHaveTextContent("0");
    const articles = await screen.findAllByRole("article");
    expect(articles).toHaveLength(2);
    expect(articles[0]).toHaveTextContent("RESOLVED");
    expect(screen.getByTestId("node-leaf-1")).not.toHaveTextContent("open");
  });
});

describe("node selection and detail", () => {
  test("clicking a router opens its own detail with interfaces, BGP, routes and unavailable facets", async () => {
    const user = userEvent.setup();
    stubOk(DEGRADED_NETWORK_STATE, failureIncidents());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    await user.click(screen.getByTestId("node-leaf-1"));
    const detail = screen.getByRole("complementary", { name: "leaf-1 details" });
    expect(detail).toHaveTextContent("device ID lab1-leaf-1");
    expect(within(detail).getByRole("heading", { name: "Interfaces" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "BGP" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Routes" })).toBeInTheDocument();
    expect(
      within(detail).getByRole("heading", { name: "Unavailable observations" }),
    ).toBeInTheDocument();
    expect(within(detail).queryByRole("heading", { name: "Reachability" })).toBeNull();

    const bgp = within(detail).getByRole("heading", { name: "BGP" }).closest("section")!;
    expect(bgp).toHaveTextContent("10.255.0.0");
    expect(bgp).toHaveTextContent("Active");
    expect(bgp).toHaveTextContent("n/a (not Established)"); // prefixes/uptime of a non-Established session
    expect(bgp).toHaveTextContent("spine-1");
    expect(detail).toHaveTextContent("1 OPEN incident on this device");
    expect(detail).toHaveTextContent("None: every applicable facet was observed.");
    // The failed interface is shown as down.
    expect(within(detail).getByText("eth1").closest("tr")).toHaveTextContent("DOWN");
    // Routes: the ECMP count and next hops are shown.
    const routes = within(detail).getByRole("heading", { name: "Routes" }).closest("section")!;
    expect(routes).toHaveTextContent("10.1.2.0/24");
    expect(routes).toHaveTextContent("10.255.0.2 via eth2");
  });

  test("clicking a host shows host-specific detail with no BGP or routes section", async () => {
    const user = userEvent.setup();
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    await user.click(screen.getByTestId("node-host-1"));
    const detail = screen.getByRole("complementary", { name: "host-1 details" });
    expect(within(detail).getByRole("heading", { name: "Interfaces" })).toBeInTheDocument();
    expect(within(detail).getByRole("heading", { name: "Reachability" })).toBeInTheDocument();
    expect(within(detail).queryByRole("heading", { name: "BGP" })).toBeNull();
    expect(within(detail).queryByRole("heading", { name: "Routes" })).toBeNull();
    expect(detail).toHaveTextContent("Not applicable to a host: BGP sessions and routes.");
    expect(detail).not.toHaveTextContent("device ID");
    expect(detail).toHaveTextContent("3/3");
  });

  test("the selected node is identifiable without colour, and keyboard operable", async () => {
    const user = userEvent.setup();
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    const spine = screen.getByTestId("node-spine-2");
    expect(spine).toHaveAttribute("aria-pressed", "false");
    spine.focus();
    await user.keyboard("{Enter}");
    expect(spine).toHaveAttribute("aria-pressed", "true");
    expect(spine).toHaveTextContent("◆ selected");
    expect(screen.getByRole("complementary", { name: "spine-2 details" })).toBeInTheDocument();

    await user.keyboard(" ");
    expect(spine).toHaveAttribute("aria-pressed", "true");
    await user.click(screen.getByTestId("node-leaf-2"));
    expect(spine).toHaveAttribute("aria-pressed", "false");
    expect(spine).not.toHaveTextContent("selected");
  });

  test("all nodes are keyboard reachable", async () => {
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    for (const id of ["spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2"]) {
      expect(screen.getByTestId(`node-${id}`)).toHaveAttribute("tabindex", "0");
    }
  });
});

describe("UNKNOWN and unavailable messaging", () => {
  test("loopback lo is shown faithfully as UNKNOWN and affects no physical link", async () => {
    const user = userEvent.setup();
    stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    await user.click(screen.getByTestId("node-leaf-1"));
    const detail = screen.getByRole("complementary", { name: "leaf-1 details" });
    const lo = within(detail).getByText("lo").closest("tr")!;
    expect(lo).toHaveTextContent("UNKNOWN");
    expect(lo).not.toHaveTextContent("UP");
    expect(lo).not.toHaveTextContent("DOWN");
    // lo is not an endpoint of any declared link, so every link stays UP.
    expect(LAB1_LINKS.some((l) => [l.a, l.b].some((e) => e.interface === "lo"))).toBe(false);
    expect(card("Presentation status")).toHaveTextContent("Healthy");
  });

  test("BGP facets unavailable are Unknown (not Down, not Established) and never Healthy", async () => {
    const user = userEvent.setup();
    stubOk(allBgpUnavailableState());
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    expect(card("BGP adjacencies")).toHaveTextContent("0 / 4 Established");
    expect(card("BGP adjacencies")).toHaveTextContent("4 unknown / not observed");
    expect(card("Presentation status")).toHaveTextContent("Unknown");
    expect(card("Presentation status")).not.toHaveTextContent("Healthy");
    expect(screen.getByTestId("node-leaf-1")).toHaveTextContent("BGP 0/2 Established ?");

    await user.click(screen.getByTestId("node-leaf-1"));
    const detail = screen.getByRole("complementary", { name: "leaf-1 details" });
    expect(detail).toHaveTextContent("Unavailable: the BGP facet was not observed");
    expect(detail).toHaveTextContent("bgp: vtysh command failed");
  });

  test("a BGP neighbor in state Unknown keeps its raw state", async () => {
    const user = userEvent.setup();
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      bgp: {
        ...node.bgp!,
        neighbors: node.bgp!.neighbors.map((n, index) =>
          index === 0
            ? {
                ...n,
                state: "Unknown" as const,
                raw_state: "Clearing",
                prefixes_received: null,
                uptime_ms: null,
              }
            : n,
        ),
      },
    }));
    stubOk(state);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    await user.click(screen.getByTestId("node-leaf-1"));
    const detail = screen.getByRole("complementary", { name: "leaf-1 details" });
    expect(detail).toHaveTextContent("raw_state: Clearing");
    expect(card("BGP adjacencies")).toHaveTextContent("1 unknown / not observed");
  });

  test("missing ECMP route is 'not observed', not 0 paths", async () => {
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      routes: node.routes!.filter((r) => r.prefix !== "10.1.2.0/24"),
    }));
    stubOk(state);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(card("ECMP redundancy")).toHaveTextContent("leaf-1: not observed");
    expect(card("ECMP redundancy")).not.toHaveTextContent("leaf-1: 0");
  });

  test("an unavailable reachability probe says unavailable, not failure", async () => {
    const state = withNode(BASELINE_NETWORK_STATE, "host-1", (node) => ({
      ...node,
      reachability: null,
      unavailable: [{ facet: "reachability", reason: "ping output unparseable" }],
    }));
    stubOk(state);
    const user = userEvent.setup();
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(card("Host reachability")).toHaveTextContent("host-1: probe unavailable");
    expect(card("Host reachability")).not.toHaveTextContent("Loss observed");

    await user.click(screen.getByTestId("node-host-1"));
    const detail = screen.getByRole("complementary", { name: "host-1 details" });
    expect(detail).toHaveTextContent("This is not a reachability failure.");
    expect(detail).toHaveTextContent("reachability: ping output unparseable");
  });

  test("an unavailable interfaces facet shows an UNKNOWN link with the reason", async () => {
    const state = withNode(BASELINE_NETWORK_STATE, "leaf-1", (node) => ({
      ...node,
      interfaces: null,
      unavailable: [{ facet: "interfaces", reason: "ip -j addr failed" }],
    }));
    stubOk(state);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    const table = screen.getByRole("table", { name: "Declared physical links" });
    const row = within(table).getByRole("row", { name: /leaf-1:eth1 ↔ spine-1:eth1/ });
    expect(row).toHaveTextContent("UNKNOWN");
    expect(row).toHaveTextContent("interfaces facet unavailable");
    expect(row).not.toHaveTextContent("UP");
  });
});

describe("no snapshot, errors, and refresh failures", () => {
  test("404 operational_state_not_found is a purposeful empty state, not an error", async () => {
    stubApi({ network: notFoundNoSnapshotResponse, incidents: () => jsonResponse([]) });
    render(<NetworkWorkspace />);
    expect(await screen.findByText("No live network snapshot yet.")).toBeInTheDocument();
    expect(screen.getByText(/Lab 1 collector\/ingestion workflow/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByTestId("node-leaf-1")).toBeNull();
    expect(document.body.textContent).not.toMatch(/healthy/i);
    // Incidents are independently available.
    expect(await screen.findByRole("heading", { name: "Incidents" })).toBeInTheDocument();
  });

  test("API unreachable and malformed responses are distinct errors", async () => {
    stubApi({
      network: () => {
        throw new TypeError("Failed to fetch");
      },
      incidents: () => jsonResponse([]),
    });
    const { unmount } = render(<NetworkWorkspace />);
    const unreachable = await screen.findByRole("alert");
    expect(unreachable).toHaveTextContent("API unreachable");
    unmount();

    stubApi({ network: () => jsonResponse({ nodes: [] }), incidents: () => jsonResponse([]) });
    render(<NetworkWorkspace />);
    const malformed = await screen.findByRole("alert");
    expect(malformed).toHaveTextContent("Malformed response");
    expect(malformed).not.toHaveTextContent("API unreachable");
  });

  test("a failed refresh keeps the previous snapshot visible with a non-destructive notice", async () => {
    const user = userEvent.setup();
    let failNext = false;
    stubApi({
      network: () => {
        if (failNext) {
          throw new TypeError("Failed to fetch");
        }
        return jsonResponse(BASELINE_NETWORK_STATE);
      },
      incidents: () => jsonResponse([]),
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    failNext = true;
    await user.click(screen.getByRole("button", { name: "Refresh all" }));
    expect(await screen.findByText(/Refresh failed: API unreachable/)).toBeInTheDocument();
    expect(screen.getByText(/Showing the last received snapshot/)).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Network summary" })).toBeInTheDocument();
    expect(card("BGP adjacencies")).toHaveTextContent("4 / 4 Established");
    expect(screen.getByTestId("node-leaf-1")).toBeInTheDocument();
  });

  test("an incident-list failure does not erase the network view, and no incidents is never assumed", async () => {
    stubApi({
      network: () => jsonResponse(BASELINE_NETWORK_STATE),
      incidents: () =>
        jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500),
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(card("Open incidents")).toHaveTextContent("Unavailable");
    // Network status comes from the network observations only.
    expect(card("Presentation status")).toHaveTextContent("Healthy");
    expect(await screen.findByText("Unable to load incidents.")).toBeInTheDocument();
  });
});

describe("existing incident actions remain available", () => {
  test("Resolve incident on an OPEN card calls the existing endpoint", async () => {
    const user = userEvent.setup();
    const [first] = failureIncidents();
    const resolved = {
      ...first!,
      status: "RESOLVED",
      resolved_at: "2026-10-07T12:05:00Z",
      updated_at: "2026-10-07T12:05:00Z",
    };
    const fetchMock = vi.fn().mockImplementation((url: string, init: RequestInit) => {
      const path = new URL(url).pathname;
      if (init.method === "POST") {
        return Promise.resolve(jsonResponse(resolved));
      }
      return Promise.resolve(
        path === "/network/state" ? jsonResponse(DEGRADED_NETWORK_STATE) : jsonResponse([first]),
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<NetworkWorkspace />);
    await user.click(await screen.findByRole("button", { name: "Resolve incident" }));
    expect(fetchMock).toHaveBeenCalledWith(
      `http://localhost:8080/incidents/${first!.incident_id}/resolve`,
      expect.objectContaining({ method: "POST" }),
    );
    expect(await screen.findByText("RESOLVED", { selector: ".badge" })).toBeInTheDocument();
  });

  test("the workspace issues only GET /network/state, /network/diagnosis, /incidents and the resolve POST", async () => {
    const fetchMock = stubOk(BASELINE_NETWORK_STATE);
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    const requests = fetchMock.mock.calls.map(
      ([url, init]) => `${(init as RequestInit).method} ${new URL(url as string).pathname}`,
    );
    expect(new Set(requests)).toEqual(
      new Set(["GET /network/state", "GET /network/diagnosis", "GET /incidents"]),
    );
  });
});
