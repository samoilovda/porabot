const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { chromium } = require("playwright-core");
const executable =
  process.env.CHROMIUM_PATH ||
  [
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    chromium.executablePath(),
  ].find((p) => fs.existsSync(p));

const HEATMAP = {
  start: "2026-08-01",
  end: "2026-10-18",
  days: [{ date: "2026-10-18", done: 1, not_today: 0, missed: 0, total: 1 }],
};
const habits = (n) =>
  Array.from({ length: n }, (_, i) => ({
    id: i + 1,
    text: "Habit " + (i + 1),
    score: 40,
    streak_current: 1,
    streak_best: 2,
    history_count: 3,
    done_count: 2,
    not_today_count: 0,
    missed_only_count: 1,
    missed_count: 1,
  }));

async function launch(t, { count, language = "en", dark = false }) {
  assert.ok(executable, "Install Chromium or set CHROMIUM_PATH");
  const browser = await chromium.launch({
    executablePath: executable,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  t.after(() => browser.close());
  const page = await browser.newPage({
    viewport: { width: 320, height: 640 },
    colorScheme: dark ? "dark" : "light",
  });
  fs.mkdirSync("/tmp/porabot-ui-screenshots", { recursive: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    ({ language }) => {
      window.Telegram = {
        WebApp: {
          initData: "local-synthetic-only",
          initDataUnsafe: { user: { language_code: language } },
          ready() {},
          expand() {},
          themeParams: {},
          onEvent() {},
        },
      };
    },
    { language },
  );
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "telegram.org")
      return route.fulfill({ contentType: "application/javascript", body: "" });
    if (url.pathname.includes("/api/miniapp/")) {
      const payload = url.pathname.endsWith("/profile")
        ? { language }
        : url.pathname.endsWith("/scores")
          ? { habits: habits(count) }
          : HEATMAP;
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(payload),
      });
    }
    const file = url.pathname.endsWith(".js")
      ? "app.js"
      : url.pathname.endsWith(".css")
        ? "style.css"
        : "index.html";
    await route.fulfill({
      contentType: file.endsWith(".js")
        ? "application/javascript"
        : file.endsWith(".css")
          ? "text/css"
          : "text/html",
      body: fs.readFileSync(path.join("bot/webapp", file), "utf8"),
    });
  });
  await page.goto("https://porabot.test/miniapp/");
  await page.waitForSelector("#heatmap button[data-date]");
  await page.waitForSelector(".habit-card");
  return { page, errors };
}

for (const count of [6, 20]) {
  test(`320px with ${count} habits: History link is on the first screen and jumps to the calendar`, async (t) => {
    const { page, errors } = await launch(t, { count });
    const link = page.locator("#history-link");
    assert.equal(await link.innerText(), "History");
    const box = await link.boundingBox();
    assert.ok(box.y + box.height <= 640, "visible without scrolling");
    assert.ok(box.height >= 44 && box.width >= 44);
    assert.equal(await page.evaluate(() => window.scrollY), 0);
    await link.click();
    await page.waitForFunction(() => window.scrollY > 0);
    // The calendar is the last section, so the page may stop scrolling before
    // the heading reaches the very top; it must be inside the viewport.
    const rect = await page
      .locator("#heatmap-title")
      .evaluate((n) => n.getBoundingClientRect().toJSON());
    assert.ok(
      rect.top >= 0 && rect.bottom <= 640,
      `calendar heading at ${rect.top}px`,
    );
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `/tmp/porabot-ui-screenshots/history-link-320-${count}.png`,
    });
    assert.deepEqual(errors, []);
  });
}

test("History link is localized (ru, es, pt)", async (t) => {
  for (const [language, text] of [
    ["ru", "История"],
    ["es", "Historial"],
    ["pt", "Histórico"],
  ]) {
    const { page } = await launch(t, { count: 1, language });
    await page.waitForFunction(
      (l) => document.documentElement.lang === l,
      language,
    );
    assert.equal(await page.locator("#history-link").innerText(), text);
  }
});
