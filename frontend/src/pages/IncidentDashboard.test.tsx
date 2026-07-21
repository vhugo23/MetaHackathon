import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { test, expect, vi, beforeEach } from "vitest";
import { IncidentDashboard } from "./IncidentDashboard";
import { ApiRequestError } from "../api/client";
import * as configurationsModule from "../api/configurations";
import type { ConfigurationSubmissionResponse, IncidentResponse } from "../api/types";

vi.mock("../api/configurations", () => ({
  submitDeviceConfiguration: vi.fn(),
}));

const submitDeviceConfigurationMock = vi.mocked(configurationsModule.submitDeviceConfiguration);

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function fillConfigurationForm(deviceId = "spine-01", rawConfigText = "hostname spine-01\n"): void {
  fireEvent.change(screen.getByLabelText(/device id/i), { target: { value: deviceId } });
  fireEvent.change(screen.getByLabelText(/raw configuration/i), {
    target: { value: rawConfigText },
  });
}

const validSubmissionResponse: ConfigurationSubmissionResponse = {
  device_id: "spine-01",
  snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  normalized_config: {
    hostname: "spine-01",
    interfaces: [],
    routing: { bgp_neighbors: [] },
    acls: [],
  },
  violations_detected: 1,
  incidents_created: 1,
  incidents_updated: 0,
};

const incidentA: IncidentResponse = {
  incident_id: "8f14e45f-ceea-4c1d-8f1e-1234567890ab",
  fingerprint: "a94a8fe5ccb19ba61c4c0873d391e987982fbbd3",
  device_id: "spine-01",
  source: "POLICY_VIOLATION",
  rule_ref: "policy-acl-external-in",
  affected_resource: "acl:ACL-EXTERNAL-IN:GigabitEthernet0/1:in",
  severity: "Medium",
  status: "OPEN",
  evidence: {
    source_snapshot_id: "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    violation_type: "MISSING_REQUIRED_ACL",
    expected_acl_name: "ACL-EXTERNAL-IN",
    actual_acl_name: null,
    interface_name: "GigabitEthernet0/1",
    direction: "in",
  },
  recommendation: "Assign ACL-EXTERNAL-IN inbound to GigabitEthernet0/1",
  created_at: "2026-07-18T10:00:00Z",
  last_seen_at: "2026-07-18T10:00:00Z",
  occurrence_count: 1,
};

const incidentB: IncidentResponse = {
  ...incidentA,
  incident_id: "second-incident-id",
  fingerprint: "second-fingerprint",
  device_id: "leaf-02",
  severity: "High",
  occurrence_count: 4,
};

beforeEach(() => {
  vi.unstubAllGlobals();
  submitDeviceConfigurationMock.mockReset();
});

test("renders an empty incident state when the API returns no incidents", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([])));

  render(<IncidentDashboard />);

  expect(await screen.findByText(/no incidents detected/i)).toBeInTheDocument();
});

test("shows a loading status while the initial request is pending", () => {
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

  render(<IncidentDashboard />);

  expect(screen.getByRole("status")).toHaveTextContent(/loading incidents/i);
});

test("renders all returned incidents in backend order", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA, incidentB])));

  render(<IncidentDashboard />);

  const cards = await screen.findAllByRole("article");
  expect(cards).toHaveLength(2);
  expect(within(cards[0]!).getByText("spine-01")).toBeInTheDocument();
  expect(within(cards[1]!).getByText("leaf-02")).toBeInTheDocument();
  expect(screen.getByText("2 incidents")).toBeInTheDocument();
});

test("exposes fingerprint and evidence in an accessible details region", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  const details = screen.getByText("Evidence").closest("details");
  expect(details).not.toBeNull();
  expect(within(details as HTMLElement).getByText(incidentA.fingerprint)).toBeInTheDocument();
  expect(
    within(details as HTMLElement).getByText(incidentA.evidence.expected_acl_name),
  ).toBeInTheDocument();
});

test("renders occurrence_count even when it equals 1", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  expect(screen.getByText("1")).toBeInTheDocument();
});

test("renders severity and status as visible text", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  expect(screen.getByText("Medium")).toBeInTheDocument();
  expect(screen.getByText("OPEN")).toBeInTheDocument();
});

test("represents ISO timestamps semantically via a time element", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  const timeElement = document.querySelector(`time[datetime="${incidentA.last_seen_at}"]`);
  expect(timeElement).not.toBeNull();
});

test("renders a controlled error state when the API call fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500)),
  );

  render(<IncidentDashboard />);

  expect(await screen.findByText("DB down")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
});

test("keeps the page heading present during loading and error states", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500)),
  );

  render(<IncidentDashboard />);

  expect(screen.getByRole("heading", { name: /network incidents/i })).toBeInTheDocument();
  await screen.findByText("DB down");
  expect(screen.getByRole("heading", { name: /network incidents/i })).toBeInTheDocument();
});

