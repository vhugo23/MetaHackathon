import { expect, type Locator, type Page, type Response } from "@playwright/test";

export const INCIDENTS_GET_PATHNAME = "/incidents";

export interface RequestRecord {
  method: string;
  pathname: string;
}

/**
 * Pure network observation — never intercepts, mocks, or fulfills a
 * response. Must be installed before navigation so the initial
 * `GET /incidents` fired on mount is captured, not just requests made
 * afterward.
 */
export function trackRequests(page: Page): RequestRecord[] {
  const records: RequestRecord[] = [];
  page.on("request", (request) => {
    records.push({
      method: request.method(),
      pathname: new URL(request.url()).pathname,
    });
  });
  return records;
}

export function countMatching(records: RequestRecord[], method: string, pathname: string): number {
  return records.filter((record) => record.method === method && record.pathname === pathname)
    .length;
}

/** Waits for the next real `GET /incidents` response — never a route fulfillment. */
export function waitForIncidentRefresh(page: Page): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === INCIDENTS_GET_PATHNAME,
  );
}

/** Waits for the next real `POST` response at the given exact pathname. */
export function waitForPost(page: Page, pathname: string): Promise<Response> {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "POST" && new URL(response.url()).pathname === pathname,
  );
}

/** Resolves a `<dt>`/`<dd>` field pair by the `<dt>`'s exact text, scoped to `scope`. */
export function fieldValue(scope: Locator, label: string): Locator {
  return scope
    .locator("dt", { hasText: new RegExp(`^${escapeRegExp(label)}$`) })
    .locator("xpath=following-sibling::dd[1]");
}

/** Resolves the semantic `<time>` element inside a named `<dt>`/`<dd>` field pair. */
export function fieldTime(scope: Locator, label: string): Locator {
  return fieldValue(scope, label).locator("time");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function exactText(value: string): RegExp {
  return new RegExp(`^${escapeRegExp(value)}$`);
}

export interface DashboardHandle {
  requests: RequestRecord[];
}

/**
 * Navigates to the dashboard and confirms it reached a ready, successful
 * initial state — real `GET /incidents` observed and successful, heading
 * and configuration form visible. Deliberately does NOT require the
 * incident list to be empty: the shared disposable database used by this
 * suite may already hold incidents (including RESOLVED ones) left by
 * another independently runnable browser scenario.
 */
export async function openDashboard(page: Page): Promise<DashboardHandle> {
  const requests = trackRequests(page);
  const initialIncidentsResponse = waitForIncidentRefresh(page);

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Network Incidents", level: 1 })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "Submit device configuration", level: 2 }),
  ).toBeVisible();
  await expect(page.getByLabel("Device ID")).toBeVisible();
  await expect(page.getByLabel("Vendor")).toBeVisible();
  await expect(page.getByLabel("Raw configuration")).toBeVisible();

  const initialResponse = await initialIncidentsResponse;
  expect(initialResponse.status()).toBe(200);

  return { requests };
}

export interface SubmissionResult {
  postResponse: Response;
  refreshResponse: Response;
}

/**
 * Submits the given configuration through the real, visible configuration
 * form (never a direct API call, never `page.evaluate(fetch(...))`) and
 * returns the observed real POST/refresh-GET responses. Request/response
 * observers are installed before the click so a fast response can never be
 * missed.
 */
export async function submitInvalidCiscoConfiguration(
  page: Page,
  deviceId: string,
  rawConfigText: string,
): Promise<SubmissionResult> {
  await page.getByLabel("Device ID").fill(deviceId);
  await expect(page.getByLabel("Vendor")).toHaveValue("cisco-ios-xe");
  await page.getByLabel("Raw configuration").fill(rawConfigText);

  const postResponsePromise = waitForPost(page, `/devices/${deviceId}/config`);
  const refreshResponsePromise = waitForIncidentRefresh(page);

  await page.getByRole("button", { name: "Submit configuration" }).click();

  const [postResponse, refreshResponse] = await Promise.all([
    postResponsePromise,
    refreshResponsePromise,
  ]);

  return { postResponse, refreshResponse };
}

export interface IncidentIdentity {
  deviceId: string;
  ruleRef: string;
  /** A deterministic substring of `affected_resource` (e.g. an interface
   * name) — never a generated ID, fingerprint, or timestamp. */
  affectedResourceContains: string;
}

/**
 * Locates the single incident article matching the given stable identity
 * fields AND the given exact visible status. Never a bare, unscoped
 * `getByRole("article")` — the shared disposable database may already
 * contain another incident (a different status, or a historical/newer
 * recurrence) with the same device/rule/affected-resource combination, so
 * status must always be part of the match. Asserts exactly one match
 * before returning, so a caller never needs `.first()` on an unproven
 * locator.
 */
export async function locateIncidentCard(
  page: Page,
  identity: IncidentIdentity,
  status: string,
): Promise<Locator> {
  const card = page
    .getByRole("article")
    .filter({ has: page.locator("dd", { hasText: exactText(identity.deviceId) }) })
    .filter({ has: page.locator("dd", { hasText: exactText(identity.ruleRef) }) })
    .filter({ has: page.locator("dd", { hasText: identity.affectedResourceContains }) })
    .filter({ has: page.getByText(status, { exact: true }) });

  await expect(card).toHaveCount(1);
  return card;
}

/** Convenience wrapper over {@link locateIncidentCard} for the exact-OPEN case. */
export async function locateOpenIncidentCard(
  page: Page,
  identity: IncidentIdentity,
): Promise<Locator> {
  return locateIncidentCard(page, identity, "OPEN");
}
