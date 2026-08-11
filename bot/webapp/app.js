/* 4.6: Porabot Mini App frontend — vanilla JS, no build step, no framework.
 *
 * Auth: every API call sends the raw Telegram.WebApp.initData string in the
 * X-Telegram-Init-Data header. The server (bot/services/miniapp.py's
 * validate_init_data, called from bot/services/webserver.py) is the only
 * place that verifies it — this file never trusts anything about "who the
 * user is" beyond what Telegram.WebApp itself reports for display purposes.
 */
(function () {
  "use strict";

  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    tg.ready();
    tg.expand();
    applyThemeParams(tg.themeParams || {});
    if (tg.onEvent) {
      tg.onEvent("themeChanged", function () {
        applyThemeParams(tg.themeParams || {});
      });
    }
  }

  function applyThemeParams(theme) {
    var map = {
      bg_color: "--bg",
      text_color: "--text",
      hint_color: "--hint",
      secondary_bg_color: "--secondary-bg",
      button_color: "--button",
      button_text_color: "--button-text",
    };
    var root = document.documentElement.style;
    Object.keys(map).forEach(function (key) {
      if (theme[key]) {
        root.setProperty(map[key], theme[key]);
      }
    });
  }

  function initData() {
    return tg ? tg.initData || "" : "";
  }

  function setStatus(text, isError) {
    var el = document.getElementById("status");
    el.textContent = text || "";
    el.className = "status" + (isError ? " error" : "");
  }

  function apiFetch(path) {
    return fetch(path, {
      headers: { "X-Telegram-Init-Data": initData() },
    }).then(function (resp) {
      if (!resp.ok) {
        throw new Error("HTTP " + resp.status);
      }
      return resp.json();
    });
  }

  function renderScores(payload) {
    var container = document.getElementById("scores");
    container.innerHTML = "";
    if (!payload.habits.length) {
      container.textContent = "No active habits yet.";
      return;
    }
    payload.habits.forEach(function (habit) {
      var card = document.createElement("div");
      card.className = "habit-card";

      var name = document.createElement("div");
      name.className = "habit-name";
      name.textContent = habit.text;
      card.appendChild(name);

      var track = document.createElement("div");
      track.className = "score-bar-track";
      var fill = document.createElement("div");
      fill.className = "score-bar-fill";
      fill.style.width = Math.max(0, Math.min(100, habit.score)) + "%";
      track.appendChild(fill);
      card.appendChild(track);

      var meta = document.createElement("div");
      meta.className = "habit-meta";
      meta.textContent =
        habit.score + "% · 🔥 " + habit.streak_current + " (best " + habit.streak_best + ")";
      card.appendChild(meta);

      container.appendChild(card);
    });
  }

  function heatLevel(day) {
    if (day.total === 0) return 0;
    var ratio = day.done / day.total;
    if (ratio <= 0) return 1;
    if (ratio < 0.5) return 2;
    if (ratio < 1) return 3;
    return 4;
  }

  function renderHeatmap(payload) {
    var container = document.getElementById("heatmap");
    container.innerHTML = "";
    payload.days.forEach(function (day) {
      var cell = document.createElement("div");
      cell.className = "heat-cell";
      cell.dataset.level = String(heatLevel(day));
      cell.title = day.date + ": " + day.done + "/" + day.total + " done";
      container.appendChild(cell);
    });

    var legend = document.getElementById("heatmap-legend");
    legend.innerHTML = "";
    var lessLabel = document.createElement("span");
    lessLabel.textContent = "Less";
    legend.appendChild(lessLabel);
    [0, 1, 2, 3, 4].forEach(function (level) {
      var swatch = document.createElement("div");
      swatch.className = "heat-cell";
      swatch.dataset.level = String(level);
      legend.appendChild(swatch);
    });
    var moreLabel = document.createElement("span");
    moreLabel.textContent = "More";
    legend.appendChild(moreLabel);
  }

  function boot() {
    if (!initData()) {
      setStatus("Open this page from inside Telegram to see your data.", true);
      return;
    }
    setStatus("Loading…");
    Promise.all([apiFetch("/api/miniapp/scores"), apiFetch("/api/miniapp/heatmap?days=90")])
      .then(function (results) {
        renderScores(results[0]);
        renderHeatmap(results[1]);
        setStatus("");
      })
      .catch(function (err) {
        setStatus("Failed to load data: " + err.message, true);
      });
  }

  boot();
})();
