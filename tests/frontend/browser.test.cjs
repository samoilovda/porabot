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

async function launch(
  t,
  {
    width = 320,
    dark = false,
    failed = "",
    failureStatus = 500,
    clock = false,
  } = {},
) {
  assert.ok(
    executable,
    "Install Chromium or set CHROMIUM_PATH to run browser acceptance tests",
  );
  const browser = await chromium.launch({
    executablePath: executable,
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
  });
  t.after(() => browser.close());
  const page = await browser.newPage({
    viewport: { width, height: 800 },
    colorScheme: dark ? "dark" : "light",
  });
  if (clock) await page.clock.install();
  fs.mkdirSync("/tmp/porabot-ui-screenshots", { recursive: true });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.addInitScript(
    ({ dark }) => {
      window.Telegram = {
        WebApp: {
          initData: "local-synthetic-only",
          initDataUnsafe: { user: { language_code: "es" } },
          ready() {},
          expand() {},
          themeParams: dark
            ? {
                bg_color: "#17212b",
                text_color: "#f5f5f5",
                secondary_bg_color: "#232e3c",
              }
            : {},
          onEvent(name, cb) {
            window.onThemeChanged = cb;
          },
        },
      };
    },
    { dark },
  );
  await page.route("**/*", async (route) => {
    const url = new URL(route.request().url());
    if (url.hostname === "telegram.org")
      return route.fulfill({ contentType: "application/javascript", body: "" });
    if (url.pathname.includes("/api/miniapp/")) {
      if (failed && url.pathname.includes(failed)) {
        if (failureStatus === "timeout") return;
        return route.fulfill({
          status: failureStatus,
          headers: { "Retry-After": "1" },
          contentType: "application/json",
          body: "{}",
        });
      }
      const payload = url.pathname.endsWith("/profile")
        ? { language: "es" }
        : url.pathname.endsWith("/scores")
          ? {
              habits: [
                {
                  text: "Leer un nombre de hábito muy largo para comprobar la adaptación",
                  score: 20,
                  streak_current: 1,
                  streak_best: 1,
                  history_count: 1,
                  done_count: 1,
                  missed_count: 0,
                },
              ],
            }
          : {
              start: "2025-10-19",
              end: "2026-10-18",
              days: [
                {
                  date: "2026-10-18",
                  done: 1,
                  not_today: 0,
                  missed: 0,
                  total: 1,
                },
              ],
            };
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
  await page.waitForFunction(() => document.documentElement.lang === "es");
  return { page, errors };
}

test("320px browser: Spanish, calendar targets, keyboard selection and large text", async (t) => {
  const { page, errors } = await launch(t);
  await page.waitForSelector(".habit-card");
  assert.match(await page.locator("#scores").innerText(), /20\/100/);
  assert.equal(await page.locator('#heatmap button[tabindex="0"]').count(), 1);
  const boxes = await page.locator("#heatmap button").evaluateAll((nodes) =>
    nodes.map((n) => ({
      width: n.getBoundingClientRect().width,
      height: n.getBoundingClientRect().height,
      label: n.getAttribute("aria-label"),
    })),
  );
  assert.ok(boxes.every((b) => b.width >= 24 && b.height >= 24 && b.label));
  const selected = page.locator('#heatmap button[tabindex="0"]');
  await selected.focus();
  await selected.press("ArrowLeft");
  assert.equal(
    await page
      .locator('#heatmap button[aria-pressed="true"]')
      .getAttribute("data-date"),
    "2026-10-17",
  );
  assert.match(await page.locator("#heatmap-detail").innerText(), /2026/);
  await page.locator("#list-view").click();
  assert.ok(
    (await page.locator("#heatmap button").first().boundingBox()).height >= 44,
  );
  await page.addStyleTag({ content: "html{font-size:200%}" });
  assert.ok(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  );
  fs.mkdirSync("/tmp/porabot-ui-screenshots", { recursive: true });
  await page.screenshot({
    path: "/tmp/porabot-ui-screenshots/es-320-large-text.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
});

test("browser: dark theme, partial failure and retry keep loaded cards", async (t) => {
  const { page, errors } = await launch(t, {
    width: 390,
    dark: true,
    failed: "heatmap",
  });
  await page.waitForSelector(".habit-card");
  await page.waitForSelector("#heatmap-status button");
  assert.ok(await page.locator("#scores").isVisible());
  assert.equal(
    await page
      .locator("body")
      .evaluate((n) => getComputedStyle(n).backgroundColor),
    "rgb(23, 33, 43)",
  );
  await page.locator("#heatmap-status button").click();
  await page.waitForSelector("#heatmap-status button");
  assert.match(await page.locator("#scores").innerText(), /20\/100/);
  await page.screenshot({
    path: "/tmp/porabot-ui-screenshots/es-390-dark-error.png",
    fullPage: true,
  });
  assert.deepEqual(errors, []);
});

test("browser: expired credentials require reopening instead of retries", async (t) => {
  const { page } = await launch(t, { failed: "scores", failureStatus: 401 });
  await page.waitForFunction(() =>
    document.querySelector("#scores-status").textContent.includes("de nuevo"),
  );
  assert.equal(await page.locator("#scores-status button").count(), 0);
});

test("browser: stalled request times out and preserves other blocks", async (t) => {
  const { page } = await launch(t, {
    failed: "scores",
    failureStatus: "timeout",
    clock: true,
  });
  await page.clock.fastForward(10001);
  await page.waitForFunction(() =>
    document.getElementById("scores-status").textContent.includes("demasiado"),
  );
  assert.equal(await page.locator("#scores-status button").count(), 1);
  assert.ok((await page.locator("#heatmap button[data-date]").count()) > 0);
});

test("browser: rate limit respects Retry-After", async (t) => {
  const { page } = await launch(t, { failed: "scores", failureStatus: 429 });
  await page.waitForSelector("#scores-status button");
  assert.ok(await page.locator("#scores-status button").isDisabled());
});
