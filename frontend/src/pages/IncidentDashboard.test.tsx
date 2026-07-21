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
  updated_at: "2026-07-18T10:00:00Z",
  resolved_at: null,
};

const incidentB: IncidentResponse = {
  ...incidentA,
  incident_id: "second-incident-id",
  fingerprint: "second-fingerprint",
  device_id: "leaf-02",
  severity: "High",
  occurrence_count: 4,
};

const incidentAcknowledged: IncidentResponse = {
  ...incidentA,
  incident_id: "acknowledged-incident-id",
  fingerprint: "acknowledged-fingerprint",
  status: "ACKNOWLEDGED",
};

function resolvedIncident(overrides: Partial<IncidentResponse> = {}): IncidentResponse {
  return {
    ...incidentA,
    status: "RESOLVED",
    updated_at: "2026-07-18T11:00:00Z",
    resolved_at: "2026-07-18T11:00:00Z",
    ...overrides,
  };
}

function createDeferred<T>(): {
  promise: Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
} {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

/**
 * Routes a stubbed `fetch` between the one initial/refresh `GET /incidents`
 * queue and per-incident `POST .../resolve` handlers, so tests can control
 * each independently without caring about call ordering across the two.
 */
function createFetchRouter(): {
  fetchMock: ReturnType<typeof vi.fn>;
  queueGet: (handler: () => Promise<Response>) => void;
  setResolveHandler: (incidentId: string, handler: () => Promise<Response>) => void;
  calls: Array<{ url: string; init: RequestInit }>;
} {
  const getQueue: Array<() => Promise<Response>> = [];
  const resolveHandlers = new Map<string, () => Promise<Response>>();
  const calls: Array<{ url: string; init: RequestInit }> = [];

  const fetchMock = vi.fn().mockImplementation((url: string, init: RequestInit) => {
    calls.push({ url, init });
    if (init.method === "POST") {
      const handler = resolveHandlers.get(url);
      if (!handler) {
        throw new Error(`Unexpected POST to ${url}`);
      }
      return handler();
    }
    const handler = getQueue.shift();
    if (!handler) {
      throw new Error(`Unexpected GET to ${url}`);
    }
    return handler();
  });

  return {
    fetchMock,
    queueGet: (handler) => getQueue.push(handler),
    setResolveHandler: (incidentId, handler) =>
      resolveHandlers.set(`http://localhost:8080/incidents/${incidentId}/resolve`, handler),
    calls,
  };
}

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

test("a successful refresh merges in new data while retaining a current-only incident (Gate 7B-C reconciliation)", async () => {
  const user = userEvent.setup();
  const fetchMock = vi
    .fn()
    .mockResolvedValueOnce(jsonResponse([incidentA]))
    .mockResolvedValueOnce(jsonResponse([incidentB]));
  vi.stubGlobal("fetch", fetchMock);

  render(<IncidentDashboard />);

  await screen.findByText("spine-01");
  await user.click(screen.getByRole("button", { name: /refresh/i }));

  // GET /incidents is unfiltered and append-only, so a refresh response that
  // omits a previously-seen incident is never treated as its deletion:
  // `incidentB` (the incoming response) is added, and `incidentA` (now
  // current-only) is retained rather than dropped.
  await screen.findByText("leaf-02");
  expect(screen.getByText("spine-01")).toBeInTheDocument();
  expect(screen.getAllByRole("article")).toHaveLength(2);
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

// =============================================================================
// Incident resolution (Day 7B, Gate 7B-D)
// =============================================================================

// --- eligibility ---------------------------------------------------------

test("an OPEN incident shows a Resolve incident button", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(within(article).getByRole("button", { name: /resolve incident/i })).toBeInTheDocument();
});

test("a RESOLVED incident shows no Resolve incident button", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([resolvedIncident()])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(
    within(article).queryByRole("button", { name: /resolve incident/i }),
  ).not.toBeInTheDocument();
});

test("an ACKNOWLEDGED incident shows no Resolve incident button", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentAcknowledged])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(
    within(article).queryByRole("button", { name: /resolve incident/i }),
  ).not.toBeInTheDocument();
});

test("an unknown non-empty status shows no Resolve incident button", async () => {
  const suppressed: IncidentResponse = { ...incidentA, status: "SUPPRESSED" };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([suppressed])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(
    within(article).queryByRole("button", { name: /resolve incident/i }),
  ).not.toBeInTheDocument();
});

