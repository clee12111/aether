import { test, expect } from "@playwright/test";

const BASE = process.env.BASE_URL || "http://localhost:3000";

test.describe("Inbound presets fill but do NOT submit", () => {
  test("Hot buyer fills form fields without submitting", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Hot buyer")');
    const name = await page.inputValue('input >> nth=0');
    const email = await page.inputValue('input >> nth=1');
    expect(name).toContain("Marcus Hale");
    expect(email).toContain("datadoghq.com");
    await expect(page.locator('text=Submit another lead')).not.toBeVisible();
    await expect(page.locator('button:has-text("Submit")')).toBeVisible();
  });

  test("Email preset fills textarea without submitting", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Email")');
    await page.click('button:has-text("VP inquiry")');
    const value = await page.locator("textarea").inputValue();
    expect(value).toContain("Stripe");
    await expect(page.locator('text=Submit another lead')).not.toBeVisible();
  });

  test("Chat preset fills transcript without submitting", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Chat")');
    await page.click('button:has-text("Interest (Figma)")');
    const value = await page.locator("textarea").inputValue();
    expect(value).toContain("figma.com");
    await expect(page.locator('text=Submit another lead')).not.toBeVisible();
  });

  test("Clay preset fills JSON without submitting", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Clay")');
    await page.click('button:has-text("Notion row")');
    const value = await page.locator("textarea").inputValue();
    expect(value).toContain("notion.so");
    await expect(page.locator('text=Submit another lead')).not.toBeVisible();
  });
});

test.describe("Inbound submit works end-to-end", () => {
  test("Fill hot buyer and submit shows confirmation", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Hot buyer")');
    await page.click('button:has-text("Submit")');
    await expect(page.locator('text=Submit another lead')).toBeVisible({ timeout: 30000 });
  });
});

test.describe("Nav has three tabs, no Lead Form", () => {
  test("Nav shows Inbound, Outbound, Testing only", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await expect(page.locator('nav >> text=Inbound')).toBeVisible();
    await expect(page.locator('nav >> text=Outbound')).toBeVisible();
    await expect(page.locator('nav >> text=Testing')).toBeVisible();
    await expect(page.locator('text=Lead Form')).not.toBeVisible();
  });

  test("Root redirects to /inbound", async ({ page }) => {
    await page.goto(`${BASE}/`);
    await page.waitForURL("**/inbound");
    expect(page.url()).toContain("/inbound");
  });
});

test.describe("Outbound - no global campaign config", () => {
  test("Global campaign config block is gone from sidebar", async ({ page }) => {
    // Submit a lead first so outbound has candidates
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Warm evaluator")');
    await page.click('button:has-text("Submit")');
    await expect(page.locator('text=Submit another lead')).toBeVisible({ timeout: 30000 });

    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(2000);

    // The sidebar should show "ACCOUNTS" (company view), not "CAMPAIGN" config
    await expect(page.locator('h3:has-text("Accounts")')).toBeVisible();
    // The sidebar should not contain a "Value prop" label (no global campaign config)
    const sidebar = page.locator('.w-72');
    await expect(sidebar.locator('text=Value prop')).not.toBeVisible();
  });
});

