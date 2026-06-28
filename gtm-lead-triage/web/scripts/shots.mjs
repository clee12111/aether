import { chromium } from "@playwright/test";
import { mkdirSync } from "fs";
import { join, dirname } from "path";
import { fileURLToPath } from "url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = join(__dirname, "..", ".shots");
mkdirSync(OUT, { recursive: true });

const BASE = process.env.BASE_URL || "http://localhost:3000";

const routes = [
  { path: "/inbound", name: "inbound" },
  { path: "/outbound", name: "outbound" },
  { path: "/testing", name: "testing" },
  { path: "/architecture", name: "architecture" },
];

async function run() {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  for (const { path, name } of routes) {
    try {
      await page.goto(`${BASE}${path}`, { waitUntil: "networkidle", timeout: 10000 });
      await page.screenshot({ path: join(OUT, `${name}.png`), fullPage: false });
      console.log(`  ${name}.png saved`);
    } catch (e) {
      console.log(`  ${name}: FAILED (${e.message.slice(0, 60)})`);
    }
  }

  await browser.close();
  console.log(`\nScreenshots saved to web/.shots/`);
}

run();
