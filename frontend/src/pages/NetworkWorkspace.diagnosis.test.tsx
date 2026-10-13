import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import type { Diagnosis, NetworkDiagnosisResponse } from "../api/networkDiagnosisTypes";
import { LAB1_LINKS } from "../network/lab1Topology";
import {
  DEGRADED_COLLECTION_ID,
  bgpDiagnosis,
  diagnosisResponse,
  healthyDiagnosis,
  insufficientDiagnosis,
  linkedIncident,
  physicalDiagnosis,
} from "../test/diagnosisFixtures";
import {
  BASELINE_NETWORK_STATE,
  DEGRADED_NETWORK_STATE,
  failureIncidents,
  jsonResponse,
  notFoundNoSnapshotResponse,
} from "../test/networkFixtures";
import { NetworkWorkspace } from "./NetworkWorkspace";

const PHYSICAL_LINK_ID = "leaf-1:eth1--spine-1:eth1";

type Responder = () => Response | Promise<Response>;

function stubApi(routes: { network: Responder; diagnosis: Responder; incidents?: Responder }) {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const path = new URL(url).pathname;
    if (path === "/network/state") {
      return Promise.resolve(routes.network());
    }
    if (path === "/network/diagnosis") {
      return Promise.resolve(routes.diagnosis());
    }
    if (path === "/incidents") {
      return Promise.resolve(routes.incidents ? routes.incidents() : jsonResponse([]));
    }
    throw new Error(`unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** The degraded state, with `diagnoses` aligned to it (override to misalign). */
function stubDegraded(diagnoses: Diagnosis[], overrides: Partial<NetworkDiagnosisResponse> = {}) {
  return stubApi({
    network: () => jsonResponse(DEGRADED_NETWORK_STATE),
    diagnosis: () => jsonResponse(diagnosisResponse(diagnoses, overrides)),
    incidents: () => jsonResponse(failureIncidents()),
  });
}

function rcaPanel(): HTMLElement {
  return screen.getByRole("region", { name: "Root-cause analysis" });
}

async function renderDegraded(diagnoses: Diagnosis[], overrides = {}) {
  stubDegraded(diagnoses, overrides);
  render(<NetworkWorkspace />);
  await screen.findByRole("region", { name: "Network summary" });
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

describe("A. healthy aligned snapshot", () => {
  test("shows 'No active root-cause diagnosis' and not 'No problems'", async () => {
    stubApi({
      network: () => jsonResponse(BASELINE_NETWORK_STATE),
      diagnosis: () => jsonResponse(healthyDiagnosis()),
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    const panel = rcaPanel();
    expect(await within(panel).findByText("No active root-cause diagnosis")).toBeInTheDocument();
    expect(panel).not.toHaveTextContent(/no problems/i);
    expect(within(panel).queryAllByRole("listitem")).toHaveLength(0);
  });
});

describe("B. physical-link diagnosis", () => {
  test("shows type, confidence, declared link and the raw type", async () => {
    await renderDegraded([physicalDiagnosis()]);
    const panel = rcaPanel();

    expect(await within(panel).findByText("Physical link failure")).toBeInTheDocument();
    expect(panel).toHaveTextContent("HIGH");
    expect(panel).toHaveTextContent("leaf-1 eth1 ↔ spine-1 eth1");
    expect(panel).toHaveTextContent("PHYSICAL_LINK_FAILURE");
    expect(panel).toHaveTextContent(PHYSICAL_LINK_ID); // secondary resource_id
    expect(panel).toHaveTextContent(/4 supporting · 1 contradicting · 1 unknown/);
  });

  test("never shows confidence as a percentage or probability", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");
    expect(rcaPanel().textContent).not.toMatch(/\d\s?%|probab/i);
    expect(rcaPanel().querySelector("progress, meter, [role='progressbar']")).toBeNull();
  });
});

describe("C. BGP session diagnosis", () => {
  test("does not claim a physical link failure", async () => {
    await renderDegraded([bgpDiagnosis()]);
    const panel = rcaPanel();
    expect(await within(panel).findByText("BGP session failure")).toBeInTheDocument();
    expect(panel).toHaveTextContent("MEDIUM");
    expect(panel).toHaveTextContent("Declared adjacency");
    expect(panel).toHaveTextContent("leaf-1 eth1 ↔ spine-1 eth1");
    expect(panel).not.toHaveTextContent(/physical link failure/i);
    expect(panel).not.toHaveTextContent("PHYSICAL_LINK_FAILURE");
  });
});

describe("D. INSUFFICIENT_EVIDENCE", () => {
  test("stays visible with 'Confidence unavailable' and its UNKNOWN / CONTRADICTS evidence", async () => {
    await renderDegraded([insufficientDiagnosis()]);
    const panel = rcaPanel();
    expect(await within(panel).findByText("Insufficient evidence")).toBeInTheDocument();
    expect(panel).toHaveTextContent("Confidence unavailable");
    expect(panel).toHaveTextContent(
      "Symptoms are present, but the current observations do not support a definitive physical-link or BGP-session attribution.",
    );
    // Evidence details are open by default for an inconclusive diagnosis.
    const details = panel.querySelector("details")!;
    expect(details).toHaveAttribute("open");
    expect(within(details).getByRole("heading", { name: "Unknown (1)" })).toBeInTheDocument();
    expect(within(details).getByRole("heading", { name: "Contradicts (1)" })).toBeInTheDocument();
    expect(details).toHaveTextContent("leaf-1 eth1 — UNKNOWN");
    expect(details).toHaveTextContent("spine-1 → 10.255.0.1 — Established");
    expect(within(details).queryByRole("heading", { name: /^Supports/ })).toBeNull();
  });
});

describe("E. zero linked incidents", () => {
  test("the diagnosis stays visible with a secondary note", async () => {
    await renderDegraded([physicalDiagnosis({ linked_incidents: [] })]);
    const panel = rcaPanel();
    expect(await within(panel).findByText("Physical link failure")).toBeInTheDocument();
    expect(panel).toHaveTextContent("HIGH");
    expect(panel).toHaveTextContent("Correlated symptoms (0)");
    expect(panel).toHaveTextContent("No matching open incident records");
    expect(within(panel).queryAllByTestId("correlated-symptom")).toHaveLength(0);
  });
});

describe("F. two linked incidents / one linked incident", () => {
  test("two correlated symptoms are listed independently, not as causes", async () => {
    await renderDegraded([physicalDiagnosis()]);
    const panel = rcaPanel();
    await within(panel).findByText("Physical link failure");
    const symptoms = within(panel).getAllByTestId("correlated-symptom");
    expect(symptoms).toHaveLength(2);
    expect(symptoms[0]).toHaveTextContent("lab1-leaf-1 · bgp-neighbor:10.255.0.0");
    expect(symptoms[0]).toHaveTextContent("Critical");
    expect(symptoms[0]).toHaveTextContent("OPEN");
    expect(symptoms[0]).toHaveTextContent("seen 1×");
    expect(symptoms[1]).toHaveTextContent("lab1-spine-1 · bgp-neighbor:10.255.0.1");
    expect(panel).toHaveTextContent("Correlated symptoms (2)");
    expect(panel).not.toHaveTextContent(/causes/i);
  });

  test("one correlated symptom", async () => {
    await renderDegraded([
      physicalDiagnosis({ linked_incidents: [linkedIncident("lab1-leaf-1", "10.255.0.0")] }),
    ]);
    const panel = rcaPanel();
    await within(panel).findByText("Physical link failure");
    expect(within(panel).getAllByTestId("correlated-symptom")).toHaveLength(1);
  });

  test("the occurrence count of a linked incident is shown", async () => {
    await renderDegraded([
      physicalDiagnosis({
        linked_incidents: [linkedIncident("lab1-leaf-1", "10.255.0.0", { occurrence_count: 7 })],
      }),
    ]);
    expect(await within(rcaPanel()).findByTestId("correlated-symptom")).toHaveTextContent(
      "seen 7×",
    );
  });
});

describe("evidence presentation", () => {
  test("is collapsed by default for a confident diagnosis and grouped by role when opened", async () => {
    const user = userEvent.setup();
    await renderDegraded([physicalDiagnosis()]);
    const panel = rcaPanel();
    await within(panel).findByText("Physical link failure");

    const details = panel.querySelector("details")!;
    expect(details).not.toHaveAttribute("open");
    await user.click(within(details).getByText("Evidence details (6)"));
    expect(details).toHaveAttribute("open");

    expect(within(details).getByRole("heading", { name: "Supports (4)" })).toBeInTheDocument();
    expect(within(details).getByRole("heading", { name: "Contradicts (1)" })).toBeInTheDocument();
    expect(within(details).getByRole("heading", { name: "Unknown (1)" })).toBeInTheDocument();
    expect(details).toHaveTextContent("leaf-1 eth1 — DOWN");
    expect(details).toHaveTextContent("leaf-1 → 10.255.0.0 — Active");
    expect(details).toHaveTextContent("leaf-1: 10.1.2.0/24 — 1 of 2 paths");
    expect(details).toHaveTextContent("path via 10.255.0.0 absent");
    expect(details).toHaveTextContent("leaf-1: 10.255.0.0 — absent from active routes");
    expect(details).toHaveTextContent("host-1 → 10.1.2.10 — 3/3 received");
    expect(details).toHaveTextContent(
      "spine-1 eth1: declared and observed interface address match",
    );
    expect(details).toHaveTextContent("Symptom keys (2)");
    expect(details.textContent).not.toMatch(/proves|caused by|because/i);
  });

  test("the evidence disclosure is a native <details>/<summary> (keyboard toggle proven in the browser E2E)", async () => {
    await renderDegraded([physicalDiagnosis()]);
    const summary = await within(rcaPanel()).findByText("Evidence details (6)");
    expect(summary.tagName).toBe("SUMMARY");
    expect(summary.parentElement?.tagName).toBe("DETAILS");
    summary.focus();
    expect(summary).toHaveFocus();
  });

  test("never dumps raw JSON", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");
    expect(rcaPanel().textContent).not.toMatch(/[{}]|"kind"|observed_paths/);
  });
});

describe("G. multiple diagnoses", () => {
  test("all are rendered in API order, with equal treatment, never re-ranked", async () => {
    await renderDegraded([insufficientDiagnosis(), physicalDiagnosis(), bgpDiagnosis()]);
    const panel = rcaPanel();
    await within(panel).findByText("Insufficient evidence");
    const cards = panel.querySelectorAll<HTMLElement>("li.diagnosis-card");
    expect([...cards].map((c) => c.dataset.diagnosisType)).toEqual([
      "INSUFFICIENT_EVIDENCE",
      "PHYSICAL_LINK_FAILURE",
      "BGP_SESSION_FAILURE",
    ]);
    expect(panel).not.toHaveTextContent(/most likely|primary diagnosis/i);
  });

  test("two diagnoses on one link are both listed and both marked in the table", async () => {
    await renderDegraded([bgpDiagnosis(), physicalDiagnosis()]);
    await within(rcaPanel()).findByText("BGP session failure");
    const row = within(screen.getByRole("table", { name: "Declared physical links" })).getByRole(
      "row",
      { name: new RegExp(`leaf-1:eth1 ↔ spine-1:eth1`) },
    );
    const markers = within(row).getAllByTestId("rca-marker");
    expect(markers.map((m) => m.textContent)).toEqual([
      "Probable root cause: BGP session failure · MEDIUM confidence",
      "Probable root cause: Physical link failure · HIGH confidence",
    ]);
  });
});

describe("H. collection mismatch", () => {
  test("a diagnosis from another collection is not presented as current", async () => {
    await renderDegraded([physicalDiagnosis()], { collection_id: "collection-older" });
    const panel = rcaPanel();
    expect(await within(panel).findByText("Diagnosis updating.")).toBeInTheDocument();
    expect(panel).toHaveTextContent("Diagnosis unavailable for the current snapshot");
    expect(panel).not.toHaveTextContent("Physical link failure");
    expect(panel).not.toHaveTextContent("No active root-cause diagnosis");
    expect(within(panel).queryAllByTestId("correlated-symptom")).toHaveLength(0);
    // No link is marked from a mismatched diagnosis.
    expect(document.querySelector("[data-diagnosis]")).toBeNull();
    expect(screen.queryAllByTestId("rca-marker")).toHaveLength(0);
  });

  test("a healthy (empty) diagnosis for another collection is not shown as 'no diagnosis'", async () => {
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: () => jsonResponse(healthyDiagnosis()), // belongs to collection-baseline
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    const panel = rcaPanel();
    expect(await within(panel).findByText("Diagnosis updating.")).toBeInTheDocument();
    expect(panel).not.toHaveTextContent("No active root-cause diagnosis");
  });

  test("the network-state workspace is unaffected by the mismatch", async () => {
    await renderDegraded([physicalDiagnosis()], { collection_id: "collection-older" });
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Declared physical links" })).toBeInTheDocument();
  });
});

describe("loading, 404 and error states (I)", () => {
  test("while the diagnosis loads, the network workspace is already rendered", async () => {
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: () => new Promise<Response>(() => undefined), // never settles
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
    expect(within(rcaPanel()).getByText("Loading diagnosis…")).toBeInTheDocument();
  });

  test("a diagnosis API error is local: topology, table, summary and incidents stay", async () => {
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: () =>
        jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500),
      incidents: () => jsonResponse(failureIncidents()),
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });

    const alert = await within(rcaPanel()).findByRole("alert");
    expect(alert).toHaveTextContent("Diagnosis unavailable.");
    expect(alert).toHaveTextContent("Database failure.");
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
    expect(screen.getByRole("table", { name: "Declared physical links" })).toBeInTheDocument();
    expect(await screen.findAllByRole("article")).toHaveLength(2);
    // Only the RCA panel carries the error; the workspace has no other alert.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
  });

  test("a transport failure of the diagnosis request is also local", async () => {
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: () => Promise.reject(new TypeError("Failed to fetch")),
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    expect(await within(rcaPanel()).findByRole("alert")).toHaveTextContent(
      "Diagnosis unavailable.",
    );
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
  });

  test("the error state offers a retry that re-requests the diagnosis", async () => {
    const user = userEvent.setup();
    let calls = 0;
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: () => {
        calls += 1;
        return calls === 1
          ? jsonResponse({ code: "persistence_error", detail: "Database failure." }, 500)
          : jsonResponse(diagnosisResponse([physicalDiagnosis()]));
      },
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    await user.click(await screen.findByRole("button", { name: "Retry diagnosis" }));
    expect(await within(rcaPanel()).findByText("Physical link failure")).toBeInTheDocument();
  });

  test("diagnosis 404 while state exists: restrained unavailable state, workspace stays", async () => {
    stubApi({
      network: () => jsonResponse(DEGRADED_NETWORK_STATE),
      diagnosis: notFoundNoSnapshotResponse,
    });
    render(<NetworkWorkspace />);
    await screen.findByRole("region", { name: "Network summary" });
    const panel = rcaPanel();
    expect(await within(panel).findByText("Diagnosis unavailable.")).toBeInTheDocument();
    expect(within(panel).queryByRole("alert")).toBeNull();
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
  });

  test("404 on both: the existing no-snapshot workspace, no RCA error", async () => {
    stubApi({ network: notFoundNoSnapshotResponse, diagnosis: notFoundNoSnapshotResponse });
    render(<NetworkWorkspace />);
    expect(await screen.findByText("No live network snapshot yet.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Root-cause analysis" })).toBeNull();
    expect(screen.queryByText(/diagnosis unavailable/i)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

describe("topology highlighting", () => {
  test("PHYSICAL_LINK_FAILURE highlights exactly the declared leaf-1 eth1 <-> spine-1 eth1 link", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");

    const marked = [...document.querySelectorAll("[data-diagnosis]")];
    expect(marked).toHaveLength(1);
    expect(marked[0]).toBe(screen.getByTestId(`link-${PHYSICAL_LINK_ID}`));
    expect(marked[0]).toHaveAttribute("data-diagnosis", "PHYSICAL_LINK_FAILURE");
    expect(marked[0]).toHaveClass(
      "topology-link--diagnosed",
      "topology-link--rca-physical_link_failure",
    );
    // Accessible textual counterpart.
    expect(marked[0]!.querySelector("title")).toHaveTextContent(
      "leaf-1:eth1 ↔ spine-1:eth1 — physical DOWN — root-cause analysis: Physical link failure",
    );
    for (const link of LAB1_LINKS.filter((l) => l.id !== PHYSICAL_LINK_ID)) {
      expect(screen.getByTestId(`link-${link.id}`)).not.toHaveAttribute("data-diagnosis");
    }
    // No link was added.
    expect(document.querySelectorAll("[data-testid^='link-']")).toHaveLength(LAB1_LINKS.length);
  });

  test("BGP_SESSION_FAILURE uses BGP labeling, not physical-failure labeling", async () => {
    await renderDegraded([bgpDiagnosis()]);
    await within(rcaPanel()).findByText("BGP session failure");

    const line = screen.getByTestId(`link-${PHYSICAL_LINK_ID}`);
    expect(line).toHaveAttribute("data-diagnosis", "BGP_SESSION_FAILURE");
    expect(line).toHaveClass("topology-link--rca-bgp_session_failure");
    expect(line).not.toHaveClass("topology-link--rca-physical_link_failure");
    const title = line.querySelector("title")!.textContent;
    expect(title).toContain("root-cause analysis: BGP session failure");
    expect(title).not.toMatch(/physical link failure/i);
    expect(document.querySelector(".topology")).toHaveTextContent("RCA: BGP session");
    expect(document.querySelector(".topology")).not.toHaveTextContent("RCA: link failure");
  });

  test("an unmatched resource does not fabricate a link; the card is still shown", async () => {
    await renderDegraded([
      physicalDiagnosis({
        resource: {
          resource_id: "leaf-7:eth1--spine-7:eth1",
          endpoint_a: { node_id: "leaf-7", interface: "eth1" },
          endpoint_b: { node_id: "spine-7", interface: "eth1" },
        },
      }),
    ]);
    const panel = rcaPanel();
    expect(await within(panel).findByText("Physical link failure")).toBeInTheDocument();
    expect(panel).toHaveTextContent("leaf-7 eth1 ↔ spine-7 eth1");
    expect(document.querySelector("[data-diagnosis]")).toBeNull();
    expect(screen.queryAllByTestId("rca-marker")).toHaveLength(0);
    expect(document.querySelectorAll("[data-testid^='link-']")).toHaveLength(LAB1_LINKS.length);
  });

  test("a healthy diagnosis marks nothing", async () => {
    stubApi({
      network: () => jsonResponse(BASELINE_NETWORK_STATE),
      diagnosis: () => jsonResponse(healthyDiagnosis()),
    });
    render(<NetworkWorkspace />);
    await within(await screen.findByRole("region", { name: "Root-cause analysis" })).findByText(
      "No active root-cause diagnosis",
    );
    expect(document.querySelector("[data-diagnosis]")).toBeNull();
  });
});

describe("physical link table", () => {
  test("the diagnosed row carries the RCA marker and keeps its observed state", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");

    const table = screen.getByRole("table", { name: "Declared physical links" });
    const row = within(table).getByRole("row", { name: /leaf-1:eth1 ↔ spine-1:eth1/ });
    expect(within(row).getByTestId("rca-marker")).toHaveTextContent(
      "Probable root cause: Physical link failure · HIGH confidence",
    );
    // Observed state is separate and still truthful.
    expect(row).toHaveTextContent("DOWN");
    expect(row).toHaveTextContent("leaf-1 down; spine-1 down");
    expect(row).toHaveTextContent("leaf-1: Active; spine-1: Active");
  });

  test("no unrelated row gets a marker", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");
    const table = screen.getByRole("table", { name: "Declared physical links" });
    const withMarker = within(table)
      .getAllByRole("row")
      .filter((row) => within(row).queryByTestId("rca-marker") !== null);
    expect(withMarker).toHaveLength(1);
    expect(withMarker[0]).toHaveTextContent("leaf-1:eth1 ↔ spine-1:eth1");
  });

  test("a BGP diagnosis marks its row as BGP and does not say the link is down", async () => {
    await renderDegraded([bgpDiagnosis()]);
    await within(rcaPanel()).findByText("BGP session failure");
    const marker = screen.getByTestId("rca-marker");
    expect(marker).toHaveTextContent("BGP session failure · MEDIUM confidence");
    expect(marker).not.toHaveTextContent(/physical/i);
  });

  test("INSUFFICIENT_EVIDENCE is marked as RCA without a confidence claim", async () => {
    await renderDegraded([insufficientDiagnosis()]);
    await within(rcaPanel()).findByText("Insufficient evidence");
    expect(screen.getByTestId("rca-marker")).toHaveTextContent(
      "RCA: Insufficient evidence · Confidence unavailable",
    );
  });
});

describe("existing workspace behavior is unchanged", () => {
  test("incident cards (workspace scope) are not changed by a diagnosis", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");
    expect(await screen.findAllByRole("article")).toHaveLength(2);
  });

  test("the RCA panel never offers remediation", async () => {
    await renderDegraded([physicalDiagnosis()]);
    await within(rcaPanel()).findByText("Physical link failure");
    expect(rcaPanel().textContent).not.toMatch(/remediat|fix|restart/i);
  });
});

describe("J. long identifiers", () => {
  const LONG = "x".repeat(180);

  test("long resource, incident and affected_resource strings render in wrapping containers", async () => {
    await renderDegraded([
      physicalDiagnosis({
        resource: {
          resource_id: `leaf-1:eth1--spine-1:eth1-${LONG}`,
          endpoint_a: { node_id: "leaf-1", interface: "eth1" },
          endpoint_b: { node_id: "spine-1", interface: "eth1" },
        },
        linked_incidents: [
          linkedIncident("lab1-leaf-1", "10.255.0.0", {
            incident_id: `incident-${LONG}`,
            affected_resource: `bgp-neighbor:${LONG}`,
          }),
        ],
      }),
    ]);
    const panel = rcaPanel();
    await within(panel).findByText("Physical link failure");
    expect(panel).toHaveTextContent(`incident-${LONG}`);
    expect(panel).toHaveTextContent(`bgp-neighbor:${LONG}`);
    // jsdom has no layout engine: the actual no-overflow proof is the
    // Playwright 390px scenario; here the wrapping container is asserted.
    expect(panel.querySelector("li.diagnosis-card")).not.toBeNull();
  });
});

describe("DEGRADED fixtures stay aligned", () => {
  test("the fixture helper's collection id matches the degraded state", () => {
    expect(DEGRADED_COLLECTION_ID).toBe(DEGRADED_NETWORK_STATE.collection_id);
  });
});