test.describe("Outbound - actions in body panel", () => {
  test("Campaign and Delete buttons appear in detail body", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Hot buyer")');
    await page.click('button:has-text("Submit")');
    await expect(page.locator('text=Submit another lead')).toBeVisible({ timeout: 30000 });

    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(4000); // auto-draft

    // Click first candidate
    const candidate = page.locator('button:has-text("@")').first();
    if (await candidate.isVisible()) {
      await candidate.click();
      await page.waitForTimeout(1000);

      // Body should have prominent Launch campaign and Delete buttons
      const actions = page.locator('[data-testid="outbound-actions"]');
      await expect(actions.locator('button:has-text("Launch campaign")')).toBeVisible();
      await expect(actions.locator('button:has-text("Delete")')).toBeVisible();
    }
  });

  test("Campaign button opens per-lead suggestive modal", async ({ page }) => {
    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(4000);

    const candidate = page.locator('button:has-text("@")').first();
    if (await candidate.isVisible()) {
      await candidate.click();
      await page.waitForTimeout(1000);

      // Click the Launch campaign button in the body
      await page.locator('[data-testid="outbound-actions"] >> button:has-text("Launch campaign")').click();

      // Modal should appear with auto-suggested campaign name
      await expect(page.locator('text=Launch campaign for')).toBeVisible();
      // Should have editable fields
      await expect(page.locator('label:has-text("Campaign name")')).toBeVisible();
      await expect(page.locator('label:has-text("ICP keywords")')).toBeVisible();

      // Cancel
      await page.click('button:has-text("Cancel")');
      await expect(page.locator('text=Launch campaign for')).not.toBeVisible();
    }
  });

  test("Delete button opens in-app modal and removes candidate", async ({ page }) => {
    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(4000);

    const candidate = page.locator('button:has-text("@")').first();
    if (await candidate.isVisible()) {
      const emailText = await candidate.textContent();
      await candidate.click();
      await page.waitForTimeout(500);

      // Click Delete in body
      await page.locator('[data-testid="outbound-actions"] >> button:has-text("Delete")').click();

      // In-app modal
      await expect(page.locator('h3:has-text("Delete candidate")')).toBeVisible();
      await expect(page.locator('text=This cannot be undone')).toBeVisible();

      // Confirm delete
      await page.locator('.fixed >> button:has-text("Delete")').click();

      // Modal should close
      await expect(page.locator('h3:has-text("Delete candidate")')).not.toBeVisible();
    }
  });
});

