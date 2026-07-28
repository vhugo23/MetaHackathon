import { expect, test, type Locator, type Page } from "@playwright/test";
import { fieldValue, waitForIncidentRefresh } from "./helpers";

// Fixed base timestamp for the deterministic all-anomalies telemetry
// sequence (matches scripts/telemetry_simulator.py's own fixed base) — never
// the real clock, so the seeded scenario is fully reproducible.
const BASE_TIMESTAMP = new Date("2026-07-18T10:00:00Z");
const INTERFACE_NAME = "GigabitEthernet0/1";
const BGP_NEIGHBOR = "10.0.0.2";

function isoAt(offsetSeconds: number): string {
  return new Date(BASE_TIMESTAMP.getTime() + offsetSeconds * 1000)
    .toISOString()
    .replace(/\.\d{3}Z$/, "Z");
}

/**
 * Telemetry-specific, URL-safe, Cisco-hostname-valid device ID: a fixed
 * letter-led prefix, the Playwright worker index, and a timestamp+random
 * suffix — unique per test run, never a fixed/shared ID across runs.
 */
function generateDeviceId(label: string, workerIndex: number): string {
  const unique = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
  return `e2e-tel-${label}-${workerIndex}-${unique}`;
}

/**
 * Navigates to the dashboard and derives the live API origin from the real
 * initial `GET /incidents` response — never a hardcoded or env-guessed
 * value, and never a new environment variable. `browser_e2e.py` reserves
 * the API port dynamically per run, so the only reliable source of truth
 * for "what origin is the running frontend actually calling" is an
 * observed real request.
 */
async function openDashboardAndResolveApiOrigin(page: Page): Promise<string> {
  const initialIncidentsResponsePromise = waitForIncidentRefresh(page);
  await page.goto("/");
  const initialResponse = await initialIncidentsResponsePromise;
  expect(initialResponse.status()).toBe(200);
  return new URL(initialResponse.url()).origin;
}

async function seedConfiguration(page: Page, apiOrigin: string, deviceId: string): Promise<void> {
  const response = await page.request.post(`${apiOrigin}/devices/${deviceId}/config`, {
    data: {
      vendor: "cisco-ios-xe",
      raw_config_text: `hostname ${deviceId}\n!\ninterface ${INTERFACE_NAME}\n!\n`,
    },
  });
  expect(response.status()).toBe(201);
}

interface TelemetrySeedPayload {
  sampled_at: string;
  cpu_utilization_pct: number;
  memory_utilization_pct: number;
  interface_error_rate: number;
  interface_states: { name: string; oper_state: string }[];
  bgp_sessions: { neighbor_ip: string; state: string }[];
}

function telemetryPayload(
  overrides: Partial<TelemetrySeedPayload> & { sampled_at: string },
): TelemetrySeedPayload {
  return {
    cpu_utilization_pct: 50.0,
    memory_utilization_pct: 50.0,
    interface_error_rate: 0.0,
    interface_states: [],
    bgp_sessions: [],
    ...overrides,
  };
}

/** The exact deterministic six-sample all-anomalies sequence (Day 11B3). */
function buildAllAnomaliesSequence(): TelemetrySeedPayload[] {
  return [
    telemetryPayload({
      sampled_at: isoAt(0),
      interface_states: [{ name: INTERFACE_NAME, oper_state: "up" }],
    }),
    telemetryPayload({
      sampled_at: isoAt(10),
      interface_states: [{ name: INTERFACE_NAME, oper_state: "down" }],
    }),
    telemetryPayload({
      sampled_at: isoAt(20),
      interface_states: [{ name: INTERFACE_NAME, oper_state: "up" }],
    }),
    telemetryPayload({
      sampled_at: isoAt(30),
      interface_states: [{ name: INTERFACE_NAME, oper_state: "down" }],
    }),
    telemetryPayload({
      sampled_at: isoAt(40),
      cpu_utilization_pct: 95.0,
      bgp_sessions: [{ neighbor_ip: BGP_NEIGHBOR, state: "Established" }],
    }),
    telemetryPayload({
      sampled_at: isoAt(50),
      cpu_utilization_pct: 95.0,
      interface_states: [{ name: INTERFACE_NAME, oper_state: "up" }],
      bgp_sessions: [{ neighbor_ip: BGP_NEIGHBOR, state: "Idle" }],
    }),
  ];
}