// --- timestamp rendering --------------------------------------------------

test("an OPEN incident displays Updated using the source updated_at value", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(within(article).getByText("Updated")).toBeInTheDocument();
  expect(article.querySelector(`time[datetime="${incidentA.updated_at}"]`)).not.toBeNull();
});

test("an OPEN incident does not display a Resolved timestamp", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([incidentA])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(within(article).queryByText("Resolved")).not.toBeInTheDocument();
});

test("a RESOLVED incident displays both Updated and Resolved, using source values", async () => {
  const resolved = resolvedIncident();
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse([resolved])));

  render(<IncidentDashboard />);

  const article = await screen.findByRole("article");
  expect(within(article).getByText("Updated")).toBeInTheDocument();
  expect(within(article).getByText("Resolved")).toBeInTheDocument();
  expect(article.querySelector(`time[datetime="${resolved.updated_at}"]`)).not.toBeNull();
  expect(article.querySelector(`time[datetime="${resolved.resolved_at}"]`)).not.toBeNull();
});

// --- request and pending state --------------------------------------------

test("clicking Resolve sends one POST to the expected resolution path with no body", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  const postCalls = router.calls.filter((call) => call.init.method === "POST");
  expect(postCalls).toHaveLength(1);
  expect(postCalls[0]?.url).toBe(
    `http://localhost:8080/incidents/${incidentA.incident_id}/resolve`,
  );
  expect("body" in postCalls[0]!.init).toBe(false);
});

test("the same card's button becomes disabled and shows Resolving… while pending", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  expect(within(article).getByRole("button", { name: /resolving/i })).toBeDisabled();
});

test("the incident card and existing data remain visible while a resolution is pending", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  expect(within(article).getByText("spine-01")).toBeInTheDocument();
  expect(within(article).getByText("OPEN")).toBeInTheDocument();
});

test("a rapid second interaction sends no second POST", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  // The button is now natively disabled — userEvent will not dispatch a
  // click to it, mirroring real browser/user behavior.
  await user.click(within(article).getByRole("button", { name: /resolving/i }));

  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(1);
});

test("another OPEN incident remains independently enabled while one is pending", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const [articleA, articleB] = await screen.findAllByRole("article");
  await user.click(within(articleA!).getByRole("button", { name: /resolve incident/i }));

  expect(within(articleB!).getByRole("button", { name: /resolve incident/i })).toBeEnabled();
});

test("resolving two different incidents may place both cards in pending state", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferredA = createDeferred<Response>();
  const deferredB = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferredA.promise);
  router.setResolveHandler(incidentB.incident_id, () => deferredB.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const [articleA, articleB] = await screen.findAllByRole("article");
  await user.click(within(articleA!).getByRole("button", { name: /resolve incident/i }));
  await user.click(within(articleB!).getByRole("button", { name: /resolve incident/i }));

  expect(within(articleA!).getByRole("button", { name: /resolving/i })).toBeDisabled();
  expect(within(articleB!).getByRole("button", { name: /resolving/i })).toBeDisabled();
});

// --- success ---------------------------------------------------------------

test("a successful resolution renders the returned RESOLVED incident, removes the Resolve button, and shows updated_at/resolved_at", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  const resolved = resolvedIncident();
  deferred.resolve(jsonResponse(resolved));
  await waitFor(() => {
    expect(within(article).getByText("RESOLVED")).toBeInTheDocument();
  });

  expect(
    within(article).queryByRole("button", { name: /resolve incident/i }),
  ).not.toBeInTheDocument();
  expect(article.querySelector(`time[datetime="${resolved.updated_at}"]`)).not.toBeNull();
  expect(article.querySelector(`time[datetime="${resolved.resolved_at}"]`)).not.toBeNull();
});

test("an unrelated incident remains present and unchanged, and card order remains stable, after a successful resolution", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const articlesBefore = await screen.findAllByRole("article");
  await user.click(within(articlesBefore[0]!).getByRole("button", { name: /resolve incident/i }));

  deferred.resolve(jsonResponse(resolvedIncident()));
  await waitFor(() => {
    expect(within(articlesBefore[0]!).getByText("RESOLVED")).toBeInTheDocument();
  });

  const articlesAfter = screen.getAllByRole("article");
  expect(articlesAfter).toHaveLength(2);
  expect(within(articlesAfter[0]!).getByText("spine-01")).toBeInTheDocument();
  expect(within(articlesAfter[1]!).getByText("leaf-02")).toBeInTheDocument();
});

