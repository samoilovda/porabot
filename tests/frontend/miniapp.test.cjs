const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { parseHTML } = require("linkedom");
const tick = () => new Promise((resolve) => setImmediate(resolve));
async function boot({ language = "es", failed = "", status = 500 } = {}) {
  const { window, document } = parseHTML(
    fs.readFileSync("bot/webapp/index.html", "utf8"),
  );
  window.Telegram = {
    WebApp: {
      initData: "local-synthetic-fixture",
      initDataUnsafe: { user: { language_code: language } },
      ready() {},
      expand() {},
      onEvent() {},
    },
  };
  const fetch = async (path) => ({
    ok: !path.includes(failed || "NO_FAILURE"),
    status: path.includes(failed || "NO_FAILURE") ? status : 200,
    headers: {
      get() {
        return "1";
      },
    },
    json: async () =>
      path.includes("profile")
        ? { language }
        : path.includes("scores")
          ? {
              habits: [
                {
                  id: 1,
                  text: "Read",
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
              start: "2026-01-01",
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
            },
  });
  vm.runInNewContext(fs.readFileSync("bot/webapp/app.js", "utf8"), {
    window,
    document,
    fetch,
    Intl,
    Date,
    Promise,
    Error,
    Math,
    String,
    Object,
    AbortController,
    setTimeout,
    clearTimeout,
  });
  await tick();
  await tick();
  return { window, document };
}

test("Spanish profile is localized and strength is not a completion percentage", async () => {
  const { document } = await boot();
  assert.equal(document.documentElement.lang, "es");
  assert.match(document.getElementById("scores").textContent, /20\/100/);
  assert.doesNotMatch(document.getElementById("scores").textContent, /20%/);
});
test("calendar failure preserves successfully loaded habits and offers retry", async () => {
  const { document } = await boot({ language: "ru", failed: "heatmap" });
  assert.match(document.getElementById("scores").textContent, /Read/);
  assert.ok(document.querySelector("#heatmap-status button"));
});
test("calendar targets have accessible labels and a single tab entry", async () => {
  const { document } = await boot();
  const cells = Array.from(
    document.querySelectorAll("#heatmap button[data-date]"),
  );
  assert.ok(cells.length >= 28);
  assert.equal(
    cells.filter((c) => c.tabIndex === 0 || c.getAttribute("tabindex") === "0")
      .length,
    1,
  );
  assert.ok(cells.every((c) => c.getAttribute("aria-label")));
});
test("expired authentication gives recovery instructions and no retry loop", async () => {
  const { document } = await boot({
    language: "en",
    failed: "scores",
    status: 401,
  });
  assert.match(
    document.getElementById("scores-status").textContent,
    /open.*again|reopen/i,
  );
  assert.equal(document.querySelector("#scores-status button"), null);
});
