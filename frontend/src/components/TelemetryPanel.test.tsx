import { fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { TelemetryPanel } from "./TelemetryPanel";
import { ApiRequestError } from "../api/client";
import * as telemetryModule from "../api/telemetry";
import * as incidentsModule from "../api/incidents";
import type { IncidentResponse, TelemetrySampleResponse } from "../api/types";

vi.mock("../api/telemetry", async () => {
  const actual = await vi.importActual<typeof import("../api/telemetry")>("../api/telemetry");
  return { ...actual, fetchRecentTelemetry: vi.fn() };
});

vi.mock("../api/incidents", async () => {
  const actual = await vi.importActual<typeof import("../api/incidents")>("../api/incidents");
  return { ...actual, fetchIncidents: vi.fn() };
});

const fetchRecentTelemetryMock = vi.mocked(telemetryModule.fetchRecentTelemetry);
const fetchIncidentsMock = vi.mocked(incidentsModule.fetchIncidents);

const T0 = "2026-07-18T10:00:00Z";
const T1 = "2026-07-18T10:00:30Z";

function sample(overrides: Partial<TelemetrySampleResponse> = {}): TelemetrySampleResponse {
  return {
    device_id: "spine-01",
    sampled_at: T0,
    cpu_utilization_pct: 95.5,
    memory_utilization_pct: 61.2,
    interface_error_rate: 0.02,
    interface_states: [{ name: "GigabitEthernet0/1", oper_state: "up" }],
    bgp_sessions: [{ neighbor_ip: "10.0.0.1", state: "Established" }],
    ...overrides,
  };
}

function cpuIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    incident_id: "cpu-incident-1",
    fingerprint: "f".repeat(40),
    device_id: "spine-01",
    source: "ANOMALY",
    rule_ref: "RULE-CPU-HIGH",
    affected_resource: "device",
    severity: "High",
    status: "OPEN",
    evidence: {
      samples: [
        { timestamp: T0, cpu_utilization_pct: 95.0 },
        { timestamp: T1, cpu_utilization_pct: 96.0 },
      ],
    },
    recommendation: "Investigate sustained high CPU utilization on spine-01.",
    created_at: T1,
    last_seen_at: T1,
    occurrence_count: 1,
    updated_at: T1,
    resolved_at: null,
    ...overrides,
  };
}

function linkFlapIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    incident_id: "link-incident-1",
    fingerprint: "f".repeat(40),
    device_id: "spine-01",
    source: "ANOMALY",
    rule_ref: "RULE-LINK-FLAP",
    affected_resource: "interface:GigabitEthernet0/1",
    severity: "High",
    status: "OPEN",
    evidence: {
      interface_name: "GigabitEthernet0/1",
      transitions: [
        { timestamp: T0, oper_state: "down" },
        { timestamp: T1, oper_state: "up" },
      ],
    },
    recommendation: "Investigate unstable link state on spine-01 interface GigabitEthernet0/1.",
    created_at: T1,
    last_seen_at: T1,
    occurrence_count: 1,
    updated_at: T1,
    resolved_at: null,
    ...overrides,
  };
}

function bgpDownIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    incident_id: "bgp-incident-1",
    fingerprint: "f".repeat(40),
    device_id: "spine-01",
    source: "ANOMALY",
    rule_ref: "RULE-BGP-DOWN",
    affected_resource: "bgp-neighbor:10.0.0.2",
    severity: "Critical",
    status: "OPEN",
    evidence: { neighbor_ip: "10.0.0.2", previous_state: "Established", state: "Idle" },
    recommendation: "Investigate BGP session down on spine-01 neighbor 10.0.0.2.",
    created_at: T1,
    last_seen_at: T1,
    occurrence_count: 1,
    updated_at: T1,
    resolved_at: null,
    ...overrides,
  };
}

function loadDevice(deviceId = "spine-01"): void {
  fireEvent.change(screen.getByLabelText(/telemetry device/i), { target: { value: deviceId } });
  fireEvent.click(screen.getByRole("button", { name: /load telemetry/i }));
}

