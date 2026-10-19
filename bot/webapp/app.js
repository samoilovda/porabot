/* Profile language is display-only; the server authenticates every API request. */
(function () {
  "use strict";
  var STRINGS = {
    en: {
      scoresTitle: "Habit strength",
      heatmapTitle: "Recorded activity",
      loading: "Loading…",
      empty: "No active habits yet. Add one from Habits in the bot.",
      noHistory: "No check-ins yet",
      strength: "Strength",
      explain:
        "A smoothed score of recorded check-ins, not a completion percentage.",
      history: "Recorded history",
      done: "done",
      missed: "missed or skipped",
      streak: "Current streak",
      best: "best",
      noRecords: "No records",
      open: "Open this app from inside Telegram.",
      error: "Could not load this section.",
      timeout: "The request took too long.",
      auth: "Close this app and open it again from the bot to sign in.",
      rate: "Please wait before trying again.",
      retry: "Try again",
      calendar: "Calendar",
      list: "List of days",
      previous: "Previous month",
      next: "Next month",
      date: "Date",
      profileError:
        "Profile language could not be loaded. Your data is still available.",
    },
    ru: {
      scoresTitle: "Устойчивость привычек",
      heatmapTitle: "Записанные отметки",
      loading: "Загрузка…",
      empty: "Активных привычек пока нет. Добавь привычку в боте.",
      noHistory: "Пока нет отметок",
      strength: "Устойчивость",
      explain:
        "Сглаженная оценка по истории отметок, а не процент выполненного.",
      history: "Записанная история",
      done: "выполнено",
      missed: "пропущено",
      streak: "Текущая серия",
      best: "рекорд",
      noRecords: "Нет записей",
      open: "Открой приложение из Telegram.",
      error: "Не удалось загрузить этот раздел.",
      timeout: "Слишком долго ждём ответа.",
      auth: "Закрой приложение и открой его заново из бота, чтобы войти.",
      rate: "Подожди перед следующей попыткой.",
      retry: "Повторить",
      calendar: "Календарь",
      list: "Список дней",
      previous: "Предыдущий месяц",
      next: "Следующий месяц",
      date: "Дата",
      profileError:
        "Не удалось загрузить язык профиля. Доступные данные показаны ниже.",
    },
    es: {
      scoresTitle: "Constancia de los hábitos",
      heatmapTitle: "Actividad registrada",
      loading: "Cargando…",
      empty: "Aún no hay hábitos activos. Añade uno desde el bot.",
      noHistory: "Aún no hay registros",
      strength: "Constancia",
      explain:
        "Una puntuación suavizada del historial, no un porcentaje de cumplimiento.",
      history: "Historial registrado",
      done: "hecho",
      missed: "omitido",
      streak: "Racha actual",
      best: "récord",
      noRecords: "Sin registros",
      open: "Abre esta aplicación desde Telegram.",
      error: "No se pudo cargar esta sección.",
      timeout: "La solicitud tardó demasiado.",
      auth: "Cierra la aplicación y ábrela de nuevo desde el bot para entrar.",
      rate: "Espera antes de volver a intentarlo.",
      retry: "Reintentar",
      calendar: "Calendario",
      list: "Lista de días",
      previous: "Mes anterior",
      next: "Mes siguiente",
      date: "Fecha",
      profileError:
        "No se pudo cargar el idioma del perfil. Los datos disponibles se muestran abajo.",
    },
    pt: {
      scoresTitle: "Constância dos hábitos",
      heatmapTitle: "Atividade registrada",
      loading: "Carregando…",
      empty: "Ainda não há hábitos ativos. Adicione um pelo bot.",
      noHistory: "Ainda não há registros",
      strength: "Constância",
      explain:
        "Uma pontuação suavizada do histórico, não uma porcentagem de cumprimento.",
      history: "Histórico registrado",
      done: "feito",
      missed: "perdido ou pulado",
      streak: "Sequência atual",
      best: "recorde",
      noRecords: "Sem registros",
      open: "Abra este aplicativo dentro do Telegram.",
      error: "Não foi possível carregar esta seção.",
      timeout: "A solicitação demorou demais.",
      auth: "Feche o aplicativo e abra-o de novo pelo bot para entrar.",
      rate: "Aguarde antes de tentar novamente.",
      retry: "Tentar novamente",
      calendar: "Calendário",
      list: "Lista de dias",
      previous: "Mês anterior",
      next: "Próximo mês",
      date: "Data",
      profileError:
        "Não foi possível carregar o idioma do perfil. Os dados disponíveis são mostrados abaixo.",
    },
  };
  var tg = window.Telegram && window.Telegram.WebApp;
  var preliminary =
    tg &&
    tg.initDataUnsafe &&
    tg.initDataUnsafe.user &&
    tg.initDataUnsafe.user.language_code;
  // Telegram reports regional variants ("pt-br"); the primary subtag picks the strings.
  var preliminaryBase = String(preliminary || "").toLowerCase().split(/[-_]/)[0];
  var lang = STRINGS[preliminaryBase] ? preliminaryBase : "en";
  var t = STRINGS[lang];
  var payloads = {},
    states = {},
    controllers = {},
    generations = {},
    errors = {},
    retryAfter = {};
  var month = null,
    selectedDate = null,
    view = "calendar",
    expired = false;
  function el(id) {
    return document.getElementById(id);
  }
  function node(tag, text, className) {
    var n = document.createElement(tag);
    if (text !== undefined) n.textContent = text;
    if (className) n.className = className;
    return n;
  }
  function button(text, onClick) {
    var b = node("button", text);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }
  function applyLanguage(value) {
    lang = STRINGS[value] ? value : lang;
    t = STRINGS[lang];
    document.documentElement.lang = lang;
    el("scores-title").textContent = t.scoresTitle;
    el("heatmap-title").textContent = t.heatmapTitle;
    el("score-explanation").textContent = t.explain;
    el("calendar-view").textContent = t.calendar;
    el("list-view").textContent = t.list;
    el("month-prev").setAttribute("aria-label", t.previous);
    el("month-next").setAttribute("aria-label", t.next);
    Object.keys(states).forEach(renderStatus);
    if (payloads.scores) renderScores();
    if (payloads.heatmap) renderCalendar();
  }
  function theme(params) {
    if (/^#[0-9a-f]{6}$/i.test(params.bg_color || "")) {
      var hex = params.bg_color.slice(1);
      var brightness =
        0.299 * parseInt(hex.slice(0, 2), 16) +
        0.587 * parseInt(hex.slice(2, 4), 16) +
        0.114 * parseInt(hex.slice(4, 6), 16);
      document.documentElement.dataset.theme =
        brightness < 128 ? "dark" : "light";
    }
    var map = {
      bg_color: "--bg",
      text_color: "--text",
      hint_color: "--hint",
      secondary_bg_color: "--secondary-bg",
      button_color: "--button",
      button_text_color: "--button-text",
    };
    Object.keys(map).forEach(function (k) {
      if (params[k])
        document.documentElement.style.setProperty(map[k], params[k]);
    });
  }
  if (tg) {
    tg.ready();
    tg.expand();
    theme(tg.themeParams || {});
    if (tg.onEvent)
      tg.onEvent("themeChanged", function () {
        theme(tg.themeParams || {});
      });
  }
  function renderStatus(section) {
    var target = el(section + "-status");
    target.replaceChildren();
    target.setAttribute(
      "aria-busy",
      states[section] === "loading" ? "true" : "false",
    );
    if (states[section] === "loading") {
      target.textContent = t.loading;
      return;
    }
    if (states[section] !== "error") return;
    var error = errors[section] || {};
    var text =
      error.status === 401
        ? t.auth
        : error.status === 429
          ? t.rate
          : error.timeout
            ? t.timeout
            : section === "profile"
              ? t.profileError
              : t.error;
    target.appendChild(node("p", text));
    if (error.status !== 401 && !expired) {
      var retry = button(t.retry, function () {
        if (Date.now() >= (retryAfter[section] || 0)) load(section);
      });
      retry.disabled = Date.now() < (retryAfter[section] || 0);
      if (retry.disabled) {
        var wait = Math.max(0, retryAfter[section] - Date.now());
        setTimeout(function () {
          if (!expired && retry.isConnected) retry.disabled = false;
        }, wait);
      }
      target.appendChild(retry);
    }
  }
  async function request(section, path) {
    if (controllers[section]) controllers[section].abort();
    var controller = new AbortController();
    controllers[section] = controller;
    var timedOut = false;
    var timer = setTimeout(function () {
      timedOut = true;
      controller.abort();
    }, 10000);
    try {
      var response = await fetch(path, {
        headers: { "X-Telegram-Init-Data": tg.initData },
        signal: controller.signal,
      });
      if (!response.ok) {
        var err = new Error("request failed");
        err.status = response.status;
        if (response.status === 429) {
          var raw = response.headers.get("Retry-After");
          var seconds = Number(raw);
          retryAfter[section] =
            Number.isFinite(seconds) && raw
              ? Date.now() + Math.max(1, seconds) * 1000
              : Math.max(Date.now() + 1000, Date.parse(raw) || 0);
        }
        throw err;
      }
      return await response.json();
    } catch (error) {
      if (timedOut) error.timeout = true;
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  async function load(section) {
    if (expired || Date.now() < (retryAfter[section] || 0)) return;
    var generation = (generations[section] || 0) + 1;
    generations[section] = generation;
    states[section] = "loading";
    renderStatus(section);
    var paths = {
      profile: "/api/miniapp/profile",
      scores: "/api/miniapp/scores",
      heatmap: "/api/miniapp/heatmap?days=365",
    };
    try {
      var data = await request(section, paths[section]);
      if (generation !== generations[section] || expired) return;
      payloads[section] = data;
      states[section] = "ready";
      if (section === "profile") applyLanguage(data.language);
      if (section === "scores") renderScores();
      if (section === "heatmap") {
        if (!month) month = data.end.slice(0, 7);
        if (!selectedDate) selectedDate = data.end;
        renderCalendar();
      }
    } catch (error) {
      if (generation !== generations[section]) return;
      if (error.status === 401) expired = true;
      errors[section] = { status: error.status, timeout: !!error.timeout };
      states[section] = "error";
    }
    renderStatus(section);
  }
  function renderScores() {
    var container = el("scores");
    container.replaceChildren();
    var habits = payloads.scores.habits;
    if (!habits.length) {
      container.textContent = t.empty;
      return;
    }
    habits.forEach(function (habit) {
      var card = node("article", undefined, "habit-card");
      card.appendChild(node("h3", habit.text, "habit-name"));
      var hasHistory =
        habit.history_count !== undefined
          ? habit.history_count > 0
          : habit.score > 0 || habit.streak_best > 0;
      card.appendChild(
        node(
          "p",
          hasHistory ? t.strength + ": " + habit.score + "/100" : t.noHistory,
        ),
      );
      var bar = node("div", undefined, "score-bar-track");
      bar.setAttribute("role", "meter");
      bar.setAttribute("aria-label", t.strength);
      bar.setAttribute("aria-valuemin", "0");
      bar.setAttribute("aria-valuemax", "100");
      bar.setAttribute("aria-valuenow", String(habit.score));
      var fill = node("div", undefined, "score-bar-fill");
      fill.style.width = Math.max(0, Math.min(100, habit.score)) + "%";
      bar.appendChild(fill);
      card.appendChild(bar);
      card.appendChild(
        node(
          "p",
          t.streak +
            ": " +
            habit.streak_current +
            " · " +
            t.best +
            ": " +
            habit.streak_best,
          "habit-meta",
        ),
      );
      if (habit.history_count !== undefined && hasHistory)
        card.appendChild(
          node(
            "p",
            t.history +
              ": " +
              habit.done_count +
              " " +
              t.done +
              ", " +
              habit.missed_count +
              " " +
              t.missed,
            "habit-meta",
          ),
        );
      container.appendChild(card);
    });
  }
  function dateLabel(value) {
    return new Intl.DateTimeFormat(lang, {
      dateStyle: "long",
      timeZone: "UTC",
    }).format(new Date(value + "T12:00:00Z"));
  }
  function details(day) {
    return (
      dateLabel(day.date) +
      ": " +
      (day.total
        ? day.done +
          " " +
          t.done +
          ", " +
          (day.missed + day.not_today) +
          " " +
          t.missed
        : t.noRecords)
    );
  }
  function selectDay(day) {
    selectedDate = day.date;
    el("heatmap-detail").textContent = details(day);
    Array.from(el("heatmap").querySelectorAll("button[data-date]")).forEach(
      function (b) {
        b.tabIndex = b.dataset.date === selectedDate ? 0 : -1;
        b.setAttribute("aria-pressed", String(b.dataset.date === selectedDate));
      },
    );
  }
  function changeMonth(delta) {
    var date = new Date(month + "-01T12:00:00Z");
    date.setUTCMonth(date.getUTCMonth() + delta);
    var candidate = date.toISOString().slice(0, 7),
      data = payloads.heatmap;
    if (candidate < data.start.slice(0, 7) || candidate > data.end.slice(0, 7))
      return;
    month = candidate;
    selectedDate = month + "-01";
    if (selectedDate < data.start) selectedDate = data.start;
    renderCalendar();
  }
  function renderCalendar() {
    var data = payloads.heatmap;
    el("month-label").textContent = new Intl.DateTimeFormat(lang, {
      month: "long",
      year: "numeric",
      timeZone: "UTC",
    }).format(new Date(month + "-01T12:00:00Z"));
    el("month-prev").disabled = month <= data.start.slice(0, 7);
    el("month-next").disabled = month >= data.end.slice(0, 7);
    el("calendar-view").setAttribute(
      "aria-pressed",
      String(view === "calendar"),
    );
    el("list-view").setAttribute("aria-pressed", String(view === "list"));
    var grid = el("heatmap");
    grid.replaceChildren();
    grid.className = view === "list" ? "days-list" : "calendar-grid";
    var days = {};
    data.days.forEach(function (d) {
      days[d.date] = d;
    });
    var first = new Date(month + "-01T12:00:00Z"),
      weekday = (first.getUTCDay() + 6) % 7;
    var count = new Date(
      first.getUTCFullYear(),
      first.getUTCMonth() + 1,
      0,
    ).getDate();
    if (view === "calendar") {
      for (var k = 0; k < 7; k++) {
        var label = new Date(Date.UTC(2026, 0, 5 + k, 12));
        grid.appendChild(
          node(
            "span",
            new Intl.DateTimeFormat(lang, {
              weekday: "short",
              timeZone: "UTC",
            }).format(label),
            "weekday",
          ),
        );
      }
      for (var blank = 0; blank < weekday; blank++)
        grid.appendChild(node("span", ""));
    }
    if (!selectedDate || selectedDate.slice(0, 7) !== month)
      selectedDate = month + "-01";
    for (var index = 1; index <= count; index++) {
      var key = month + "-" + String(index).padStart(2, "0");
      var day = days[key] || {
        date: key,
        done: 0,
        missed: 0,
        not_today: 0,
        total: 0,
      };
      (function (item, num) {
        var cell = button(
          view === "list" ? details(item) : String(num),
          function () {
            selectDay(item);
          },
        );
        cell.className = "heat-cell";
        cell.dataset.date = item.date;
        cell.disabled = item.date < data.start || item.date > data.end;
        cell.dataset.level = String(
          item.total === 0
            ? 0
            : item.done === 0
              ? 1
              : item.done < item.total
                ? 2
                : 3,
        );
        cell.setAttribute("aria-label", details(item));
        cell.setAttribute("aria-pressed", String(item.date === selectedDate));
        cell.setAttribute("tabindex", item.date === selectedDate ? "0" : "-1");
        cell.addEventListener("keydown", function (event) {
          var shifts = {
            ArrowLeft: -1,
            ArrowRight: 1,
            ArrowUp: -7,
            ArrowDown: 7,
          };
          if (shifts[event.key] === undefined) return;
          event.preventDefault();
          var next = new Date(item.date + "T12:00:00Z");
          next.setUTCDate(next.getUTCDate() + shifts[event.key]);
          var target = next.toISOString().slice(0, 10);
          if (target < data.start || target > data.end) return;
          if (target.slice(0, 7) !== month) {
            changeMonth(target < item.date ? -1 : 1);
          }
          var nextCell = grid.querySelector(
            'button[data-date="' + target + '"]',
          );
          if (nextCell) {
            selectDay(
              days[target] || {
                date: target,
                done: 0,
                missed: 0,
                not_today: 0,
                total: 0,
              },
            );
            nextCell.focus();
          }
        });
        grid.appendChild(cell);
      })(day, index);
    }
    el("heatmap-legend").textContent =
      t.noRecords + " · " + t.done + " · " + t.missed;
  }
  el("month-prev").addEventListener("click", function () {
    changeMonth(-1);
  });
  el("month-next").addEventListener("click", function () {
    changeMonth(1);
  });
  el("calendar-view").addEventListener("click", function () {
    view = "calendar";
    if (payloads.heatmap) renderCalendar();
  });
  el("list-view").addEventListener("click", function () {
    view = "list";
    if (payloads.heatmap) renderCalendar();
  });
  window.addEventListener("pagehide", function () {
    Object.keys(controllers).forEach(function (section) {
      generations[section] = (generations[section] || 0) + 1;
      controllers[section].abort();
    });
  });
  applyLanguage(lang);
  if (!tg || !tg.initData) {
    el("status").textContent = t.open;
    return;
  }
  ["profile", "scores", "heatmap"].forEach(load);
})();
