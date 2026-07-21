import { expect, test } from "@playwright/test";
import {
  INCIDENTS_GET_PATHNAME,
  countMatching,
  fieldTime,
  locateIncidentCard,
  openDashboard,
  submitInvalidCiscoConfiguration,
  waitForIncidentRefresh,
} from "./helpers";

const DEVICE_ID = "spine-01";
const RAW_CONFIG_TEXT = "hostname spine-01\n!\ninterface GigabitEthernet0/1\n!\n";
const RESOLVE_PATHNAME_PATTERN = /^\/incidents\/([^/]+)\/resolve$/;
const IDENTITY = {
  deviceId: DEVICE_ID,
  ruleRef: "policy-acl-external-in",
  affectedResourceContains: "GigabitEthernet0/1",
};

test.describe("incident resolution", () => {
  test("resolves an OPEN incident through real HTTP and preserves the RESOLVED state after reload", async ({
    page,
  }) => {
    // 1-2. Navigate to the dashboard and confirm the initial real
    // GET /incidents completed successfully and the dashboard/form are
    // ready. No assumption about the incident list's contents: the shared
    // disposable database may already be fresh, may already hold an OPEN
    // incident left by the configuration-submission scenario, or may
    // already hold a historical RESOLVED incident with the same identity.
    const { requests } = await openDashboard(page);

    // 3. Establish (create or update) an OPEN incident through the real,
    // visible configuration form — never a direct API call.
    const { postResponse, refreshResponse } = await submitInvalidCiscoConfiguration(
      page,
      DEVICE_ID,
      RAW_CONFIG_TEXT,
    );

    expect(postResponse.status()).toBe(201);
    expect(refreshResponse.status()).toBe(200);

    const submissionSuccess = page
      .getByRole("status")
      .filter({ hasText: "Configuration submitted successfully." });
    await expect(submissionSuccess).toBeVisible();

    // The other independently runnable browser scenario may already have
    // created the same OPEN fingerprint, so the created/updated split is
    // not fixed — only that exactly one of the two happened for this one
    // violation.
    interface SubmissionBody {
      violations_detected: number;
      incidents_created: number;
      incidents_updated: number;
    }
    const submissionBody = (await postResponse.json()) as SubmissionBody;

    expect(submissionBody.violations_detected).toBe(1);
    expect(Number.isInteger(submissionBody.incidents_created)).toBe(true);
    expect(Number.isInteger(submissionBody.incidents_updated)).toBe(true);
    expect(submissionBody.incidents_created).toBeGreaterThanOrEqual(0);
    expect(submissionBody.incidents_updated).toBeGreaterThanOrEqual(0);
    expect(submissionBody.incidents_created + submissionBody.incidents_updated).toBe(1);
    const isValidOutcome =
      (submissionBody.incidents_created === 1 && submissionBody.incidents_updated === 0) ||
      (submissionBody.incidents_created === 0 && submissionBody.incidents_updated === 1);
    expect(isValidOutcome).toBe(true);

    // 5. Locate the exact OPEN incident this submission produced or
    // updated — matched on stable identity fields plus exact status OPEN.
    // Requires exactly one match, so a historical RESOLVED card with the
    // same identity can never be selected instead.
    const openCard = await locateIncidentCard(page, IDENTITY, "OPEN");
    await expect(openCard.getByText("OPEN", { exact: true })).toBeVisible();
    const resolveButton = openCard.getByRole("button", { name: "Resolve incident" });
    await expect(resolveButton).toBeVisible();
    await expect(fieldTime(openCard, "Updated")).toBeVisible();
    await expect(openCard.locator("dt", { hasText: /^Resolved$/ })).toHaveCount(0);

    // Baseline GET /incidents count, taken after the submission's own
    // refresh has already completed, so the upcoming resolution's effect
    // on this count can be measured in isolation.
    const getCountBeforeResolve = countMatching(requests, "GET", INCIDENTS_GET_PATHNAME);

    // 6. Install request/response observation for the real resolution POST
    // *before* clicking, so a fast response can never be missed. Matches
    // only the resolution endpoint shape — the incident ID is never
    // hardcoded, only observed.
    const resolveRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" &&
        RESOLVE_PATHNAME_PATTERN.test(new URL(request.url()).pathname),
    );
    const resolveResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "POST" &&
        RESOLVE_PATHNAME_PATTERN.test(new URL(response.url()).pathname),
    );

    await resolveButton.click();

    const [resolveRequest, resolveResponse] = await Promise.all([
      resolveRequestPromise,
      resolveResponsePromise,
    ]);

    // Real request assertions — method, path shape, no body, no
    // Content-Type, exact Accept header.
    expect(resolveRequest.method()).toBe("POST");
    const resolvePathname = new URL(resolveRequest.url()).pathname;
    const pathMatch = resolvePathname.match(RESOLVE_PATHNAME_PATTERN);
    expect(pathMatch).not.toBeNull();
    const encodedIncidentId = pathMatch![1]!;
    expect(encodedIncidentId.length).toBeGreaterThan(0);
    expect(resolveRequest.postData()).toBeNull();

    const requestHeaders = await resolveRequest.allHeaders();
    const normalizedRequestHeaders = Object.fromEntries(
      Object.entries(requestHeaders).map(([key, value]) => [key.toLowerCase(), value]),
    );
    expect(normalizedRequestHeaders["content-type"]).toBeUndefined();
    expect(normalizedRequestHeaders["accept"]).toBe("application/json");

    // Real response assertions.
    expect(resolveResponse.status()).toBe(200);
    interface ResolvedIncidentBody {
      incident_id: string;
      status: string;
      updated_at: string;
      resolved_at: string;
    }
    const resolvedIncident = (await resolveResponse.json()) as ResolvedIncidentBody;

    expect(typeof resolvedIncident.incident_id).toBe("string");
    expect(resolvedIncident.incident_id.length).toBeGreaterThan(0);
    expect(resolvedIncident.status).toBe("RESOLVED");
    expect(typeof resolvedIncident.updated_at).toBe("string");
    expect(resolvedIncident.updated_at.length).toBeGreaterThan(0);
    expect(typeof resolvedIncident.resolved_at).toBe("string");
    expect(resolvedIncident.resolved_at.length).toBeGreaterThan(0);

    // The observed request path's encoded incident segment must correspond
    // to the returned incident_id when decoded — fail clearly if the
    // captured segment is not validly encoded, rather than let a decode
    // exception surface as an opaque test crash.
    let decodedRequestIncidentId: string;
    try {
      decodedRequestIncidentId = decodeURIComponent(encodedIncidentId);
    } catch (error) {
      throw new Error(
        `resolution request path segment is not validly URI-encoded: ${encodedIncidentId}`,
        { cause: error },
      );
    }
    expect(decodedRequestIncidentId).toBe(resolvedIncident.incident_id);

    // 8. Success UI — re-locate through the identity fields plus exact
    // status RESOLVED (the OPEN-filtered locator is stale the instant the
    // status changes, so it is never reused past this point).
    const resolvedCard = await locateIncidentCard(page, IDENTITY, "RESOLVED");
    await expect(resolvedCard.getByText("RESOLVED", { exact: true })).toBeVisible();
    await expect(resolvedCard.getByRole("button", { name: "Resolve incident" })).toHaveCount(0);
    await expect(fieldTime(resolvedCard, "Updated")).toHaveAttribute(
      "datetime",
      resolvedIncident.updated_at,
    );
    await expect(fieldTime(resolvedCard, "Resolved")).toHaveAttribute(
      "datetime",
      resolvedIncident.resolved_at,
    );

    // Unrelated dashboard controls remain visible and usable.
    await expect(page.getByRole("button", { name: "Refresh" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Submit configuration" })).toBeVisible();

    // 9. Resolution must have used the direct persisted POST response, not
    // an automatic list refresh — the GET /incidents count must be
    // unchanged from the baseline taken right after the submission's own
    // refresh, now that the RESOLVED UI state has already been awaited
    // above.
    const getCountAfterResolve = countMatching(requests, "GET", INCIDENTS_GET_PATHNAME);
    expect(getCountAfterResolve).toBe(getCountBeforeResolve);

    // 10. Reload persistence — install observation before reloading.
    const reloadIncidentsResponse = waitForIncidentRefresh(page);
    await page.reload();

    const reloadResponse = await reloadIncidentsResponse;
    expect(reloadResponse.status()).toBe(200);

    const reloadedResolvedCard = await locateIncidentCard(page, IDENTITY, "RESOLVED");
    await expect(reloadedResolvedCard.getByText("RESOLVED", { exact: true })).toBeVisible();
    await expect(fieldTime(reloadedResolvedCard, "Updated")).toHaveAttribute(
      "datetime",
      resolvedIncident.updated_at,
    );
    await expect(fieldTime(reloadedResolvedCard, "Resolved")).toHaveAttribute(
      "datetime",
      resolvedIncident.resolved_at,
    );
    await expect(
      reloadedResolvedCard.getByRole("button", { name: "Resolve incident" }),
    ).toHaveCount(0);
  });
});
