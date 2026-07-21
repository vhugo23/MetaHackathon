import { expect, test } from "@playwright/test";
import {
  fieldValue,
  locateIncidentCard,
  openDashboard,
  waitForIncidentRefresh,
  waitForPost,
} from "./helpers";

const DEVICE_ID = "leaf-02";
const CONFIG_POST_PATHNAME = `/devices/${DEVICE_ID}/config`;
const RAW_CONFIG_TEXT =
  "hostname leaf-02\n!\ninterface Ethernet1\n   description Uplink to spine-01\n   ip address 10.0.1.1/30\n   no shutdown\n!\nrouter bgp 65002\n   neighbor 10.0.1.2 remote-as 65001\n!\n";
const IDENTITY = {
  deviceId: DEVICE_ID,
  ruleRef: "policy-acl-external-in-leaf-02",
  affectedResourceContains: "Ethernet1",
};

test.describe("Arista configuration submission", () => {
  test("Arista configuration submission creates an incident that persists after reload", async ({
    page,
  }) => {
    // 1-2. Navigate to the dashboard through the existing base URL and
    // confirm the form is ready — never assumes the incident list starts
    // empty, matching the existing scenarios' shared-disposable-database
    // convention.
    await openDashboard(page);

    // 3. Confirm Cisco remains the initial/default vendor before selecting
    // Arista — proves the widened select still defaults correctly in a
    // real browser, not merely in Vitest.
    await expect(page.getByLabel("Vendor")).toHaveValue("cisco-ios-xe");

    await page.getByLabel("Device ID").fill(DEVICE_ID);

    // 4-5. Select Arista EOS and confirm the select value actually changed.
    await page.getByLabel("Vendor").selectOption("arista-eos");
    await expect(page.getByLabel("Vendor")).toHaveValue("arista-eos");

    // 7. Enter the exact EOS configuration text.
    await page.getByLabel("Raw configuration").fill(RAW_CONFIG_TEXT);

    // Install request/response observation *before* clicking, so a fast
    // response can never be missed. Pure observation — never intercepts,
    // mocks, or fulfills a response; the request reaches the real FastAPI
    // service.
    const postRequestPromise = page.waitForRequest(
      (request) =>
        request.method() === "POST" && new URL(request.url()).pathname === CONFIG_POST_PATHNAME,
    );
    const postResponsePromise = waitForPost(page, CONFIG_POST_PATHNAME);
    const refreshResponsePromise = waitForIncidentRefresh(page);

    await page.getByRole("button", { name: "Submit configuration" }).click();

    const [postRequest, postResponse, refreshResponse] = await Promise.all([
      postRequestPromise,
      postResponsePromise,
      refreshResponsePromise,
    ]);

    // Real network request assertions.
    expect(postRequest.method()).toBe("POST");
    expect(new URL(postRequest.url()).pathname).toBe(CONFIG_POST_PATHNAME);
    interface SubmitRequestBody {
      vendor: string;
      raw_config_text: string;
    }
    const requestBody = JSON.parse(postRequest.postData() ?? "null") as SubmitRequestBody;
    expect(requestBody.vendor).toBe("arista-eos");
    expect(requestBody.raw_config_text).toBe(RAW_CONFIG_TEXT);

    expect(postResponse.status()).toBe(201);
    expect(refreshResponse.status()).toBe(200);

    const submissionSuccess = page
      .getByRole("status")
      .filter({ hasText: "Configuration submitted successfully." });
    await expect(submissionSuccess).toBeVisible();

    // The shared disposable database may already hold this exact
    // fingerprint from an earlier independent run, so the created/updated
    // split is not fixed — only that exactly one of the two happened for
    // this one violation.
    interface SubmissionResponseBody {
      violations_detected: number;
      incidents_created: number;
      incidents_updated: number;
    }
    const submissionBody = (await postResponse.json()) as SubmissionResponseBody;
    expect(submissionBody.violations_detected).toBe(1);
    expect(submissionBody.incidents_created + submissionBody.incidents_updated).toBe(1);

    // Locate the leaf-02 OPEN incident by stable identity, plus exact
    // status OPEN — never a bare/unscoped article locator, never an
    // assumption about list order or emptiness, never dependent on the
    // existing Cisco scenarios running before or after this one (distinct
    // device_id/rule_ref/affected_resource — no shared fingerprint is
    // structurally possible).
    const openCard = await locateIncidentCard(page, IDENTITY, "OPEN");
    await expect(openCard.getByText("OPEN", { exact: true })).toBeVisible();
    await expect(openCard.getByText("Medium", { exact: true })).toBeVisible();
    await expect(fieldValue(openCard, "Device")).toHaveText(DEVICE_ID);
    await expect(fieldValue(openCard, "Affected resource")).toContainText("Ethernet1");
    await expect(fieldValue(openCard, "Rule")).toHaveText("policy-acl-external-in-leaf-02");
    await expect(
      openCard.getByText("Assign ACL-EXTERNAL-IN inbound to Ethernet1", { exact: true }),
    ).toBeVisible();

    await openCard.locator("summary", { hasText: "Evidence" }).click();
    await expect(fieldValue(openCard, "Expected ACL")).toHaveText("ACL-EXTERNAL-IN");
    await expect(fieldValue(openCard, "Interface")).toHaveText("Ethernet1");
    await expect(fieldValue(openCard, "Direction")).toHaveText("Inbound");

    // Reload persistence — proves real PostgreSQL persistence and
    // list-API retrieval, never merely in-memory React state. The
    // incident is deliberately never resolved in this scenario — that
    // remains the dedicated resolution scenario's responsibility.
    const reloadIncidentsResponse = waitForIncidentRefresh(page);
    await page.reload();
    const reloadResponse = await reloadIncidentsResponse;
    expect(reloadResponse.status()).toBe(200);

    const reloadedCard = await locateIncidentCard(page, IDENTITY, "OPEN");
    await expect(reloadedCard.getByText("OPEN", { exact: true })).toBeVisible();
    await expect(fieldValue(reloadedCard, "Affected resource")).toContainText("Ethernet1");
  });
});
