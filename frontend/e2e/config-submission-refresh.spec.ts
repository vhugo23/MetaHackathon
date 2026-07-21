import { expect, test, type Locator, type Page } from "@playwright/test";

const DEVICE_ID = "spine-01";
const RAW_CONFIG_TEXT = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n";
const CONFIG_POST_PATHNAME = `/devices/${DEVICE_ID}/config`;
const INCIDENTS_GET_PATHNAME = "/incidents";

interface RequestRecord {
  method: string;
  pathname: string;
}

/**
 * Pure network observation — never intercepts, mocks, or fulfills a
 * response. Registered before `page.goto()` so the initial `GET /incidents`
 * fired on mount is counted, not just requests made after this call.
 */
function trackRequests(page: Page): RequestRecord[] {
  const records: RequestRecord[] = [];
  page.on("request", (request) => {
    records.push({
      method: request.method(),
      pathname: new URL(request.url()).pathname,
    });
  });
  return records;
}

function countMatching(records: RequestRecord[], method: string, pathname: string): number {
  return records.filter((record) => record.method === method && record.pathname === pathname)
    .length;
}

function waitForGetIncidents(page: Page) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      new URL(response.url()).pathname === INCIDENTS_GET_PATHNAME,
  );
}

function waitForPostConfig(page: Page) {
  return page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      new URL(response.url()).pathname === CONFIG_POST_PATHNAME,
  );
}

/** Resolves a `<dt>`/`<dd>` field pair by the `<dt>`'s exact text, scoped to `scope`. */
function fieldValue(scope: Locator, label: string): Locator {
  return scope
    .locator("dt", { hasText: new RegExp(`^${label}$`) })
    .locator("xpath=following-sibling::dd[1]");
}

test.describe("configuration submission -> incident refresh", () => {
  test("submits a Cisco IOS-XE configuration and surfaces the resulting incident through real HTTP and reload", async ({
    page,
  }) => {
    // Observer + waiter created before any navigation so the initial
    // GET /incidents fired on mount can never be missed.
    const requests = trackRequests(page);
    const initialIncidentsResponse = waitForGetIncidents(page);

    // 1. Navigate to the dashboard.
    await page.goto("/");

    // 2. Confirm the dashboard heading and configuration form are visible.
    await expect(page.getByRole("heading", { name: "Network Incidents", level: 1 })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Submit device configuration", level: 2 }),
    ).toBeVisible();
    await expect(page.getByLabel("Device ID")).toBeVisible();
    await expect(page.getByLabel("Vendor")).toBeVisible();
    await expect(page.getByLabel("Raw configuration")).toBeVisible();

    // 3. Wait for the initial real GET /incidents.
    const initialResponse = await initialIncidentsResponse;
    expect(initialResponse.status()).toBe(200);

    // 4. Confirm the dashboard reaches its actual empty-state presentation
    //    (fresh database: no incidents exist yet).
    await expect(page.getByText("No incidents detected.")).toBeVisible();

    // 5. Assert network counts before any submission.
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(1);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(0);

    // 6. Fill the submission form.
    await page.getByLabel("Device ID").fill(DEVICE_ID);
    await expect(page.getByLabel("Vendor")).toHaveValue("cisco-ios-xe");
    await page.getByLabel("Raw configuration").fill(RAW_CONFIG_TEXT);

    // 7. Create response waiters before clicking Submit so a fast response
    //    can never be missed.
    const postResponsePromise = waitForPostConfig(page);
    const refreshResponsePromise = waitForGetIncidents(page);

    // 8. Click Submit once.
    await page.getByRole("button", { name: "Submit configuration" }).click();

    // 9. Assert the real POST response status.
    const postResponse = await postResponsePromise;
    expect(postResponse.status()).toBe(201);

    // Wait for the visible success confirmation before scoping any further
    // status assertions to it — avoids racing the transient "Submitting
    // configuration…" status region, which uses different text.
    const submissionSuccess = page
      .getByRole("status")
      .filter({ hasText: "Configuration submitted successfully." });
    await expect(submissionSuccess).toBeVisible();

    // 10. Assert the visible submission result.
    await expect(fieldValue(submissionSuccess, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(submissionSuccess, "Snapshot")).not.toBeEmpty();
    await expect(fieldValue(submissionSuccess, "Violations detected")).toHaveText("1");
    await expect(fieldValue(submissionSuccess, "Incidents created")).toHaveText("1");
    await expect(fieldValue(submissionSuccess, "Incidents updated")).toHaveText("0");

    // 11. Expand the semantic "Normalized configuration" details region.
    await submissionSuccess.locator("summary", { hasText: "Normalized configuration" }).click();
    await expect(submissionSuccess.locator("pre")).toContainText("GigabitEthernet0/1");

    // 12. Wait for the automatic incident refresh and assert counts.
    const refreshResponse = await refreshResponsePromise;
    expect(refreshResponse.status()).toBe(200);
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(2);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(1);

    // 13. Confirm the visible incident's stable fields.
    const incidentCard = page.getByRole("article");
    await expect(incidentCard).toBeVisible();
    await expect(fieldValue(incidentCard, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(incidentCard, "Affected resource")).toContainText("GigabitEthernet0/1");
    await expect(fieldValue(incidentCard, "Rule")).toHaveText("policy-acl-external-in");
    await expect(fieldValue(incidentCard, "Occurrences")).toHaveText("1");
    await expect(incidentCard.getByText("Medium", { exact: true })).toBeVisible();
    await expect(incidentCard.getByText("OPEN", { exact: true })).toBeVisible();

    await incidentCard.locator("summary", { hasText: "Evidence" }).click();
    await expect(fieldValue(incidentCard, "Interface")).toHaveText("GigabitEthernet0/1");
    await expect(fieldValue(incidentCard, "Direction")).toHaveText("Inbound");

    // 14. Reload the page.
    const reloadIncidentsResponse = waitForGetIncidents(page);
    await page.reload();

    // 15. Wait for the reload GET /incidents and assert the same logical
    //     incident remains available through the real API and database.
    const reloadResponse = await reloadIncidentsResponse;
    expect(reloadResponse.status()).toBe(200);
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(3);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(1);

    const reloadedIncidentCard = page.getByRole("article");
    await expect(reloadedIncidentCard).toBeVisible();
    await expect(fieldValue(reloadedIncidentCard, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(reloadedIncidentCard, "Affected resource")).toContainText(
      "GigabitEthernet0/1",
    );
    await expect(fieldValue(reloadedIncidentCard, "Rule")).toHaveText("policy-acl-external-in");
    await expect(fieldValue(reloadedIncidentCard, "Occurrences")).toHaveText("1");
    await expect(reloadedIncidentCard.getByText("Medium", { exact: true })).toBeVisible();
    await expect(reloadedIncidentCard.getByText("OPEN", { exact: true })).toBeVisible();
  });
});