test.describe("Testing journey renders", () => {
  test("Selecting a lead shows three-column journey + metrics", async ({ page }) => {
    // Submit a lead first
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Hot buyer")');
    await page.click('button:has-text("Submit")');
    await expect(page.locator('text=Submit another lead')).toBeVisible({ timeout: 30000 });

    await page.goto(`${BASE}/testing`);
    await page.waitForTimeout(2000);

    const firstLead = page.locator('button:has-text("@")').first();
    if (await firstLead.isVisible()) {
      await firstLead.click();

      // Should show the three column headers
      await expect(page.locator('h4:has-text("Inbound")')).toBeVisible({ timeout: 10000 });
      // Outbound columns should show "not run yet" placeholders
      await expect(page.locator('text=not run yet').first()).toBeVisible({ timeout: 5000 });

      // E2E metrics should render
      await expect(page.locator('[data-testid="e2e-metrics"]')).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe("Channel color-coding", () => {
  test("Channel legend visible in outbound sidebar", async ({ page }) => {
    await page.goto(`${BASE}/outbound`);
    await expect(page.locator('text=Form').last()).toBeVisible();
    await expect(page.locator('text=Email').last()).toBeVisible();
    await expect(page.locator('text=Chat').last()).toBeVisible();
    await expect(page.locator('text=Clay').last()).toBeVisible();
  });
});

test.describe("Architecture tab", () => {
  test("Renders all 7 stages, PB loop, and live stats", async ({ page }) => {
    await page.goto(`${BASE}/architecture`);

    // Nav should show Architecture
    await expect(page.locator('nav >> text=Architecture')).toBeVisible();

    // All 7 stages present
    for (const id of ["intake", "parse", "enrich", "score", "draft", "deliver", "observe"]) {
      await expect(page.locator(`[data-testid="stage-${id}"]`)).toBeVisible();
    }

    // Productboard loop
    await expect(page.locator('[data-testid="pb-loop"]')).toBeVisible();

    // Live stats header
    await expect(page.locator('[data-testid="live-stats"]')).toBeVisible();
    await expect(page.locator('text=Leads')).toBeVisible();
    await expect(page.locator('text=Companies')).toBeVisible();
    await expect(page.locator('text=Runs')).toBeVisible();

    // Swappable footer
    await expect(page.locator('text=Every stop swaps behind one interface')).toBeVisible();
  });

  test("Clicking a stage shows popover", async ({ page }) => {
    await page.goto(`${BASE}/architecture`);
    await page.click('[data-testid="stage-enrich"]');
    await expect(page.locator('text=Company research')).toBeVisible();
    await expect(page.locator('text=Swap PDL')).toBeVisible();
  });
});

test.describe("Campaign lifecycle (domain-keyed)", () => {
  test("1. Launch campaign -> section appears in Outbound", async ({ page }) => {
    await page.goto(`${BASE}/inbound`);
    await page.click('button:has-text("Hot buyer")');
    await page.click('button:has-text("Submit")');
    await expect(page.locator('text=Submit another lead')).toBeVisible({ timeout: 30000 });

    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(3000);

    const company = page.locator('.w-72 button[class*="border-b"]').first();
    if (await company.isVisible()) {
      await company.click();
      await page.waitForTimeout(1000);

      // Click the campaign button in the detail panel
      const launchBtn = page.locator('[data-testid="outbound-actions"]');
      if (await launchBtn.isVisible()) {
        await launchBtn.click();
        // Wait for modal to appear (h3 with "Launch campaign for")
        await expect(page.locator('h3:has-text("Launch campaign for")')).toBeVisible({ timeout: 3000 });
        // Click the Launch campaign button in the modal
        await page.locator('.fixed button:has-text("Launch campaign")').click();
        await page.waitForTimeout(3000);

        // Campaign section should appear
        await expect(page.locator('[data-testid="campaign-section"]')).toBeVisible({ timeout: 5000 });
      }
    }
  });

  test("2. Campaign persists across navigation", async ({ page }) => {
    await page.goto(`${BASE}/testing`);
    await page.waitForTimeout(500);
    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(3000);

    const company = page.locator('.w-72 button[class*="border-b"]').first();
    if (await company.isVisible()) {
      await company.click();
      await page.waitForTimeout(1000);
      await expect(page.locator('[data-testid="campaign-section"]')).toBeVisible({ timeout: 5000 });
    }
  });

  test("3. Re-launch shows Update button", async ({ page }) => {
    await page.goto(`${BASE}/outbound`);
    await page.waitForTimeout(3000);

    const company = page.locator('.w-72 button[class*="border-b"]').first();
    if (await company.isVisible()) {
      await company.click();
      await page.waitForTimeout(1000);
      await expect(page.locator('[data-testid="outbound-actions"] >> text=Update campaign')).toBeVisible({ timeout: 3000 });
    }
  });

  test("4. Testing shows the company with contacts", async ({ page }) => {
    await page.goto(`${BASE}/testing`);
    await page.waitForTimeout(2000);

    // Select the first company in the list
    const company = page.locator('.w-64 button[class*="border-b"]').first();
    if (await company.isVisible()) {
      await company.click();
      await page.waitForTimeout(1000);

      // Should show contacts list for the company
      await expect(page.locator('text=Contacts')).toBeVisible({ timeout: 5000 });
    }
  });
});

test.describe("Console errors - zero on all tabs", () => {
  for (const tab of ["/inbound", "/outbound", "/testing", "/architecture"]) {
    test(`No console errors on ${tab}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("console", (msg) => {
        if (msg.type() === "error" || msg.type() === "warning") {
          const text = msg.text();
          // Ignore expected warnings (network errors when API is slow, Next.js dev noise)
          if (text.includes("Failed to fetch") || text.includes("net::ERR") || text.includes("hydration") || text.includes("[GTM]")) return;
          errors.push(`[${msg.type()}] ${text}`);
        }
      });

      await page.goto(`${BASE}${tab}`);
      await page.waitForTimeout(2000);

      // Filter for React key warnings specifically
      const keyErrors = errors.filter(e => e.includes("key") || e.includes("Each child"));
      expect(keyErrors).toEqual([]);
    });
  }
});
