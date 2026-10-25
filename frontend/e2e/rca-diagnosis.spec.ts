import { expect, test, type Page } from "@playwright/test";

const DIAGNOSIS_PATHNAME = "/network/diagnosis";
const DIAGNOSED_LINK_ID = "leaf-1:eth1--spine-1:eth1";

function symptomsRegion(page: Page) {
  return page.getByRole("region", { name: "Correlated Symptoms" });
}

function rcaRegion(page: Page) {
  return page.getByRole("region", { name: "Probable Root Cause" });
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
    await expect(rca).toContainText("2 correlated symptoms");
    const symptoms = symptomsRegion(page).getByTestId("correlated-symptom");
    await expect(symptoms).toHaveCount(2);
    await expect(symptoms.nth(0)).toContainText("lab1-leaf-1");
    await expect(symptoms.nth(0)).toContainText("BGP neighbor 10.255.0.0");
    await expect(symptoms.nth(1)).toContainText("lab1-spine-1");
    await expect(symptoms.nth(1)).toContainText("BGP neighbor 10.255.0.1");

    // Exactly the declared link is marked, in the topology and in the table.
    await expect(page.locator("[data-diagnosis]")).toHaveCount(1);
    await expect(page.getByTestId(`link-${DIAGNOSED_LINK_ID}`)).toHaveAttribute(
      "data-diagnosis",
      "PHYSICAL_LINK_FAILURE",
    );
    const table = page.getByRole("table", { name: "Declared physical links" });
    await expect(table.getByTestId("rca-marker")).toHaveCount(1);
    const row = table.getByRole("row", { name: /^leaf-1 spine-1 eth1\s*↔\s*eth1/ });
    await expect(row.getByTestId("rca-marker")).toContainText(
      "Probable root cause: Physical link failure · HIGH confidence",
    );
    await expect(row).toContainText("DOWN"); // observed state stays truthful
  });

  test("evidence details open from the keyboard", async ({ page }) => {
    await page.goto("/");
    const rca = rcaRegion(page);
    await expect(rca.getByText("Physical link failure")).toBeVisible();

    const details = rca.locator("details.diagnosis-details");
    await expect(details).not.toHaveAttribute("open", "");
    await rca.getByText(/^View evidence/).focus();
    await page.keyboard.press("Enter");
    await expect(details).toHaveAttribute("open", "");
    await expect(details).toContainText("leaf-1 eth1 — DOWN");
    await expect(details).toContainText("Supporting");
  });

  test("390px viewport: no horizontal page overflow and the RCA content is usable", async ({
    page,
  }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    const rca = rcaRegion(page);
    await expect(rca.getByText("Physical link failure")).toBeVisible();
    await expect(symptomsRegion(page).getByTestId("correlated-symptom")).toHaveCount(2);
    await rca.getByText(/^View evidence/).click();
    await expect(rca.locator("details.diagnosis-details")).toHaveAttribute("open", "");

    // Page-expression strings: the e2e tsconfig has no DOM lib.
    const pageOverflow = await page.evaluate<number>(
      "document.documentElement.scrollWidth - window.innerWidth",
    );
    const panelOverflow = await page.evaluate<number>(
      "(() => { const p = document.querySelector('.diagnosis-panel'); return p.scrollWidth - p.clientWidth; })()",
    );
    expect(pageOverflow).toBeLessThanOrEqual(0);
    expect(panelOverflow).toBeLessThanOrEqual(0);

    // Physical Links collapse to one readable card per link (no squeezed table).
    await expect(page.locator("table.data-table--links thead")).toBeHidden();
    const failedLink = page.locator("tr.link-row").first();
    await expect(failedLink).toContainText("leaf-1");
    await expect(failedLink).toContainText("DOWN");
    await expect(failedLink).toContainText("Probable root cause: Physical link failure");
    const linksOverflow = await page.evaluate<number>(
      "(() => { const p = document.querySelector('.panel--links'); return p.scrollWidth - p.clientWidth; })()",
    );
    expect(linksOverflow).toBeLessThanOrEqual(0);
    await symptomsRegion(page).getByTestId("correlated-symptom").first().scrollIntoViewIfNeeded();
    await expect(symptomsRegion(page).getByTestId("correlated-symptom").first()).toBeInViewport({
      ratio: 0.1,
    });
  });
});