test("success performs zero additional GET /incidents requests", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  deferred.resolve(jsonResponse(resolvedIncident()));
  await waitFor(() => {
    expect(within(article).getByText("RESOLVED")).toBeInTheDocument();
  });

  expect(router.calls.filter((call) => call.init.method === "GET")).toHaveLength(1);
});

test("dashboard-level refresh and configuration controls remain visible after a successful resolution", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  deferred.resolve(jsonResponse(resolvedIncident()));
  await waitFor(() => {
    expect(within(article).getByText("RESOLVED")).toBeInTheDocument();
  });

  expect(screen.getByRole("button", { name: /^refresh$/i })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /submit configuration/i })).toBeInTheDocument();
});

// --- failure -----------------------------------------------------------

test("a controlled error appears with role=alert inside only the affected card on failure", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA, incidentB])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const [articleA, articleB] = await screen.findAllByRole("article");
  await user.click(within(articleA!).getByRole("button", { name: /resolve incident/i }));

  deferred.reject(new ApiRequestError("Incident 'x' was not found.", "incident_not_found"));
  await waitFor(() => {
    expect(within(articleA!).getByRole("alert")).toHaveTextContent("Incident 'x' was not found.");
  });

  expect(within(articleB!).queryByRole("alert")).not.toBeInTheDocument();
});

test("the incident remains OPEN and the Resolve button re-enables after a failure, allowing a retry", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const firstAttempt = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => firstAttempt.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  firstAttempt.reject(new ApiRequestError("Incident 'x' was not found.", "incident_not_found"));
  await waitFor(() => {
    expect(within(article).getByRole("alert")).toBeInTheDocument();
  });

  expect(within(article).getByText("OPEN")).toBeInTheDocument();
  expect(within(article).getByRole("button", { name: /resolve incident/i })).toBeEnabled();
});

test("failure performs zero additional GET /incidents requests and leaves dashboard-level data visible", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const deferred = createDeferred<Response>();
  router.setResolveHandler(incidentA.incident_id, () => deferred.promise);
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  deferred.reject(new ApiRequestError("Incident 'x' was not found.", "incident_not_found"));
  await waitFor(() => {
    expect(within(article).getByRole("alert")).toBeInTheDocument();
  });

  expect(router.calls.filter((call) => call.init.method === "GET")).toHaveLength(1);
  expect(screen.getByText("spine-01")).toBeInTheDocument();
});

// --- retry -----------------------------------------------------------------

test("a retry after a failed resolution starts a second POST, clears the previous error, and a later success renders RESOLVED", async () => {
  const user = userEvent.setup();
  const router = createFetchRouter();
  router.queueGet(() => Promise.resolve(jsonResponse([incidentA])));
  const firstAttempt = createDeferred<Response>();
  const secondAttempt = createDeferred<Response>();
  let attempt = 0;
  router.setResolveHandler(incidentA.incident_id, () => {
    attempt += 1;
    return attempt === 1 ? firstAttempt.promise : secondAttempt.promise;
  });
  vi.stubGlobal("fetch", router.fetchMock);

  render(<IncidentDashboard />);
  const article = await screen.findByRole("article");
  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));

  firstAttempt.reject(new ApiRequestError("Incident 'x' was not found.", "incident_not_found"));
  await waitFor(() => {
    expect(within(article).getByRole("alert")).toBeInTheDocument();
  });

  await user.click(within(article).getByRole("button", { name: /resolve incident/i }));
  expect(within(article).queryByRole("alert")).not.toBeInTheDocument();
  expect(within(article).getByRole("button", { name: /resolving/i })).toBeDisabled();

  const resolved = resolvedIncident();
  secondAttempt.resolve(jsonResponse(resolved));
  await waitFor(() => {
    expect(within(article).getByText("RESOLVED")).toBeInTheDocument();
  });

  expect(within(article).queryByRole("alert")).not.toBeInTheDocument();
  expect(router.calls.filter((call) => call.init.method === "POST")).toHaveLength(2);
});