beforeEach(() => {
  fetchRecentTelemetryMock.mockReset();
  fetchIncidentsMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// 1-3. Initial UI
// ---------------------------------------------------------------------------

test("renders the initial heading and instructions", () => {
  render(<TelemetryPanel />);

  expect(screen.getByRole("heading", { name: /device telemetry/i })).toBeInTheDocument();
  expect(
    screen.getByText(/load telemetry and anomaly incidents already recorded/i),
  ).toBeInTheDocument();
});

test("the device ID input has an accessible label", () => {
  render(<TelemetryPanel />);

  expect(screen.getByLabelText(/telemetry device/i)).toBeInTheDocument();
});

test("no request occurs merely on initial render", () => {
  render(<TelemetryPanel />);

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
});

test("submitting an empty device ID issues no request", () => {
  render(<TelemetryPanel />);

  fireEvent.click(screen.getByRole("button", { name: /load telemetry/i }));

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
  expect(fetchIncidentsMock).not.toHaveBeenCalled();
});

test("submitting a whitespace-only device ID issues no request", () => {
  render(<TelemetryPanel />);

  fireEvent.change(screen.getByLabelText(/telemetry device/i), { target: { value: "   " } });
  fireEvent.click(screen.getByRole("button", { name: /load telemetry/i }));

  expect(fetchRecentTelemetryMock).not.toHaveBeenCalled();
});

// ---------------------------------------------------------------------------
// 4-5. Loading and selected-device header
// ---------------------------------------------------------------------------

test("shows an accessible loading status during the initial load", () => {
  fetchRecentTelemetryMock.mockReturnValue(new Promise(() => {}));
  fetchIncidentsMock.mockReturnValue(new Promise(() => {}));
  render(<TelemetryPanel />);

  loadDevice();

  const status = screen.getByText(/loading telemetry/i);
  expect(status.closest('[role="status"]')).not.toBeNull();
});

test("renders the selected device ID after loading", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
});

// ---------------------------------------------------------------------------
// 6-8. Refresh
// ---------------------------------------------------------------------------

test("renders a manual Refresh telemetry control after loading", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(await screen.findByRole("button", { name: /refresh telemetry/i })).toBeInTheDocument();
});

test("refresh keeps previously loaded telemetry visible while pending", async () => {
  const s = sample();
  fetchRecentTelemetryMock.mockResolvedValueOnce([s]).mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  render(<TelemetryPanel />);

  loadDevice("spine-01");
  await screen.findByText("spine-01");

  fireEvent.click(screen.getByRole("button", { name: /refresh telemetry/i }));

  expect(screen.getAllByText("95.5%").length).toBeGreaterThan(0);
});

test("communicates refresh status through visible text", async () => {
  fetchRecentTelemetryMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  render(<TelemetryPanel />);

  loadDevice("spine-01");
  await screen.findByText("spine-01");

  fireEvent.click(screen.getByRole("button", { name: /refresh telemetry/i }));

  expect(screen.getByText(/refreshing telemetry/i)).toBeInTheDocument();
});

test("the refresh control is disabled while a refresh is pending", async () => {
  fetchRecentTelemetryMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  fetchIncidentsMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(() => {}));
  render(<TelemetryPanel />);

  loadDevice("spine-01");
  await screen.findByText("spine-01");

  const refreshButton = screen.getByRole("button", { name: /refresh telemetry/i });
  fireEvent.click(refreshButton);

  expect(refreshButton).toBeDisabled();
});

// ---------------------------------------------------------------------------
// 9-12. Latest-sample summary
// ---------------------------------------------------------------------------

test("renders the latest sample's CPU utilization", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
  expect(screen.getAllByText("95.5%").length).toBeGreaterThan(0);
});

test("renders the latest sample's memory utilization", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
  expect(screen.getAllByText("61.2%").length).toBeGreaterThan(0);
});

test("renders the latest sample's interface error rate", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  const matches = await screen.findAllByText("0.02");
  expect(matches.length).toBeGreaterThan(0);
});

test("renders the latest sample's sampled-at timestamp via a time element", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
  expect(document.querySelectorAll(`time[datetime="${T0}"]`).length).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// 13-16. Interface / BGP state
// ---------------------------------------------------------------------------

test("renders interface names and their exact operating-state text", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("GigabitEthernet0/1");
  expect(screen.getByText("up")).toBeInTheDocument();
});

test("shows a clear empty message when the latest sample has no interface states", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample({ interface_states: [] })]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText(/no interface state has been recorded for this device/i),
  ).toBeInTheDocument();
});

test("renders BGP neighbor IPs and their exact state text", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("10.0.0.1");
  expect(screen.getByText("Established")).toBeInTheDocument();
});

test("shows a clear empty message when the latest sample has no BGP sessions", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample({ bgp_sessions: [] })]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText(/no bgp session state has been recorded for this device/i),
  ).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 17-18. History table
// ---------------------------------------------------------------------------

test("renders a semantic history table with a caption and column headings", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  const table = await screen.findByRole("table");
  expect(within(table).getByText(/recent telemetry samples/i)).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /sampled at/i })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /^cpu$/i })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /^memory$/i })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /interface error rate/i })).toBeInTheDocument();
});

