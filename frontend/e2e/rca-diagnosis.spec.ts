import { expect, test, type Page } from "@playwright/test";

const DIAGNOSIS_PATHNAME = "/network/diagnosis";
const DIAGNOSED_LINK_ID = "leaf-1:eth1--spine-1:eth1";

function rcaRegion(page: Page) {
  return page.getByRole("region", { name: "Root-cause analysis" });
}

/**
 * NPE-1E1C: `scripts/browser_e2e.py` re-seeds the captured `leaf-1 eth1 down`
 * state through the real ingestion path (which also opens the two device-scoped
 * BGP incidents) and then runs ONLY the specs tagged below, so these assert the
 * real GET /network/diagnosis rendered by the real workspace. Nothing is
 * mocked or intercepted.
 */
test.describe("live root-cause analysis (seeded degraded state) @rca-degraded", () => {
  test("shows the real physical-link diagnosis with correlated symptoms and the marked link", async ({
    page,
  }) => {
    const diagnosisResponse = page.waitForResponse(
      (response) =>
        response.request().method() === "GET" &&
        new URL(response.url()).pathname === DIAGNOSIS_PATHNAME,
    );
    await page.goto("/");
    const response = await diagnosisResponse;
    expect(response.status()).toBe(200);
    const body = (await response.json()) as {
      collection_id: string;
      diagnoses: { diagnosis_type: string; confidence: string | null }[];
    };
    expect(body.collection_id).toBe("browser-e2e-degraded");
    expect(body.diagnoses.map((d) => [d.diagnosis_type, d.confidence])).toEqual([
      ["PHYSICAL_LINK_FAILURE", "HIGH"],
    ]);

    const rca = rcaRegion(page);
    await expect(rca.getByText("Physical link failure")).toBeVisible();
    await expect(rca).toContainText("HIGH");
    await expect(rca).toContainText("leaf-1 eth1 ↔ spine-1 eth1");
    await expect(rca).not.toContainText("Diagnosis updating");

    // Two correlated symptoms, listed independently.
    const symptoms = rca.getByTestId("correlated-symptom");
    await expect(symptoms).toHaveCount(2);
    await expect(symptoms.nth(0)).toContainText("lab1-leaf-1 · bgp-neighbor:10.255.0.0");
    await expect(symptoms.nth(1)).toContainText("lab1-spine-1 · bgp-neighbor:10.255.0.1");

    // Exactly the declared link is marked, in the topology and in the table.
    await expect(page.locator("[data-diagnosis]")).toHaveCount(1);
    await expect(page.getByTestId(`link-${DIAGNOSED_LINK_ID}`)).toHaveAttribute(
      "data-diagnosis",
      "PHYSICAL_LINK_FAILURE",
    );
    const table = page.getByRole("table", { name: "Declared physical links" });
    await expect(table.getByTestId("rca-marker")).toHaveCount(1);
    const row = table.getByRole("row", { name: /leaf-1:eth1 ↔ spine-1:eth1/ });
    await expect(row.getByTestId("rca-marker")).toContainText(
      "Probable root cause: Physical link failure · HIGH confidence",
    );
    await expect(row).toContainText("DOWN"); // observed state stays truthful
  });

  test("evidence details open from the keyboard", async ({ page }) => {
    await page.goto("/");
    const rca = rcaRegion(page);
    await expect(rca.getByText("Physical link failure")).toBeVisible();

    const details = rca.locator("details");
    await expect(details).not.toHaveAttribute("open", "");
    await rca.getByText(/^Evidence details/).focus();
    await page.keyboard.press("Enter");
    await expect(details).toHaveAttribute("open", "");
    await expect(details).toContainText("leaf-1 eth1 — DOWN");
    await expect(details).toContainText("Supports");
  });

  test("390px viewport: no horizontal page overflow and the RCA content is usable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const rca = rcaRegion(page);
    await expect(rca.getByText("Physical link failure")).toBeVisible();
    await expect(rca.getByTestId("correlated-symptom")).toHaveCount(2);
    await rca.getByText(/^Evidence details/).click();
    await expect(rca.locator("details")).toHaveAttribute("open", "");

    // Page-expression strings: the e2e tsconfig has no DOM lib.
    const pageOverflow = await page.evaluate<number>(
      "document.documentElement.scrollWidth - window.innerWidth",
    );
    const panelOverflow = await page.evaluate<number>(
      "(() => { const p = document.querySelector('.diagnosis-panel'); return p.scrollWidth - p.clientWidth; })()",
    );
    expect(pageOverflow).toBeLessThanOrEqual(0);
    expect(panelOverflow).toBeLessThanOrEqual(0);
    await expect(rca.getByTestId("correlated-symptom").first()).toBeInViewport({ ratio: 0.1 });
  });
});