interface TelemetryIngestionResponse {
  anomalies: { rule_id: string }[];
}

/** Submits the six-sample sequence via the real, public HTTP endpoint and
 * returns the final response's anomaly rule-ID order — never computed
 * locally. */
async function seedAllAnomaliesTelemetry(
  page: Page,
  apiOrigin: string,
  deviceId: string,
): Promise<string[]> {
  let lastBody: TelemetryIngestionResponse | undefined;
  for (const payload of buildAllAnomaliesSequence()) {
    const response = await page.request.post(`${apiOrigin}/devices/${deviceId}/telemetry`, {
      data: payload,
    });
    expect(response.status()).toBe(201);
    lastBody = (await response.json()) as TelemetryIngestionResponse;
  }
  return lastBody!.anomalies.map((anomaly) => anomaly.rule_id);
}

/** Scopes all telemetry-workspace queries away from the separate, unrelated
 * global incident list — located by its own accessible heading, never a
 * generated/hashed CSS selector. */
function telemetrySectionLocator(page: Page): Locator {
  return page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: "Device telemetry", level: 2 }) });
}

test.describe("telemetry workspace — complete anomaly scenario", () => {
  test("loads seeded telemetry, interface/BGP state, history, and anomaly incidents for a device", async ({
    page,
  }, testInfo) => {
    const deviceId = generateDeviceId("full", testInfo.parallelIndex);
    const apiOrigin = await openDashboardAndResolveApiOrigin(page);

    // Seed via the real, public HTTP API only — never the simulator script,
    // Docker, or a direct database connection.
    await seedConfiguration(page, apiOrigin, deviceId);
    const finalRuleOrder = await seedAllAnomaliesTelemetry(page, apiOrigin, deviceId);
    expect(finalRuleOrder).toEqual(["RULE-CPU-HIGH", "RULE-LINK-FLAP", "RULE-BGP-DOWN"]);

    const telemetrySection = telemetrySectionLocator(page);

    // 2-4. Locate the input by its accessible label and submit.
    await page.getByLabel("Telemetry device").fill(deviceId);
    await page.getByRole("button", { name: "Load telemetry" }).click();

    // 5-6. Waiting for the device ID to appear inherently waits through the
    // (possibly momentary) accessible loading state.
    await expect(telemetrySection.getByText(deviceId, { exact: true })).toBeVisible();

    // 7. Latest-sample summary. Backend/JSON serializes 95.0/50.0/0.0 as
    // JSON numbers; JavaScript has no trailing-zero concept, so the real
    // rendered text is "95%"/"50%"/"0" — asserted as actually rendered,
    // never as an assumed "95.0%"/"50.0%"/"0.0" literal.
    await expect(fieldValue(telemetrySection, "CPU utilization")).toHaveText("95%");
    await expect(fieldValue(telemetrySection, "Memory utilization")).toHaveText("50%");
    await expect(fieldValue(telemetrySection, "Interface error rate")).toHaveText("0");

    await expect(telemetrySection.getByText(INTERFACE_NAME).first()).toBeVisible();
    await expect(telemetrySection.getByText("up").first()).toBeVisible();
    await expect(telemetrySection.getByText(BGP_NEIGHBOR).first()).toBeVisible();
    await expect(telemetrySection.getByText("Idle").first()).toBeVisible();

    // 8-9. Semantic history table: exactly six body rows, ascending
    // received order (the backend contract already guarantees ascending
    // order; this proves the UI preserves it unmodified).
    const table = telemetrySection.getByRole("table");
    await expect(table).toBeVisible();
    const bodyRows = table.locator("tbody tr");
    await expect(bodyRows).toHaveCount(6);
    const expectedTimestamps = [0, 10, 20, 30, 40, 50].map((offset) => isoAt(offset));
    for (const [index, timestamp] of expectedTimestamps.entries()) {
      await expect(bodyRows.nth(index).locator("time")).toHaveAttribute("datetime", timestamp);
    }

    // 10-12. Exactly three anomaly incidents, all OPEN, with the three
    // expected rule references present.
    const anomalyItems = telemetrySection.locator("li.telemetry-anomaly");
    await expect(anomalyItems).toHaveCount(3);
    await expect(telemetrySection.getByText("RULE-CPU-HIGH", { exact: true })).toBeVisible();
    await expect(telemetrySection.getByText("RULE-LINK-FLAP", { exact: true })).toBeVisible();
    await expect(telemetrySection.getByText("RULE-BGP-DOWN", { exact: true })).toBeVisible();
    const openStatuses = anomalyItems.getByText("OPEN", { exact: true });
    await expect(openStatuses).toHaveCount(3);

    // 13. Evidence summaries present (pattern-matched, never a hardcoded
    // pre-computed count — rule-engine window behavior is not recomputed
    // here, only the real API response's own effect is observed).
    await expect(
      telemetrySection.getByText(/Latest CPU utilization: .+% across \d+ evidence sample\(s\)\./),
    ).toBeVisible();
    await expect(
      telemetrySection.getByText(
        new RegExp(
          `Interface ${INTERFACE_NAME.replace("/", "\\/")}: \\d+ recorded transition\\(s\\)\\.`,
        ),
      ),
    ).toBeVisible();
    await expect(
      telemetrySection.getByText(
        new RegExp(`Neighbor ${BGP_NEIGHBOR.replace(".", "\\.")}: .+ -> Idle\\.`),
      ),
    ).toBeVisible();

    // 14. No literal "undefined" anywhere in the telemetry workspace.
    await expect(telemetrySection).not.toContainText("undefined");

    // 15. No duplicated resolve control inside the telemetry workspace —
    // resolution remains only in the separate global incident list.
    await expect(telemetrySection.getByRole("button", { name: "Resolve incident" })).toHaveCount(0);
  });
});

