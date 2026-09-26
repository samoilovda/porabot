/* 4.6: Porabot Mini App frontend — vanilla JS, no build step, no framework.
 *
 * Auth: every API call sends the raw Telegram.WebApp.initData string in the
 * X-Telegram-Init-Data header. The server (bot/services/miniapp.py's
 * validate_init_data, called from bot/services/webserver.py) is the only
 * place that verifies it — this file never trusts anything about "who the
 * user is" beyond what Telegram.WebApp itself reports for display purposes.
 *
 * Step 10 (2026-09-26 audit remediation): this used to be English-only
 * regardless of the language the user actually set for the bot (/language),
 * with no <html lang>, and a heatmap day's only way to see its numbers was
 * a hover title — unreachable on the touch-only Telegram mobile client this
 * is mostly opened from. Now: strings come from STRINGS[lang] below (lang
 * fetched from /api/miniapp/profile, the bot PROFILE's language — NOT
 * Telegram client's own locale, a different, unrelated setting), dates
 * format via Intl.DateTimeFormat, and a heatmap cell is a focusable,
 * keyboard-activatable button that shows its details in a real on-screen
 * element instead of only a title tooltip.
 */
(function () {
  "use strict";

  var STRINGS = {
    ru: {
      scoresTitle: "Устойчивость привычек",
      heatmapTitle: "Карта выполнения",
      noHabits: "Пока нет активных привычек.",
      openInTelegram: "Открой эту страницу внутри Telegram, чтобы увидеть свои данные.",
      loading: "Загрузка…",
      failedToLoad: "Не удалось загрузить данные: ",
      less: "Меньше",
      more: "Больше",
      habitMeta: function (habit) {
        return habit.score + "% · 🔥 " + habit.streak_current + " (рекорд " + habit.streak_best + ")";
      },
      dayDetail: function (dateLabel, day) {
        return dateLabel + ": выполнено " + day.done + " из " + day.total;
      },
      dayAriaLabel: function (dateLabel, day) {
        return dateLabel + ", выполнено " + day.done + " из " + day.total;
      },
    },
    en: {
      scoresTitle: "Habit scores",
      heatmapTitle: "Completion heatmap",
      noHabits: "No active habits yet.",
      openInTelegram: "Open this page from inside Telegram to see your data.",
      loading: "Loading…",
      failedToLoad: "Failed to load data: ",
      less: "Less",
      more: "More",
      habitMeta: function (habit) {
        return habit.score + "% · 🔥 " + habit.streak_current + " (best " + habit.streak_best + ")";
      },
      dayDetail: function (dateLabel, day) {
        return dateLabel + ": " + day.done + "/" + day.total + " done";
      },
      dayAriaLabel: function (dateLabel, day) {
        return dateLabel + ": " + day.done + " of " + day.total + " done";
      },
    },
  };

  var DEFAULT_LANG = "en";
  var lang = DEFAULT_LANG;
  var t = STRINGS[DEFAULT_LANG];
  var dateFormatter = new Intl.DateTimeFormat(DEFAULT_LANG, { dateStyle: "medium" });

  function applyLanguage(languageCode) {
    lang = Object.prototype.hasOwnProperty.call(STRINGS, languageCode) ? languageCode : DEFAULT_LANG;
    t = STRINGS[lang];
    dateFormatter = new Intl.DateTimeFormat(lang, { dateStyle: "medium" });
    document.documentElement.lang = lang;
    document.getElementById("scores-title").textContent = t.scoresTitle;
    document.getElementById("heatmap-title").textContent = t.heatmapTitle;
  }

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
      container.textContent = t.noHabits;
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
      meta.textContent = t.habitMeta(habit);
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

  function showDayDetail(day) {
    var dateLabel = dateFormatter.format(new Date(day.date + "T00:00:00"));
    var detail = document.getElementById("heatmap-detail");
    detail.textContent = t.dayDetail(dateLabel, day);
    // Step 10: move focus to the detail element so a keyboard/screen-reader
    // user actually notices it changed — a hover title never reached them
    // in the first place, and a text update alone is easy to miss.
    detail.focus();
  }

  function renderHeatmap(payload) {
    var container = document.getElementById("heatmap");
    container.innerHTML = "";
    payload.days.forEach(function (day) {
      var cell = document.createElement("div");
      cell.className = "heat-cell";
      cell.dataset.level = String(heatLevel(day));
      // Step 10: a day's details used to be reachable only via a hover
      // `title` — unreachable on a touch-only client (Telegram mobile,
      // where this Mini App mostly opens) and to keyboard/screen-reader
      // users everywhere. Now a real, focusable, Enter/Space-activatable
      // control that shows its details in an on-screen element.
      var dateLabel = dateFormatter.format(new Date(day.date + "T00:00:00"));
      cell.setAttribute("role", "button");
      cell.setAttribute("tabindex", "0");
      cell.setAttribute("aria-label", t.dayAriaLabel(dateLabel, day));
      cell.addEventListener("click", function () {
        showDayDetail(day);
      });
      cell.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          showDayDetail(day);
        }
      });
      container.appendChild(cell);
    });

    var legend = document.getElementById("heatmap-legend");
    legend.innerHTML = "";
    var lessLabel = document.createElement("span");
    lessLabel.textContent = t.less;
    legend.appendChild(lessLabel);
    [0, 1, 2, 3, 4].forEach(function (level) {
      var swatch = document.createElement("div");
      swatch.className = "heat-cell";
      swatch.dataset.level = String(level);
      legend.appendChild(swatch);
    });
    var moreLabel = document.createElement("span");
    moreLabel.textContent = t.more;
    legend.appendChild(moreLabel);
  }

  function boot() {
    if (!initData()) {
      // No initData at all means we can't authenticate to fetch the bot
      // profile's language either — falls back to DEFAULT_LANG, same as
      // opening this page outside Telegram always has.
      setStatus(t.openInTelegram, true);
      return;
    }
    setStatus(t.loading);
    Promise.all([
      apiFetch("/api/miniapp/profile"),
      apiFetch("/api/miniapp/scores"),
      apiFetch("/api/miniapp/heatmap?days=90"),
    ])
      .then(function (results) {
        applyLanguage(results[0].language);
        renderScores(results[1]);
        renderHeatmap(results[2]);
        setStatus("");
      })
      .catch(function (err) {
        setStatus(t.failedToLoad + err.message, true);
      });
  }

  boot();
})();
