import { expect, test, type Page } from "@playwright/test";

/** Fails the test on any uncaught page error or console error (a broken shape crashes a page). */
function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    // The browser logs every non-2xx fetch (e.g. a deep link to an unknown id 404s by design).
    // Other API failures surface as role="alert" notices, which the tests check separately.
    if (m.type() === "error" && !/Failed to load resource: .* 404/.test(m.text())) errors.push(`console: ${m.text()}`);
  });
  return errors;
}

const PAGES: [path: string, heading: RegExp][] = [
  ["/overview", /^Overview$/],
  ["/engineering/artifacts", /Artifact/i],
  ["/engineering/failures", /Failure/i],
  ["/engineering/sessions", /Session/i],
  ["/engineering/inspector", /Inspector|Events/i],
  ["/engineering/export", /^Export$/],
  ["/proposals", /proposals/i],
  ["/settings/general", /General|Settings/i],
  ["/settings/users", /Users/i],
];

test("root redirects to the role landing", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/");
  await expect(page).toHaveURL(/\/(overview|engineering)/);
  await expect(page.locator("h1")).toBeVisible();
  expect(errors).toEqual([]);
});

for (const [path, heading] of PAGES) {
  test(`deep link ${path} renders`, async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto(path);
    await expect(page.locator("h1").first()).toHaveText(heading);
    await page.waitForLoadState("networkidle");
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test("proposals page shows the loop status", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/proposals");
  const status = page.getByTestId("loop-status");
  await expect(status).toBeVisible();
  await expect(status).toContainText(/Analyst/);
  await expect(status).toContainText(/PR creation/);
  expect(errors).toEqual([]);
});

/** Needs at least one proposal (the Phase 5 container check seeds two). Approving one in
 * status `proposed` proves the audited approval and, without GitHub, the "not configured" note. */
test("proposal detail shows diff and audit, and approval is recorded", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/proposals");
  await page.waitForLoadState("networkidle");
  const rows = page.locator("tbody tr[tabindex='0']");
  test.skip((await rows.count()) === 0, "no proposals seeded");
  const proposed = rows.filter({ hasText: "proposed" });
  const target = (await proposed.count()) > 0 ? proposed.first() : rows.first();
  await target.click();
  await expect(page).toHaveURL(/\/proposals\/[0-9a-f-]{36}/);
  await expect(page.getByText("Diff", { exact: true })).toBeVisible();
  const approve = page.getByRole("button", { name: "Approve" });
  if (await approve.isEnabled()) {
    await approve.click();
    await expect(page.getByTestId("proposal-state")).toContainText(/approved, PR creation not configured|PR/);
    await expect(page.getByTestId("audit-trail")).toContainText("proposal.approve");
  }
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("overview shows the improvement loop card", async ({ page }) => {
  await page.goto("/overview");
  await expect(page.getByTestId("loop-card")).toBeVisible();
});

test("sessions list is paginated server-side", async ({ page }) => {
  await page.goto("/engineering/sessions");
  await expect(page.getByTestId("sessions-range")).toHaveText(/\d+–\d+ of \d+/);
});

test("filters live in the URL and survive a reload", async ({ page }) => {
  await page.goto("/overview?from=2026-01-01&to=2026-01-14&compare=none&bucket=week");
  await page.reload();
  await expect(page).toHaveURL(/from=2026-01-01&to=2026-01-14/);
  await expect(page).toHaveURL(/compare=none/);
  await expect(page).toHaveURL(/bucket=week/);
});

test("data table rows are keyboard reachable and open the detail", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/engineering/artifacts");
  const row = page.locator("tbody tr[tabindex='0']").first();
  await expect(row).toBeVisible();
  await row.focus();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/engineering\/artifacts\/[^/?]+/);
  await expect(page.locator("h1").first()).toBeVisible();
  await page.waitForLoadState("networkidle");
  expect(errors).toEqual([]);
});

test("session list opens a timeline", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/engineering/sessions");
  const row = page.locator("tbody tr[tabindex='0']").first();
  await expect(row).toBeVisible();
  await row.click();
  await expect(page).toHaveURL(/\/engineering\/sessions\/[^/?]+/);
  await page.waitForLoadState("networkidle");
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("dark mode applies from the stored preference", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("ouroboros-theme", "dark"));
  await page.goto("/overview");
  await expect(page.locator("html")).toHaveClass(/dark/);
});

test("mobile shell opens the sidebar sheet @mobile", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto("/overview");
  await page.getByRole("button", { name: /toggle sidebar/i }).first().click();
  await expect(page.getByRole("link", { name: "Failures" })).toBeVisible();
  expect(errors).toEqual([]);
});

/** Leadership persona (optional): E2E_LEADERSHIP_EMAIL is a principal with role
 * `leadership`, E2E_SESSION_ID a session with prompt text inside the retention window. */
test.describe("leadership", () => {
  const lead = process.env.E2E_LEADERSHIP_EMAIL;
  const sid = process.env.E2E_SESSION_ID;
  test.skip(!lead || !sid, "set E2E_LEADERSHIP_EMAIL and E2E_SESSION_ID");
  test.use({ extraHTTPHeaders: lead ? { "X-Auth-Request-Email": lead } : {} });

  test("lands on /overview, has no export nav, sees redaction chips", async ({ page }) => {
    const errors = watchErrors(page);
    await page.goto("/");
    await expect(page).toHaveURL(/\/overview/);
    await expect(page.getByRole("link", { name: "Export" })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Users" })).toHaveCount(0);
    await page.goto(`/engineering/sessions/${sid}`);
    await expect(page.getByText(/redacted \(\d[\d,]* chars\)/).first()).toBeVisible();
    expect(errors).toEqual([]);
  });
});
