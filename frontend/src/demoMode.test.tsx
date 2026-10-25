import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";
import { App } from "./App";
import { isDemoReadOnly } from "./demoMode";
import { diagnosisResponse, physicalDiagnosis } from "./test/diagnosisFixtures";
import { DEGRADED_NETWORK_STATE, failureIncidents, jsonResponse } from "./test/networkFixtures";

function stubApi() {
  const fetchMock = vi.fn().mockImplementation((url: string) => {
    const path = new URL(url).pathname;
    if (path === "/network/state") {
      return Promise.resolve(jsonResponse(DEGRADED_NETWORK_STATE));
    }
    if (path === "/network/diagnosis") {
      return Promise.resolve(jsonResponse(diagnosisResponse([physicalDiagnosis()])));
    }
    if (path === "/incidents") {
      return Promise.resolve(jsonResponse(failureIncidents()));
    }
    throw new Error(`unexpected request ${path}`);
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function methods(fetchMock: ReturnType<typeof stubApi>): string[] {
  return fetchMock.mock.calls.map(([, init]) => (init as RequestInit)?.method ?? "GET");
}

beforeEach(() => {
  vi.stubEnv("VITE_API_BASE_URL", "http://localhost:8080");
  window.localStorage.clear();
  window.location.hash = "";
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
  window.location.hash = "";
});

describe("isDemoReadOnly (build-time flag)", () => {
  test.each([
    [undefined, false],
    ["", false],
    ["false", false],
    ["1", false],
    ["yes", false],
    ["true", true],
    ["TRUE", true],
    [" true ", true],
  ])("VITE_DEMO_READ_ONLY=%j -> %s", (value, expected) => {
    if (value !== undefined) {
      vi.stubEnv("VITE_DEMO_READ_ONLY", value);
    }
    expect(isDemoReadOnly()).toBe(expected);
  });
});

describe("default mode keeps every existing control", () => {
  test("no read-only badge, configuration form and Resolve incident are present", async () => {
    const user = userEvent.setup();
    stubApi();
    render(<App />);
    expect(screen.queryByText("Read-only demo")).toBeNull();

    await user.click(screen.getByRole("link", { name: "Incidents & Tools" }));
    expect(
      await screen.findByRole("heading", { name: "Submit device configuration", level: 2 }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText(/raw configuration/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit configuration" })).toBeInTheDocument();
    expect((await screen.findAllByRole("button", { name: "Resolve incident" })).length).toBe(2);
  });
});

describe("read-only demo mode", () => {
  beforeEach(() => {
    vi.stubEnv("VITE_DEMO_READ_ONLY", "true");
  });

  test("the header visibly says Read-only demo on both views", async () => {
    const user = userEvent.setup();
    stubApi();
    render(<App />);
    expect(screen.getByText("Read-only demo")).toBeInTheDocument();

    await user.click(screen.getByRole("link", { name: "Incidents & Tools" }));
    await screen.findByRole("heading", { name: "Network Incidents", level: 1 });
    expect(screen.getByText("Read-only demo")).toBeInTheDocument();
  });

  test("Live Network and the diagnosis are unaffected", async () => {
    stubApi();
    render(<App />);
    const kpis = await screen.findByRole("region", { name: "Network summary" });
    expect(within(kpis).getByText("Degraded")).toBeInTheDocument();
    const rca = await screen.findByRole("region", { name: "Probable Root Cause" });
    expect(await within(rca).findByText("Physical link failure")).toBeInTheDocument();
    expect(rca).toHaveTextContent("HIGH confidence");
    expect(screen.getByRole("group", { name: "Lab 1 physical topology" })).toBeInTheDocument();
    expect((await screen.findAllByTestId("open-incident-row")).length).toBe(2);
  });

  test("Incidents & Tools still renders, but offers no configuration form or Resolve button", async () => {
    const user = userEvent.setup();
    const fetchMock = stubApi();
    render(<App />);
    await user.click(screen.getByRole("link", { name: "Incidents & Tools" }));

    expect(
      await screen.findByRole("heading", { name: "Submit device configuration", level: 2 }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/configuration submission and incident resolution are turned off/),
    ).toBeInTheDocument();
    expect(screen.queryByLabelText(/device id/i)).toBeNull();
    expect(screen.queryByLabelText(/raw configuration/i)).toBeNull();
    expect(screen.queryByRole("button", { name: "Submit configuration" })).toBeNull();

    // The incidents themselves stay inspectable.
    expect((await screen.findAllByRole("article")).length).toBe(2);
    expect(screen.queryByRole("button", { name: "Resolve incident" })).toBeNull();
    // Read-only telemetry inspection is still offered.
    expect(screen.getByRole("heading", { name: "Device telemetry", level: 2 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Load telemetry" })).toBeInTheDocument();
    // The page never issued a mutating request.
    expect(new Set(methods(fetchMock))).toEqual(new Set(["GET"]));
  });
});
