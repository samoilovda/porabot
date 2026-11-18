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
  days: [
    { date: "2026-10-18", done: 2, not_today: 0, missed: 0, total: 2 }, // 3
    { date: "2026-10-17", done: 1, not_today: 0, missed: 1, total: 2 }, // 2
    { date: "2026-10-16", done: 0, not_today: 0, missed: 2, total: 2 }, // 1
    { date: "2026-10-15", done: 0, not_today: 1, missed: 0, total: 1 }, // skip
  ],
};

async function launch(t, { width = 320, dark = false } = {}) {
  assert.ok(executable, "Install Chromium or set CHROMIUM_PATH");
  const browser = await chromium.launch({
    executablePath: executable,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  t.after(() => browser.close());
  const page = await browser.newPage({
    viewport: { width, height: 900 },
    colorScheme: dark ? "dark" : "light",
  });
  fs.mkdirSync("/tmp/porabot-ui-screenshots", { recursive: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(() => {
    window.Telegram = {
      WebApp: {
        initData: "local-synthetic-only",
        initDataUnsafe: { user: { language_code: "en" } },
        ready() {},
        expand() {},
        themeParams: {},
        onEvent() {},
      },
    };
  });
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "telegram.org")
      return route.fulfill({ contentType: "application/javascript", body: "" });
    if (url.pathname.includes("/api/miniapp/")) {
      const payload = url.pathname.endsWith("/profile")
        ? { language: "en" }
        : url.pathname.endsWith("/scores")
          ? { habits: [] }
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
  return { page, errors };
}

const luminance = (rgb) => {
  const [r, g, b] = rgb
    .match(/\d+/g)
    .slice(0, 3)
    .map((v) => {
      const c = Number(v) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

for (const dark of [false, true]) {
  test(`legend lists every level with a swatch (${dark ? "dark" : "light"}, 320px)`, async (t) => {
    const { page, errors } = await launch(t, { dark });
    const items = await page
      .locator("#heatmap-legend li")
      .evaluateAll((nodes) =>
        nodes.map((n) => ({
          text: n.textContent.trim(),
          level: n.querySelector(".swatch").dataset.level,
        })),
      );
    assert.deepEqual(items, [
      { text: "No records", level: "0" },
      { text: "skipped on purpose", level: "skip" },
      { text: "missed", level: "1" },
      { text: "partly done", level: "2" },
      { text: "done", level: "3" },
    ]);
    // the swatches really carry the cell colours
    const colour = (sel) =>
      page.locator(sel).evaluate((n) => getComputedStyle(n).backgroundColor);
    for (const level of ["0", "skip", "1", "2", "3"]) {
      assert.equal(
        await colour(`.swatch[data-level="${level}"]`),
        await colour(`#heatmap .heat-cell[data-level="${level}"] >> nth=0`),
      );
    }
    assert.ok(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await page.screenshot({
      path: `/tmp/porabot-ui-screenshots/legend-320-${dark ? "dark" : "light"}.png`,
      fullPage: true,
    });
    assert.deepEqual(errors, []);
  });
}

test("a complete day carries a check mark, a partial day does not", async (t) => {
  const { page } = await launch(t);
  const content = (date) =>
    page
      .locator(`#heatmap button[data-date="${date}"]`)
      .evaluate((n) => getComputedStyle(n, "::after").content);
  assert.equal(await content("2026-10-18"), '"✓"');
  assert.notEqual(await content("2026-10-17"), '"✓"');
});

test("dark palette: partial and complete days differ clearly in lightness", async (t) => {
  const { page } = await launch(t, { dark: true });
  const bg = (date) =>
    page
      .locator(`#heatmap button[data-date="${date}"]`)
      .evaluate((n) => getComputedStyle(n).backgroundColor);
  const partial = await bg("2026-10-17");
  const full = await bg("2026-10-18");
  assert.ok(ratio(partial, full) >= 2, `${partial} vs ${full}`);
  // text on the complete day stays readable
  const text = await page
    .locator('#heatmap button[data-date="2026-10-18"]')
    .evaluate((n) => getComputedStyle(n).color);
  assert.ok(ratio(full, text) >= 4.5, `${full} vs ${text}`);
});