test("retry transitions from error to success and performs a new request", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500))
    .mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByText("DB down");
  await user.click(screen.getByRole("button", { name: /retry/i }));

  await screen.findByRole("article");
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("renders a controlled error state when the API returns a malformed incident array", async () => {
  const malformedIncident = { ...incidentA, fingerprint: undefined };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([malformedIncident])));

  render(<IncidentDashboard />);

  await screen.findByText(/unable to load incidents/i);
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
});

// Deliberately overlapping requests (a stale response arriving after a
// newer one) are exercised at the hook level in useIncidents.test.ts,
// using deferred promises and direct calls to `refresh()` — not through
// this component's UI. With the Refresh control natively `disabled` while
// a refresh is pending (see below), a second overlapping request can no
// longer be produced by clicking through the rendered dashboard at all,
// so there is nothing left for a click-based test here to prove.

test("refresh performs a new network request", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentA, incidentB]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  await screen.findAllByRole("article").then((cards) => expect(cards).toHaveLength(2));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("one Refresh click produces exactly one new request", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: /refresh/i }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

test("one Retry click produces exactly one new request", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500))
    .mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByText("DB down");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  await user.click(screen.getByRole("button", { name: /retry/i }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});

test("refresh preserves the existing incident cards while the new request is pending", async () => {
  const user = userEvent.setup();
  const pendingSecond = new Promise<Response>(() => {});
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => pendingSecond);
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  // The refresh request never resolves in this test — the previously
  // rendered card must remain visible throughout, not be replaced by a
  // full loading state.
  expect(screen.getByText("spine-01")).toBeInTheDocument();
  expect(screen.getByRole("article")).toBeInTheDocument();
});

test("refresh exposes an accessible busy status", async () => {
  const user = userEvent.setup();
  const pendingSecond = new Promise<Response>(() => {});
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => pendingSecond);
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  expect(await screen.findByRole("status", { name: "" })).toHaveTextContent(
    /refreshing incidents/i,
  );
});

test("Refresh is enabled after a successful load", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  expect(screen.getByRole("button", { name: /refresh/i })).toBeEnabled();
});

test("Refresh becomes natively disabled while a refresh is pending", async () => {
  const user = userEvent.setup();
  const pendingSecond = new Promise<Response>(() => {});
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => pendingSecond);
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  const refreshButton = screen.getByRole("button", { name: /refresh/i });
  expect(refreshButton).toBeEnabled();

  await user.click(refreshButton);

  await waitFor(() => {
    expect(refreshButton).toBeDisabled();
  });
});

test("another attempted click while Refresh is disabled cannot start another request", async () => {
  const user = userEvent.setup();
  const pendingSecond = new Promise<Response>(() => {});
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => pendingSecond);
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  const refreshButton = screen.getByRole("button", { name: /refresh/i });
  await user.click(refreshButton);

  await waitFor(() => {
    expect(refreshButton).toBeDisabled();
  });
  expect(fetchMock).toHaveBeenCalledTimes(2);

  // userEvent respects the native `disabled` attribute and will not
  // dispatch a click to a disabled button, mirroring real browser/user
  // behavior — this is the same guarantee a real user gets, not just a
  // testing-library quirk.
  await user.click(refreshButton);

  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("Refresh becomes enabled again after a successful refresh completes", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentB]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  const refreshButton = screen.getByRole("button", { name: /refresh/i });
  await user.click(refreshButton);

  await screen.findByText("leaf-02");
  expect(refreshButton).toBeEnabled();
});

test("a successful refresh replaces the old data with the new data", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentB]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByText("spine-01");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  await screen.findByText("leaf-02");
  expect(screen.queryByText("spine-01")).not.toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(1);
});

test("a failed refresh produces the controlled error state", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  expect(await screen.findByText("DB down")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  expect(screen.queryByRole("article")).not.toBeInTheDocument();
});

test("the heading remains present while a refresh is pending and after a refresh failure", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  expect(screen.getByRole("heading", { name: /network incidents/i })).toBeInTheDocument();
  await screen.findByText("DB down");
  expect(screen.getByRole("heading", { name: /network incidents/i })).toBeInTheDocument();
});

test("a failed refresh leaves the resulting Retry control enabled", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByRole("article");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  await screen.findByText("DB down");
  expect(screen.getByRole("button", { name: /retry/i })).toBeEnabled();
});

test("unmounting during the initial load aborts the active request", () => {
  let capturedSignal: AbortSignal | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((_url: string, init: RequestInit) => {
      capturedSignal = init.signal as AbortSignal;
      return new Promise(() => {});
    }),
  );

  const { unmount } = render(<IncidentDashboard />);

  expect(capturedSignal?.aborted).toBe(false);
  unmount();
  expect(capturedSignal?.aborted).toBe(true);
});