test.describe("telemetry workspace — empty state and responsive layout", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("shows non-error empty states for a device with no telemetry, at a narrow viewport, with no document overflow", async ({
    page,
  }, testInfo) => {
    const deviceId = generateDeviceId("empty", testInfo.parallelIndex);
    const apiOrigin = await openDashboardAndResolveApiOrigin(page);

    // Configuration only — deliberately no telemetry submission for this
    // device.
    await seedConfiguration(page, apiOrigin, deviceId);

    const telemetrySection = telemetrySectionLocator(page);

    // 5. Keyboard operability: focus and type via the keyboard, submit via
    // Enter rather than a mouse click.
    await page.getByLabel("Telemetry device").focus();
    await page.keyboard.type(deviceId);
    await page.keyboard.press("Enter");

    // 3-4. Both empty states are non-error: plain visible text, not inside
    // any `role="alert"` element.
    const noTelemetryMessage = telemetrySection.getByText(
      "No telemetry has been recorded for this device.",
    );
    const noAnomalyMessage = telemetrySection.getByText(
      "No anomaly incidents exist for this device.",
    );
    await expect(noTelemetryMessage).toBeVisible();
    await expect(noAnomalyMessage).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);

    // 6. The page itself must not develop horizontal document overflow at
    // this narrow viewport. The evaluate callback runs in the browser, but
    // this project's Node-targeted tsconfig (e2e/**/*.ts) has no DOM lib,
    // so `document` is referenced through a minimal structural cast rather
    // than the ambient (unavailable) `Document` type.
    const hasHorizontalOverflow = await page.evaluate(() => {
      const doc = (
        globalThis as unknown as {
          document: { documentElement: { scrollWidth: number; clientWidth: number } };
        }
      ).document;
      return doc.documentElement.scrollWidth > doc.documentElement.clientWidth;
    });
    expect(hasHorizontalOverflow).toBe(false);

    // 7. The workspace remains visible and usable at the narrow viewport.
    await expect(telemetrySection).toBeVisible();
    await expect(telemetrySection.getByText(deviceId, { exact: true })).toBeVisible();
    await expect(telemetrySection.getByRole("button", { name: "Refresh telemetry" })).toBeVisible();
  });
});