test("preserves received sample order in the history table", async () => {
  const first = sample({ sampled_at: T0 });
  const second = sample({ sampled_at: T1 });
  fetchRecentTelemetryMock.mockResolvedValue([first, second]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  const table = await screen.findByRole("table");
  const rows = within(table).getAllByRole("row").slice(1);
  expect(within(rows[0]!).getByText(new Date(T0).toLocaleString())).toBeInTheDocument();
  expect(within(rows[1]!).getByText(new Date(T1).toLocaleString())).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 19-20. Empty states
// ---------------------------------------------------------------------------

test("shows the no-telemetry message when the recent-telemetry endpoint returns an empty array", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText("No telemetry has been recorded for this device."),
  ).toBeInTheDocument();
});

test("shows the no-anomaly-incidents message when filtering produces no matches", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText("No anomaly incidents exist for this device."),
  ).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 21-22. Partial-failure rendering
// ---------------------------------------------------------------------------

test("a telemetry error is shown as an alert while successful incidents remain visible", async () => {
  fetchRecentTelemetryMock.mockRejectedValue(
    new ApiRequestError("device not found: 'spine-01'", "device_not_found"),
  );
  fetchIncidentsMock.mockResolvedValue([cpuIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("device not found: 'spine-01'");
  expect(screen.getByText("RULE-CPU-HIGH")).toBeInTheDocument();
});

test("an incident error is shown as an alert while successful telemetry remains visible", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockRejectedValue(new ApiRequestError("DB down", "persistence_error"));
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("DB down");
  expect(screen.getAllByText("95.5%").length).toBeGreaterThan(0);
});

// ---------------------------------------------------------------------------
// 23-27. Anomaly evidence summaries
// ---------------------------------------------------------------------------

test("renders a CPU anomaly evidence summary with latest percentage and sample count", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([cpuIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText("Latest CPU utilization: 96% across 2 evidence sample(s)."),
  ).toBeInTheDocument();
});

test("renders a link-flap evidence summary with interface name and transition count", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([linkFlapIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(
    await screen.findByText("Interface GigabitEthernet0/1: 2 recorded transition(s)."),
  ).toBeInTheDocument();
});

test("renders a BGP-down evidence summary with neighbor and state transition", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([bgpDownIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(await screen.findByText("Neighbor 10.0.0.2: Established -> Idle.")).toBeInTheDocument();
});

test("renders both OPEN and RESOLVED anomaly incidents", async () => {
  const resolved = cpuIncident({
    incident_id: "cpu-incident-resolved",
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
  });
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([cpuIncident(), resolved]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findAllByText("RULE-CPU-HIGH");
  expect(screen.getByText("OPEN")).toBeInTheDocument();
  expect(screen.getByText("RESOLVED")).toBeInTheDocument();
});

test("an unknown anomaly rule_ref falls back to a generic evidence message without crashing", async () => {
  const unknownRule = cpuIncident({
    incident_id: "future-rule-incident",
    rule_ref: "RULE-FUTURE-THING",
    evidence: { neighbor_ip: "10.0.0.9", previous_state: "x", state: "y" },
  });
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([unknownRule]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  expect(await screen.findByText("Evidence details are unavailable.")).toBeInTheDocument();
  expect(screen.getByText("RULE-FUTURE-THING")).toBeInTheDocument();
});

// ---------------------------------------------------------------------------
// 28-30. Cross-cutting safety
// ---------------------------------------------------------------------------

test("renders every timestamp using a semantic time element", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([cpuIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
  expect(document.querySelectorAll(`time[datetime="${T0}"]`).length).toBeGreaterThan(0);
  expect(document.querySelectorAll(`time[datetime="${T1}"]`).length).toBeGreaterThan(0);
});

test("never renders the literal text undefined", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([sample()]);
  fetchIncidentsMock.mockResolvedValue([cpuIncident(), linkFlapIncident(), bgpDownIncident()]);
  const { container } = render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("spine-01");
  expect(container.textContent).not.toMatch(/undefined/);
});

test("never renders a Resolve incident control inside the telemetry panel", async () => {
  fetchRecentTelemetryMock.mockResolvedValue([]);
  fetchIncidentsMock.mockResolvedValue([cpuIncident()]);
  render(<TelemetryPanel />);

  loadDevice("spine-01");

  await screen.findByText("RULE-CPU-HIGH");
  expect(screen.queryByRole("button", { name: /resolve incident/i })).not.toBeInTheDocument();
});