// ---------------------------------------------------------------------------
// Gate D — ConfigurationSubmissionForm integration
//
// The form's own request shape, local validation, native disabling, and
// success-field rendering are already proven in
// ConfigurationSubmissionForm.test.tsx — these tests focus only on
// cross-component integration: POST outcome, callback invocation, GET
// refresh count, and the independence of submission and incident state.
// ---------------------------------------------------------------------------

test("the configuration submission form renders above the incident section", () => {
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

  render(<IncidentDashboard />);

  const formHeading = screen.getByRole("heading", { name: /submit device configuration/i });
  const loadingStatus = screen.getByRole("status");
  expect(
    formHeading.compareDocumentPosition(loadingStatus) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
});

test("the configuration submission form remains present while the initial incident request is pending", () => {
  vi.stubGlobal("fetch", vi.fn().mockReturnValue(new Promise(() => {})));

  render(<IncidentDashboard />);

  expect(screen.getByRole("button", { name: /submit configuration/i })).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent(/loading incidents/i);
});

test("the configuration submission form remains present when the initial incident request fails", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockResolvedValue(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500)),
  );

  render(<IncidentDashboard />);

  await screen.findByText("DB down");
  expect(screen.getByRole("button", { name: /submit configuration/i })).toBeInTheDocument();
});

test("a successful submission triggers exactly one additional GET, leaves the submission success visible, and renders the refreshed incident data", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentA, incidentB]));
  vi.stubGlobal("fetch", fetchMock);
  submitDeviceConfigurationMock.mockResolvedValue(validSubmissionResponse);

  render(<IncidentDashboard />);
  await screen.findByRole("article");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fillConfigurationForm();
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
  await screen.findByText("leaf-02");
  expect(screen.getByText(/configuration submitted successfully/i)).toBeInTheDocument();
});

test("one successful submission never triggers two refresh GETs", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);
  submitDeviceConfigurationMock.mockResolvedValue(validSubmissionResponse);

  render(<IncidentDashboard />);
  await screen.findByRole("article");

  fillConfigurationForm();
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  // Give any errant extra effect/timer a chance to fire before asserting the
  // count stays put.
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(fetchMock).toHaveBeenCalledTimes(2);
});

test("a failed POST displays the controlled submission error, triggers zero additional GET calls, and preserves existing incident data", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);
  submitDeviceConfigurationMock.mockRejectedValue(
    new ApiRequestError("vendor not recognized", "unsupported_vendor"),
  );

  render(<IncidentDashboard />);
  await screen.findByRole("article");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fillConfigurationForm();
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("vendor not recognized");
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText("spine-01")).toBeInTheDocument();
});

test("a local validation failure triggers zero POSTs, zero additional GET calls, and preserves existing incident data", async () => {
  const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse([incidentA]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);
  await screen.findByRole("article");
  expect(fetchMock).toHaveBeenCalledTimes(1);

  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  expect(await screen.findByText("Enter a device ID.")).toBeInTheDocument();
  expect(submitDeviceConfigurationMock).not.toHaveBeenCalled();
  expect(fetchMock).toHaveBeenCalledTimes(1);
  expect(screen.getByText("spine-01")).toBeInTheDocument();
});

test("a successful POST followed by a failed refresh GET keeps the submission success visible and shows the incident section's own controlled error, without touching the POST result", async () => {
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse({ code: "persistence_error", detail: "DB down" }, 500));
  vi.stubGlobal("fetch", fetchMock);
  submitDeviceConfigurationMock.mockResolvedValue(validSubmissionResponse);

  render(<IncidentDashboard />);
  await screen.findByRole("article");

  fillConfigurationForm();
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  await screen.findByText("DB down");

  // Submission success remains its own outcome, untouched by the refresh
  // failure — never reported as a failed submission.
  expect(screen.getByText(/configuration submitted successfully/i)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /retry/i })).toBeInTheDocument();
  expect(fetchMock).toHaveBeenCalledTimes(2);
  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);
});

test("a successful submission while a manual refresh is pending supersedes it, rendering only the latest GET result", async () => {
  const user = userEvent.setup();
  const pendingManualRefresh = new Promise<Response>(() => {});
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockImplementationOnce(() => pendingManualRefresh)
    .mockResolvedValueOnce(jsonResponse([incidentA, incidentB]));
  vi.stubGlobal("fetch", fetchMock);
  submitDeviceConfigurationMock.mockResolvedValue(validSubmissionResponse);

  render(<IncidentDashboard />);
  await screen.findByRole("article");

  // Kick off a manual refresh that never resolves on its own.
  await user.click(screen.getByRole("button", { name: /refresh/i }));
  expect(fetchMock).toHaveBeenCalledTimes(2);

  fillConfigurationForm();
  fireEvent.click(screen.getByRole("button", { name: /submit configuration/i }));

  await screen.findByText(/configuration submitted successfully/i);
  expect(submitDeviceConfigurationMock).toHaveBeenCalledTimes(1);

  await screen.findByText("leaf-02");
  expect(fetchMock).toHaveBeenCalledTimes(3);
});
