import { expect, test } from "@playwright/test";
import {
  countMatching,
  fieldValue,
  locateOpenIncidentCard,
  openDashboard,
  submitInvalidCiscoConfiguration,
  waitForIncidentRefresh,
} from "./helpers";

const DEVICE_ID = "spine-01";
const RAW_CONFIG_TEXT = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n";
const CONFIG_POST_PATHNAME = `/devices/${DEVICE_ID}/config`;
const INCIDENTS_GET_PATHNAME = "/incidents";
const IDENTITY = {
  deviceId: DEVICE_ID,
  ruleRef: "policy-acl-external-in",
  affectedResourceContains: "GigabitEthernet0/1",
};

test.describe("configuration submission -> incident refresh", () => {
  test("submits a Cisco IOS-XE configuration and surfaces the resulting incident through real HTTP and reload", async ({
    page,
  }) => {
    // 1-2. Navigate to the dashboard and confirm the initial real
    // GET /incidents completed successfully and the dashboard/form are
    // ready. Deliberately does not require the incident list to start
    // empty — the shared disposable database may already hold an incident
    // (including a RESOLVED one) left by another independently runnable
    // browser scenario.
    const { requests } = await openDashboard(page);

    // Assert network counts before any submission.
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(1);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(0);

    // 3-5. Submit the known invalid Cisco IOS-XE configuration through the
    // real form, observing the real POST and the real refresh GET.
    const { postResponse, refreshResponse } = await submitInvalidCiscoConfiguration(
      page,
      DEVICE_ID,
      RAW_CONFIG_TEXT,
    );

    // Assert the real POST response status.
    expect(postResponse.status()).toBe(201);

    // Wait for the visible success confirmation before scoping any further
    // status assertions to it — avoids racing the transient "Submitting
    // configuration…" status region, which uses different text.
    const submissionSuccess = page
      .getByRole("status")
      .filter({ hasText: "Configuration submitted successfully." });
    await expect(submissionSuccess).toBeVisible();

    // Assert the visible submission result.
    await expect(fieldValue(submissionSuccess, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(submissionSuccess, "Snapshot")).not.toBeEmpty();
    await expect(fieldValue(submissionSuccess, "Violations detected")).toHaveText("1");
    await expect(fieldValue(submissionSuccess, "Incidents created")).toHaveText("1");
    await expect(fieldValue(submissionSuccess, "Incidents updated")).toHaveText("0");

    // Expand the semantic "Normalized configuration" details region.
    await submissionSuccess.locator("summary", { hasText: "Normalized configuration" }).click();
    await expect(submissionSuccess.locator("pre")).toContainText("GigabitEthernet0/1");

    // Assert the automatic incident refresh occurred and network counts.
    expect(refreshResponse.status()).toBe(200);
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(2);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(1);

    // 6-7. Locate the exact OPEN incident this submission produced or
    // updated — matched on stable identity fields plus exact status OPEN,
    // never a bare/unscoped article locator and never an assumption that
    // the whole page contains exactly one article.
    const incidentCard = await locateOpenIncidentCard(page, IDENTITY);
    await expect(incidentCard).toBeVisible();
    await expect(fieldValue(incidentCard, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(incidentCard, "Affected resource")).toContainText("GigabitEthernet0/1");
    await expect(fieldValue(incidentCard, "Rule")).toHaveText("policy-acl-external-in");
    // Occurrence count is not asserted as an exact value: the shared
    // disposable database may already contain an OPEN incident with this
    // same fingerprint from another independently runnable browser
    // scenario, so a repeated submission can dedupe as an update rather
    // than a fresh creation. This scenario's binding responsibility is
    // that the persisted incident has a valid (positive integer)
    // occurrence count — exact deduplication counts remain covered by
    // lower-level (unit/integration) tests.
    await expect(fieldValue(incidentCard, "Occurrences")).toHaveText(/^[1-9]\d*$/);
    await expect(incidentCard.getByText("Medium", { exact: true })).toBeVisible();
    await expect(incidentCard.getByText("OPEN", { exact: true })).toBeVisible();

    await incidentCard.locator("summary", { hasText: "Evidence" }).click();
    await expect(fieldValue(incidentCard, "Interface")).toHaveText("GigabitEthernet0/1");
    await expect(fieldValue(incidentCard, "Direction")).toHaveText("Inbound");

    // 8-9. Reload the page and confirm the same matching OPEN incident
    // remains available through a real GET /incidents, proving persistence
    // in PostgreSQL rather than merely in React state.
    const reloadIncidentsResponse = waitForIncidentRefresh(page);
    await page.reload();

    const reloadResponse = await reloadIncidentsResponse;
    expect(reloadResponse.status()).toBe(200);
    expect(countMatching(requests, "GET", INCIDENTS_GET_PATHNAME)).toBe(3);
    expect(countMatching(requests, "POST", CONFIG_POST_PATHNAME)).toBe(1);

    const reloadedIncidentCard = await locateOpenIncidentCard(page, IDENTITY);
    await expect(reloadedIncidentCard).toBeVisible();
    await expect(fieldValue(reloadedIncidentCard, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(reloadedIncidentCard, "Affected resource")).toContainText(
      "GigabitEthernet0/1",
    );
    await expect(fieldValue(reloadedIncidentCard, "Rule")).toHaveText("policy-acl-external-in");
    await expect(fieldValue(reloadedIncidentCard, "Occurrences")).toHaveText(/^[1-9]\d*$/);
    await expect(reloadedIncidentCard.getByText("Medium", { exact: true })).toBeVisible();
    await expect(reloadedIncidentCard.getByText("OPEN", { exact: true })).toBeVisible();
  });
});
