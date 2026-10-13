import { expect, test, type Page } from "@playwright/test";

const NETWORK_STATE_PATHNAME = "/network/state";
const NODE_IDS = ["spine-1", "spine-2", "leaf-1", "leaf-2", "host-1", "host-2"];

function summaryCard(page: Page, name: string) {
  return page.getByRole("heading", { name, level: 3 }).locator("xpath=..");
}

/**
 * NPE-1D2: the DEFAULT view is the live network workspace, rendered from the
 * real GET /network/state. `scripts/browser_e2e.py` seeds one deterministic
 * baseline operational state through the real ingestion service before this
 * runs (collection `browser-e2e-baseline`, collected_at 2026-10-07T12:00:00Z),
 * so this proves real API data reaches the workspace. No frontend mock, no
 * Containerlab. Degraded/recovery rendering and the no-snapshot state are
 * covered by component tests and the live Lab 1 proof.
 */
test.describe("live network workspace (seeded baseline)", () => {
  test("renders the real baseline snapshot as the default view", async ({ page }) => {
    const networkResponsePromise = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === NETWORK_STATE_PATHNAME,
    );

    await page.goto("/");
    await expect(page.getByRole("heading", { name: "Live Network", level: 1 })).toBeVisible();

    const networkResponse = await networkResponsePromise;
    expect(networkResponse.status()).toBe(200);
    const body = (await networkResponse.json()) as { collection_id: string; collected_at: string };
    expect(body.collection_id).toBe("browser-e2e-baseline");

    await expect(page.getByRole("region", { name: "Network summary" })).toBeVisible();

    // Six declared nodes.
    for (const id of NODE_IDS) {
      await expect(page.getByTestId(`node-${id}`)).toBeVisible();
    }

    // BGP, ECMP, reachability from the real snapshot.
    await expect(summaryCard(page, "BGP adjacencies")).toContainText("4 / 4 Established");
    await expect(summaryCard(page, "ECMP redundancy")).toContainText("leaf-1: 2 paths");
    await expect(summaryCard(page, "ECMP redundancy")).toContainText("leaf-2: 2 paths");
    await expect(summaryCard(page, "Host reachability")).toContainText("host-1 → 10.1.2.10 3/3");
    await expect(summaryCard(page, "Host reachability")).toContainText("host-2 → 10.1.1.10 3/3");
    await expect(summaryCard(page, "Presentation status")).toContainText("Healthy");

    // NPE-1E1C: the real GET /network/diagnosis of the healthy baseline.
    const rca = page.getByRole("region", { name: "Root-cause analysis" });
    await expect(rca).toContainText("No active root-cause diagnosis");
    await expect(page.locator("[data-diagnosis]")).toHaveCount(0);

    // Observation timestamp is visible and is the seeded collection time.
    const collected = summaryCard(page, "Observation").locator("time");
    await expect(collected).toBeVisible();
    await expect(collected).toHaveAttribute("datetime", body.collected_at);

    // Hosts carry no invented device ID and no BGP status.
    for (const host of ["host-1", "host-2"]) {
      const node = page.getByTestId(`node-${host}`);
      await expect(node).not.toContainText("lab1-");
      await expect(node).not.toContainText("BGP");
    }
    await page.getByTestId("node-host-1").click();
    const hostDetail = page.getByRole("complementary", { name: "host-1 details" });
    await expect(hostDetail).toBeVisible();
    await expect(hostDetail.getByRole("heading", { name: "BGP" })).toHaveCount(0);
    await expect(hostDetail).not.toContainText("device ID");

    // A router shows its platform device ID.
    await page.getByTestId("node-leaf-1").click();
    await expect(page.getByRole("complementary", { name: "leaf-1 details" })).toContainText(
      "device ID lab1-leaf-1",
    );

    // The existing tools stay reachable.
    await page.getByRole("link", { name: "Incidents & tools" }).click();
    await expect(page.getByRole("heading", { name: "Network Incidents", level: 1 })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Submit device configuration", level: 2 }),
    ).toBeVisible();

    await page.getByRole("link", { name: "Live Network" }).click();
    await expect(page.getByRole("heading", { name: "Live Network", level: 1 })).toBeVisible();
  });
});
