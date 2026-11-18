const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { parseHTML } = require("linkedom");
const tick = () => new Promise((resolve) => setImmediate(resolve));

const HEATMAP = {
  start: "2026-08-01",
  end: "2026-10-18",
  days: [
    { date: "2026-10-18", done: 1, not_today: 0, missed: 0, total: 1 },
    { date: "2026-09-30", done: 0, not_today: 0, missed: 1, total: 1 },
  ],
};

// heatmap: "ok" | "fail" | "hold" (pending until release()) — mutable via the returned handle.
async function boot({ heatmap = "ok" } = {}) {
  const { window, document } = parseHTML(
    fs.readFileSync("bot/webapp/index.html", "utf8"),
  );
  const ctl = { heatmap, held: [] };
  window.Telegram = {
    WebApp: {
      initData: "local-synthetic-fixture",
      initDataUnsafe: { user: { language_code: "en" } },
      ready() {},
      expand() {},
      onEvent() {},
    },
  };
  const fetch = async (path) => {
    const body = path.includes("profile")
      ? { language: "en" }
      : path.includes("scores")
        ? { habits: [] }
        : HEATMAP;
    if (path.includes("heatmap")) {
      if (ctl.heatmap === "hold")
        await new Promise((resolve) => ctl.held.push(resolve));
      if (ctl.heatmap === "fail")
        return { ok: false, status: 500, headers: { get: () => "1" } };
    }
    return {
      ok: true,
      status: 200,
      headers: { get: () => "1" },
      json: async () => body,
    };
  };
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
    Array,
    Number,
    AbortController,
    setTimeout,
    clearTimeout,
  });
  await tick();
  await tick();
  return { window, document, ctl };
}

const $ = (document, id) => document.getElementById(id);
const pressed = (document) =>
  document.querySelector('#heatmap button[aria-pressed="true"]');
function click(window, element) {
  element.dispatchEvent(new window.Event("click", { bubbles: true }));
}
function key(window, element, name) {
  const event = new window.Event("keydown", { bubbles: true });
  event.key = name;
  element.dispatchEvent(event);
}

test("month arrows are disabled and harmless while the calendar loads", async () => {
  const { window, document, ctl } = await boot({ heatmap: "hold" });
  assert.equal($(document, "month-prev").disabled, true);
  assert.equal($(document, "month-next").disabled, true);
  assert.doesNotThrow(() => {
    click(window, $(document, "month-prev"));
    click(window, $(document, "month-next"));
  });
  assert.equal($(document, "month-label").textContent, "");
  ctl.held.forEach((release) => release());
  await tick();
  await tick();
  assert.match($(document, "month-label").textContent, /October 2026/);
});

test("after a load error arrows stay disabled and Retry recovers", async () => {
  const { window, document, ctl } = await boot({ heatmap: "fail" });
  assert.equal($(document, "month-prev").disabled, true);
  assert.equal($(document, "month-next").disabled, true);
  assert.doesNotThrow(() => click(window, $(document, "month-prev")));
  const retry = document.querySelector("#heatmap-status button");
  assert.ok(retry);
  ctl.heatmap = "ok";
  click(window, retry);
  await tick();
  await tick();
  assert.match($(document, "month-label").textContent, /October 2026/);
  assert.equal(document.querySelector("#heatmap-status button"), null);
  assert.equal($(document, "month-next").disabled, true); // already at the last month
  assert.equal($(document, "month-prev").disabled, false);
});

test("arrows are disabled at the range boundaries", async () => {
  const { window, document } = await boot();
  click(window, $(document, "month-prev"));
  click(window, $(document, "month-prev"));
  assert.match($(document, "month-label").textContent, /August 2026/);
  assert.equal($(document, "month-prev").disabled, true);
  assert.equal($(document, "month-next").disabled, false);
});

test("the caption is filled on first render and names the selected day", async () => {
  const { document } = await boot();
  const caption = $(document, "heatmap-detail").textContent;
  assert.ok(caption.length > 0);
  assert.equal(caption, pressed(document).getAttribute("aria-label"));
  assert.match(caption, /October 18, 2026/);
});

test("the caption follows the selection after a month change and a view toggle", async () => {
  const { window, document } = await boot();
  click(window, $(document, "month-prev"));
  assert.equal(
    $(document, "heatmap-detail").textContent,
    pressed(document).getAttribute("aria-label"),
  );
  assert.match($(document, "heatmap-detail").textContent, /September 1, 2026/);
  click(window, $(document, "list-view"));
  assert.equal(
    $(document, "heatmap-detail").textContent,
    pressed(document).getAttribute("aria-label"),
  );
  click(window, $(document, "calendar-view"));
  assert.equal(
    $(document, "heatmap-detail").textContent,
    pressed(document).getAttribute("aria-label"),
  );
});

test("keyboard navigation across a month boundary updates month, selection and caption", async () => {
  const { window, document } = await boot();
  const first = document.querySelector(
    '#heatmap button[data-date="2026-10-01"]',
  );
  click(window, first);
  key(window, first, "ArrowLeft");
  assert.match($(document, "month-label").textContent, /September 2026/);
  assert.equal(pressed(document).dataset.date, "2026-09-30");
  assert.equal(
    $(document, "heatmap-detail").textContent,
    pressed(document).getAttribute("aria-label"),
  );
  assert.match($(document, "heatmap-detail").textContent, /1 missed/);
});
