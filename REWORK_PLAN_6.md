# План доработки Porabot, итерация 6 — инструкция для ИИ-агента

> **Статус: выполнен.** Все 19 пунктов (Фазы 1–3) применены
> последовательно, каждый — отдельным коммитом `fix(N.M): …` с
> регрессионным тестом. Финальная база: `BOT_TOKEN=test-token
> python -m pytest -q` → 505 passed, `ruff check .` → чисто. Документ
> оставлен как исторический план-протокол; не выполнять повторно.
>
> Расхождение с исходным текстом плана, обнаруженное в процессе работы:
> **2.4** изначально предполагал, что `l10n` можно получить в
> `handle_dispatcher_error` через параметр, заполняемый DI aiogram из
> `data["l10n"]` (которое устанавливает `DatabaseMiddleware`). Экспериментально
> подтверждено, что это не работает: `ErrorsMiddleware` переотправляет
> событие `"error"` через СОБСТВЕННый снимок `data`, сделанный ДО того,
> как вложенный observer ("message"/"callback_query") запускает свою
> цепочку middleware — `TelegramEventObserver.trigger`'овская распаковка
> `**kwargs` создаёт новый словарь на этой границе, так что мутация
> `data["l10n"]` из `DatabaseMiddleware` не долетает обратно до
> `ErrorsMiddleware`. Фактическая реализация вместо этого читает
> `language_code` прямо из `Update.message.from_user` /
> `Update.callback_query.from_user` — тот же приём, что и в
> `RateLimitMiddleware` (см. коммит `fix(2.4)`).
>
> Также по ходу работы обнаружен и исправлен смежный, более серьёзный баг:
> `reminders_listing.py`'s `cmd_find` (`/find`) был НЕДОСТИЖИМ —
> `reminders_wizard.router` (перехватывающий любой текст без активного
> состояния) регистрируется раньше `reminders_listing.router` в том же
> составном роутере, так что `/find milk` подхватывался как текст новой
> задачи прежде, чем `Command("find")` успевал сработать. Исправлено
> вместе с 2.5 (см. коммит `fix(2.5)`).

Результат сплошного аудита `main` на коммите `c9c2814` (после мерджа
PR #8). Проверено: композиционный корень, планировщик, все cron-джобы,
DAO, middleware, хендлеры, FSM-хранилище, веб-сервер, парсер, Docker/CI.

**Базовая линия:** `BOT_TOKEN=test-token python -m pytest -q` → `442 passed`,
`ruff check .` → чисто. Ни один шаг этого плана не должен это ломать.

Ключевые гипотезы проверены не «на глаз», а экспериментом (см. пометки
«Подтверждено» ниже): версии в `requirements.lock` — aiogram 3.29.1,
APScheduler 3.11.3.

---

## 0. Как работать с этим документом

1. Фазы по порядку: Фаза 1 → 2 → 3. Внутри фазы порядок шагов значим
   (например, 1.1 и 1.5 связаны, 1.4 меняет jobstore и должен идти до 3.4).
2. Один шаг = один атомарный коммит `fix(N.M): …` / `refactor(N.M): …`.
3. После каждого шага: `BOT_TOKEN=test-token python -m pytest -q` и
   `ruff check .` — зелено.
4. Для каждого шага Фаз 1–2 — регрессионный тест в `tests/services/`,
   который **падает до фикса и проходит после**. Проверь это явно.
5. Новые строки интерфейса — сразу в `bot/lexicon/ru.py`, `en.py`, `es.py`
   (`tests/services/test_l10n_key_coverage.py` это проверяет).
6. Схема — по-прежнему только мягкие миграции через
   `_add_column_if_missing` в `bot/database/engine.py`. Alembic не вводить.

### Инварианты — не трогать

- **Naive UTC в БД**, конвертация в локальное время только при рендере.
- **Anti-drift снуза привычек**: у habit-like recurring `execution_time`
  при снузе не перезаписывается.
- **`completed_for_execution_time`** — механизм скрытия выполненного цикла.
- **`pending_delete_at`** — soft-delete; каждый запрос активных
  напоминаний обязан его исключать.
- **`get_owned(reminder_id, user_id)`** — единственный способ достать
  напоминание по id из `callback_data`. Проверено: нарушений нет.
- **Job-таргеты APScheduler — только top-level функции**, доступ к
  живым объектам через `bot.context.get_context()`.

---

## Сводка находок

| # | Проблема | Приоритет | Класс |
|---|---|---|---|
| 1.1 | aiogram перехватывает SIGTERM/SIGINT сам → `stop_event` никогда не ставится, каждый деплой завершается `RuntimeError("Polling stopped unexpectedly")`; порядок shutdown закрывает сессию бота раньше планировщика | P1 | Надёжность |
| 1.2 | Исключение внутри `_execute_reminder` (например `database is locked`) → одноразовый date-job удалён, напоминание молча не сработает до следующего рестарта | P1 | Потеря уведомлений |
| 1.3 | Подтверждение «задача создана» и job в планировщике появляются **до** коммита транзакции; при падении коммита пользователь видит успех, а строки в БД нет | P1 | Потеря данных |
| 1.4 | Периодические джобы на bound-методах (`cleanup_expired` для обоих rate-limiter'ов) пиклятся по значению и чистят **копию** объекта — живые словари не чистятся никогда (утечка, которую «фикс 3.1» не закрыл) | P1 | Утечка памяти |
| 1.5 | Docker: healthcheck ничего не перезапускает (`docker compose` не реагирует на `unhealthy`), нет ротации логов → диск заполняется | P1 | Инфраструктура |
| 2.1 | «Обновить» в списке задач при неизменившемся списке → `message is not modified` → пользователю алерт «Что-то пошло не так» | P2 | UX/ошибка |
| 2.2 | Бизнес-изменение + правка сообщения в одной транзакции: упавший `edit_text` откатывает уже применённые изменения, но job в планировщике уже переставлен | P2 | Консистентность |
| 2.3 | `RateLimitMiddleware` и `DatabaseMiddleware` получают `Update`, а не `Message` — ответы «слишком много запросов» и «ошибка БД» никогда не отправляются | P2 | UX |
| 2.4 | Глобальный обработчик ошибок отвечает всем на русском, игнорируя язык пользователя | P2 | i18n |
| 2.5 | Неизвестная команда (`/settings`, `/stats`) превращается в задачу с текстом «/settings» | P2 | UX |
| 2.6 | Бот в группах создаёт задачи из любого текста и шлёт их в личку (у не начавших диалог — `Forbidden`) | P2 | Безопасность/UX |
| 2.7 | Блокировка бота (`my_chat_member`) не обрабатывается: пользователь навсегда остаётся в кандидатах всех минутных джобов | P2 | Нагрузка |
| 2.8 | Шторм после долгого простоя: все пропущенные one-off ставятся на `now+1min` разом; `TelegramRetryAfter` не учитывает `retry_after` | P2 | Flood control |
| 3.1 | HTTP rate-limiter по `request.remote` за reverse-proxy лимитирует всех пользователей как одного | P3 | Веб |
| 3.2 | Heartbeat подтверждает, что жив планировщик, а не связь с Telegram | P3 | Наблюдаемость |
| 3.3 | Нет `stop_grace_period` (по умолчанию 10 с) — деплой посреди рассылки брифов может убить процесс SIGKILL | P3 | Инфраструктура |
| 3.4 | Все служебные cron/interval-джобы персистятся в `jobs.sqlite`, хотя переигрываются на каждом старте; sync-jobstore дёргается по одному `get_job` на напоминание при reconcile | P3 | Производительность |
| 3.5 | `FsmState` строки не удаляются при `state.clear()`; `callback.message.text` может быть `None` в снузе | P3 | Гигиена |
| 3.6 | Документация: README пишет «135 tests», реально 442; `NotAppKeyWarning` в aiohttp | P3 | Гигиена |

---

## Фаза 1 — стабильность процесса и сохранность данных

### 1.1 Graceful shutdown не работает: aiogram перехватывает сигналы

**Где:** `bot/__main__.py` — `main()`, `_run_until_stopped`, блок `finally`.

**Что происходит.** `main()` регистрирует `loop.add_signal_handler(SIGTERM/SIGINT, stop_event.set)`,
но затем вызывает `dp.start_polling(bot)` с дефолтным `handle_signals=True`.
aiogram 3.29 внутри `start_polling` делает свой `loop.add_signal_handler`
на те же сигналы — а `add_signal_handler` **заменяет** предыдущий
обработчик. Подтверждено чтением `Dispatcher.start_polling` /
`_signal_stop_polling` в установленной версии.

Итог на каждом `docker compose up -d --build` / `docker stop`:
1. SIGTERM → aiogram останавливает polling штатно, `stop_event` не поставлен.
2. `_run_until_stopped` видит «polling завершился без сигнала» →
   `raise RuntimeError("Polling stopped unexpectedly without a shutdown signal.")`.
3. `finally` отрабатывает, но `asyncio.run` завершается трейсбеком и
   ненулевым кодом. В логах каждого деплоя — ложная «авария».

Вторая проблема в том же `finally`: `bot.session.close()` вызывается **до**
`scheduler.shutdown(wait=False)`. Джоб, который в этот момент шлёт
напоминание/бриф, получает ошибку закрытой сессии.

**Как исправить.**
1. `await _run_until_stopped(dp.start_polling(bot, handle_signals=False), stop_event)`.
   Собственные обработчики в `main()` оставить — они теперь единственные.
2. Порядок в `finally`: `scheduler.shutdown(wait=False)` → короткое ожидание
   уже запущенных async-джобов (например, `await asyncio.sleep(0)` +
   `asyncio.gather(*pending_job_tasks)` не нужен — достаточно поставить
   shutdown первым, чтобы новые джобы не стартовали) → `web_runner.cleanup()`
   → `bot.session.close()` → `dispose_engine(engine)`.
3. Windows-ветка (`NotImplementedError`) остаётся как есть.

**Тест.** `tests/services/test_main_shutdown.py`:
- `_run_until_stopped` с корутиной polling, которая завершается сама
  **после** `stop_event.set()`, не должна поднимать `RuntimeError`.
- Через `unittest.mock.patch("bot.__main__.Dispatcher")` убедиться, что
  `start_polling` вызван с `handle_signals=False` (можно вынести вызов в
  маленькую функцию `_polling_coro(dp, bot)` и проверять её).

---

### 1.2 Упавший `_execute_reminder` теряет напоминание до рестарта

**Где:** `bot/services/scheduler.py` — `execute_reminder_job`, `_execute_reminder`.

**Что происходит.** `_execute_reminder` ловит исключение, откатывает сессию,
удаляет только что созданные джобы и **пробрасывает исключение дальше**.
APScheduler логирует «Job raised an exception» и на этом всё: для
`date`-триггера job одноразовый, повторного запуска не будет. Любая
транзиентная ошибка (SQLite `database is locked` при совпадении с
минутными джобами, обрыв соединения с БД, неожиданный `None` в данных)
означает, что напоминание не сработает **до следующего рестарта**, когда
`reconcile_jobs_with_db` его подберёт. Retry-механизм
(`_schedule_send_retry`) покрывает только ошибки отправки в Telegram, не
ошибки кода/БД.

**Как исправить.**
1. В `execute_reminder_job` обернуть вызов:
   ```python
   try:
       await ctx.scheduler._execute_reminder(reminder_id, is_nagging_execution=...)
   except Exception:
       logger.exception("Reminder %s: unexpected failure, scheduling retry", reminder_id)
       ctx.scheduler.schedule_execution_retry(reminder_id, is_nagging_execution)
   ```
2. `schedule_execution_retry` — новый метод рядом с `_schedule_send_retry`:
   счётчик попыток хранить **в памяти процесса** (`dict[int, int]` на
   сервисе; после рестарта reconcile всё равно разберётся), задержки —
   те же `SEND_RETRY_BACKOFF_MINUTES`, id джоба — тот же `str(id)` /
   `nag_{id}`, `replace_existing=True`. После исчерпания — warning и
   выход (reconcile при рестарте).
3. В docstring зафиксировать компромисс: если ошибка случилась **после**
   отправки, но до коммита, ретрай отправит дубль. Это осознанно лучше,
   чем потерянное напоминание.

**Тест.** `tests/services/test_execute_reminder_job_retries_on_crash.py`:
замокать `_execute_reminder` так, чтобы он бросал `RuntimeError`; после
`await execute_reminder_job(42)` в `scheduler.get_job("42")` должен быть
джоб с `run_date ≈ now + 1 min`; после `len(SEND_RETRY_BACKOFF_MINUTES)+1`
попыток — джоба нет.

---

### 1.3 Подтверждение пользователю и job создаются до коммита

**Где:** `bot/handlers/reminders_shared.py::_save_and_show_edit`,
`bot/handlers/habits.py::state_habit_time`, `cb_fluid_habit_mode`,
`bot/handlers/reminders_snooze.py::callback_snooze_act`,
`bot/handlers/reminders_completion.py::callback_done_undo`, `callback_done_skip_next`.

**Что происходит.** Транзакция коммитится `DatabaseMiddleware` **после**
возврата хендлера. К этому моменту хендлер уже (а) записал job в
`jobs.sqlite` через `schedule_reminder` и (б) отправил «✅ задача создана».
Если `commit()` падает (SQLite locked дольше `busy_timeout`, ошибка диска,
любое исключение в `flush`), пользователь видит успех, строки нет, а
осиротевший job сработает и залогирует «Reminder N not found» (или его
уберёт часовой `remove_orphan_scheduler_jobs`). Ровно этот класс проблем
уже осознан для удаления (`callback_delete_task` делает явный
`session.commit()` до ответа) — нужно распространить паттерн.

**Как исправить.**
1. В `_save_and_show_edit` после успешного `schedule_reminder(...)`:
   ```python
   try:
       await reminder_dao.session.commit()
   except Exception:
       await reminder_dao.session.rollback()
       scheduler_service.remove_reminder_job(new_reminder.id)
       await source_message.answer(l10n.get("schedule_error", ...))
       await state.clear()
       return
   ```
   и только потом `state.clear()` + отправка превью.
2. То же в `state_habit_time` и `cb_fluid_habit_mode` (для fluid — джоба
   нет, только коммит перед `edit_text`).
3. В снузе/undo/skip-next: коммит сразу после `schedule_reminder`, до
   `edit_text`/`answer` (это же закрывает часть 2.2).
4. `DatabaseMiddleware` оставить как есть — повторный `commit()` пустой
   сессии безвреден.

**Тест.** `tests/services/test_save_and_show_edit_commits_before_reply.py`:
подменить `reminder_dao.session.commit` на `AsyncMock(side_effect=OperationalError)`;
ожидать, что `source_message.answer` получил `schedule_error`, а
`scheduler_service.remove_reminder_job` вызван с id созданного напоминания.
Второй тест: при нормальном коммите порядок вызовов — `commit` раньше
`answer` (через `MagicMock().mock_calls`).

---

### 1.4 Джобы на bound-методах чистят копию объекта (утечка не закрыта)

**Где:** `bot/__main__.py` — регистрация `cleanup_rate_limit_hits`
(`rate_limit_middleware.cleanup_expired`) и `cleanup_http_rate_limit_hits`
(`web_runner.app["http_rate_limiter"].cleanup_expired`).

**Что происходит.** `SQLAlchemyJobStore` сериализует job pickle'ом.
Для bound-метода APScheduler кладёт `func.__self__` в `args` (см.
`Job.__getstate__`), то есть **весь экземпляр `RateLimitMiddleware`
пиклится по значению** в момент `add_job`. При каждом срабатывании job
распикливается → `cleanup_expired()` вызывается на **свежей копии** и
чистит её, а живой `_hits` в middleware, через который идут апдейты, не
трогается никогда. Утечка, ради которой шаг 3.1 предыдущего плана
добавлял эти джобы, на самом деле не закрыта.

**Подтверждено** экспериментом на APScheduler 3.11.3: interval-job на
bound-методе через `SQLAlchemyJobStore` — метод дважды вызван на объектах с
другим `id()`, у живого объекта `calls == 0`, `_hits` не изменился.

**Как исправить** (выбрать один вариант; рекомендуется **A**, он же
закрывает 3.4):

**A. Отдельный `MemoryJobStore` для всех служебных джобов.**
```python
scheduler = AsyncIOScheduler(
    jobstores={
        "default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL),  # только напоминания
        "memory": MemoryJobStore(),                                  # всё остальное
    },
    job_defaults={...},
)
```
и во всех `add_job` служебных задач (heartbeat, cleanup_*, daily_briefs,
missed_recovery, habit_sweeper, habit_reports, delete_cleanup,
retention_cleanup, remove_orphan_scheduler_jobs) передать `jobstore="memory"`.
Они и так регистрируются с `replace_existing=True` на каждом старте, в
персистентности нет смысла, а без пиклинга bound-методы работают на
живых объектах. В `setup_*` функциях добавить параметр `jobstore="memory"`.
Побочный плюс: `jobs.sqlite` содержит только напоминания, и
`remove_orphan_scheduler_jobs` больше не перебирает служебные записи.

**B. Top-level функции + AppContext.** Добавить в `AppContext` поля
`rate_limiter` и `http_rate_limiter`, сделать `cleanup_rate_limit_hits_job()`
/ `cleanup_http_rate_limit_hits_job()` по образцу
`remove_orphan_scheduler_jobs_job`.

**Тест.** `tests/services/test_cleanup_jobs_act_on_live_instances.py`:
собрать scheduler как в `__main__` (с `MemoryJobStore` для `memory`),
зарегистрировать `cleanup_expired` живого `RateLimitMiddleware` с одним
просроченным хитом, дождаться срабатывания (`interval, seconds=0.2`),
проверить `middleware._hits == {}`. Аналогично для `HttpRateLimiter`.

---

### 1.5 Docker: healthcheck ничего не перезапускает, логи не ротируются

**Где:** `docker-compose.yml`.

**Что происходит.**
1. `restart: always` реагирует только на выход процесса. Статус `unhealthy`
   от `healthcheck` **никем не обрабатывается** — `docker compose` (в отличие
   от Swarm) не перезапускает нездоровые контейнеры. Вся конструкция с
   `HEARTBEAT_FILE` сейчас чисто информационная.
2. Драйвер логов по умолчанию `json-file` **без лимита**. Бот пишет
   INFO-строку на каждое сообщение пользователя и на каждый тик пяти
   минутных джобов («Starting hourly daily briefs check...» каждую минуту).
   За месяцы это гигабайты на VPS.

**Как исправить.**
1. Логи:
   ```yaml
   logging:
     driver: json-file
     options:
       max-size: "20m"
       max-file: "5"
   ```
2. Авто-перезапуск по healthcheck — самый простой вариант без кода:
   sidecar `willfarrell/autoheal` с `AUTOHEAL_CONTAINER_LABEL=autoheal`
   и меткой `autoheal=true` на сервисе `bot` (нужен `/var/run/docker.sock`).
   Альтернатива в коде — см. 3.2 (самозавершение при потере связи с Telegram).
3. Убрать «каждую минуту» INFO-шум: `logger.info("Starting hourly daily briefs check...")`
   в `daily_briefs.py` перевести в `debug`; то же для аналогичных стартовых
   строк других минутных джобов, если есть.
4. `stop_grace_period: 30s` (см. 3.3).

**Тест.** Инфраструктурный шаг, юнит-теста нет. Проверка руками:
`docker compose config` валиден; `docker inspect` показывает `LogConfig`.

---

## Фаза 2 — корректность в рантайме и UX-ошибки, выглядящие как поломка

### 2.1 «Обновить» без изменений → алерт «Что-то пошло не так»

**Где:** `bot/handlers/reminders_listing.py::callback_tasks_page`,
`_show_filtered_tasks`, `callback_tasks_filter_by_tag`,
`callback_tasks_tags_menu`; `bot/handlers/habits.py::cb_habit_back_dash`;
`bot/handlers/settings.py` — все `edit_text` в callback'ах меню настроек.

**Что происходит.** Кнопка «Обновить» в списке задач — `tasks_page_{page}`
текущей страницы. Если список не изменился, Telegram отвечает
`TelegramBadRequest: message is not modified`. `edit_text` не обёрнут в
`try`, исключение уходит в `handle_dispatcher_error`, пользователь видит
`show_alert` «❌ Что-то пошло не так». То же при повторном нажатии любой
кнопки-фильтра/«назад», если текст совпал.

**Как исправить.**
1. В `bot/utils/telegram.py` (новый модуль) — хелпер:
   ```python
   async def safe_edit_text(message, text, **kwargs) -> bool:
       try:
           await message.edit_text(text, **kwargs)
           return True
       except TelegramBadRequest as e:
           if "message is not modified" in str(e):
               return False
           raise
   ```
   и аналогичный `safe_edit_reply_markup`.
2. Заменить прямые `callback.message.edit_text(...)` в перечисленных
   хендлерах на хелпер; после него всегда `await callback.answer()`.
3. Не глотать другие `TelegramBadRequest` — только «not modified».

**Тест.** `tests/services/test_refresh_not_modified.py`: `callback.message.edit_text`
= `AsyncMock(side_effect=TelegramBadRequest(method=..., message="Bad Request: message is not modified"))`;
`callback_tasks_page` должен завершиться без исключения и вызвать `callback.answer()`.

---

### 2.2 Правка сообщения после бизнес-изменения откатывает транзакцию

**Где:** `bot/handlers/reminders_snooze.py::callback_snooze_act` (`edit_text`
с MarkdownV2 без `try`; `callback.message.text` может быть `None`),
`bot/handlers/reminders_listing.py::callback_undo_delete`,
`callback_recovery_done_all`, `callback_recovery_snooze_all`,
`bot/handlers/reminders_repeat.py::callback_edit_nagging` и соседи,
`bot/handlers/habits.py::cb_fluid_habit_mode` (после `flush`).

**Что происходит.** Порядок в хендлере: изменить ORM-объект → переставить
job в планировщике → `edit_text`. Если `edit_text` падает (сообщение
старше 48 часов — «message can't be edited», удалено пользователем,
`text is None` у медиа), исключение доходит до `DatabaseMiddleware`, который
делает `rollback()`. В итоге: job в `jobs.sqlite` уже на новом времени, а
`execution_time` в БД — старое; пользователь видит ошибку и повторяет
действие. Напоминания, отложенные на «завтра», как раз часто оказываются
старше 48 часов к моменту снуза.

**Как исправить.**
1. Единый порядок для всех мутирующих callback'ов: изменить состояние →
   `schedule_*` → **`await session.commit()`** → UI (`edit_text`/`answer`)
   в `try/except TelegramBadRequest` с логом на `warning`.
2. В `callback_snooze_act`: `original = callback.message.text or callback.message.caption or ""`.
3. При `TelegramBadRequest` на UI-шаге всё равно вызвать `callback.answer(toast)`
   — действие выполнено.

**Тест.** `tests/services/test_snooze_survives_edit_failure.py`: `edit_text`
бросает `TelegramBadRequest("message can't be edited")`; после хендлера
`reminder.execution_time` == новое время, `session.commit` вызван,
исключение не пробросилось, `callback.answer` вызван с `snoozed_toast`.

---

### 2.3 Middleware на `dp.update` никогда не отправляют свои ответы

**Где:** `bot/middlewares/rate_limit.py`, `bot/middlewares/database.py`;
регистрация в `bot/__main__.py` через `dp.update.middleware(...)`.

**Что происходит.** Middleware, зарегистрированный на observer `update`,
получает объект `aiogram.types.Update`, а не `Message`/`CallbackQuery`.
В `RateLimitMiddleware` проверки `isinstance(event, Message)` /
`isinstance(event, CallbackQuery)` всегда ложны → пользователь, попавший под
лимит, не получает «⏳ Too many messages», его сообщения молча
исчезают. В `DatabaseMiddleware` `hasattr(event, "answer")` всегда `False`
(у `Update` нет `answer`, проверено) → «❌ Database error» не отправляется.
Сама логика лимита и отката работает — ломается только обратная связь.

**Как исправить.**
1. Хелпер в `bot/middlewares/__init__.py`:
   ```python
   def inner_event(update: Update) -> Message | CallbackQuery | None:
       return update.message or update.callback_query
   ```
2. В обоих middleware брать `target = inner_event(event)` и слать ответ
   через `target.answer(...)` (для `CallbackQuery` — с `show_alert=False`).
3. Тексты уведомлений rate-limit вынести в лексикон
   (`rate_limited_message`, `rate_limited_callback`) — сейчас там
   хардкод на английском.

**Тест.** `tests/services/test_rate_limit_notifies_user.py`: 21 раз
прогнать `Update(message=...)` через middleware, у `message.answer`
ровно один вызов.

---

### 2.4 Глобальный обработчик ошибок игнорирует язык пользователя

**Где:** `bot/__main__.py::handle_dispatcher_error`.

**Что происходит.** `get_l10n(None)` → всегда `ru`. Англо- и
испаноязычные пользователи при любой внутренней ошибке получают алерт на
русском.

**Как исправить.** aiogram передаёт в error-handler те же `**data`, что
дошли до хендлера, поэтому:
```python
async def handle_dispatcher_error(event: ErrorEvent, l10n: dict | None = None) -> None:
    l10n = l10n or get_l10n(None)
```
Если исключение случилось до `DatabaseMiddleware` (в rate-limit), `l10n`
не будет — fallback остаётся.

**Тест.** Вызвать обработчик с `l10n=get_l10n("en")` и убедиться, что
`callback_query.answer` получил английскую строку `generic_error`.

---

### 2.5 Неизвестные команды становятся задачами

**Где:** `bot/handlers/reminders_wizard.py::handle_task_text`.

**Что происходит.** `/settings`, `/stats`, `/старт` и любой другой
незарегистрированный слэш-текст попадает в catch-all и уходит в парсер —
создаётся задача «/settings» с запросом времени.

**Как исправить.** Перед catch-all, в том же роутере:
```python
@router.message(StateFilter(None), F.text.regexp(r"^/\w+"))
async def handle_unknown_command(message: Message, l10n: dict[str, Any]) -> None:
    await message.answer(l10n["unknown_command"])
```
Ключ `unknown_command` — в три лексикона (текст со ссылкой на `/help`).
Учитывать, что `Command`-хендлеры живут в роутерах раньше `reminders`, так
что известные команды сюда не дойдут.

**Тест.** `tests/services/test_unknown_command_not_a_task.py`: сообщение
`/whatever` → `parser.parse` не вызван, `answer` получил `unknown_command`.

---

### 2.6 Бот работает в группах как в личке

**Где:** все роутеры в `bot/handlers/`; `bot/__main__.py`.

**Что происходит.** Ни один хендлер не фильтрует `chat.type`. Добавленный в
группу бот (или бот с выключенным privacy mode) создаёт напоминание из
любого текста, а уведомление позже шлёт в `chat_id=user_id`. Если этот
участник никогда не писал боту в личку — `TelegramForbiddenError` три
раза подряд и напоминание отключается; в группе при этом висит
интерфейс с кнопками, которые нажимают чужие люди (кнопки проходят
`get_owned` и просто отвечают «не найдено»).

**Как исправить.** Outer-middleware на `dp.update` (первым, до rate-limit):
если `inner_event(update).chat.type != "private"` — ответить один раз
`l10n["private_only"]` (ключ ×3) и вернуть `None`. Для `my_chat_member` в
группах — просто игнорировать. Опционально: в `BotFather` включить
privacy mode (задокументировать в `DEPLOYMENT.md`).

**Тест.** `Update` с `message.chat.type == "supergroup"` не доходит до
хендлера (`handler` mock не вызван), `answer` вызван с `private_only`.

---

### 2.7 Блокировка бота пользователем не обрабатывается

**Где:** новый хендлер в `bot/handlers/commands.py`; `bot/database/models.py`;
кандидаты в `daily_briefs.get_users_needing_brief_check`,
`missed_recovery`, `habit_sweeper`, `habit_reports`,
`scheduler.reconcile_jobs_with_db`.

**Что происходит.** При блокировке Telegram шлёт `my_chat_member` с
`new_chat_member.status == "kicked"`. Сейчас это никто не слушает:
напоминания продолжают уходить до 3 `Forbidden`-страйков **на каждое
напоминание**, а брифы/дайджесты/отчёты для заблокировавшего
пользователя проверяются и «отправляются» каждый день бесконечно
(`_send_safe` считает `Forbidden` финальным успехом, но кандидат
остаётся в выборке навсегда).

**Как исправить.**
1. Колонка `User.bot_blocked_at: DateTime | None` (мягкая миграция
   подхватит автоматически через `Base.metadata`).
2. Хендлер:
   ```python
   @router.my_chat_member()
   async def on_my_chat_member(event: ChatMemberUpdated, user_dao: UserDAO, user: User) -> None:
       if event.chat.type != "private": return
       blocked = event.new_chat_member.status == "kicked"
       await user_dao.update_settings(user.id, bot_blocked_at=_utcnow_naive() if blocked else None)
   ```
3. Во всех выборках кандидатов минутных джобов и в `reconcile_jobs_with_db`
   добавить `User.bot_blocked_at.is_(None)` (для reminders — через join).
4. В `_send_telegram_message` при `Forbidden` дополнительно ставить
   `bot_blocked_at`, если ещё не стоит (страховка на случай пропущенного апдейта).
5. При разблокировке (`status == "member"`) — сброс поля; reconcile при
   следующем старте (или ручной вызов) вернёт джобы.

**Тест.** Апдейт `my_chat_member` со статусом `kicked` → у пользователя
`bot_blocked_at` не `None`; `get_users_needing_brief_check` такого
пользователя не возвращает.

---

### 2.8 Шторм отправок после долгого простоя и игнорирование `retry_after`

**Где:** `bot/services/scheduler.py::reconcile_jobs_with_db`,
`_send_telegram_message`, `_schedule_send_retry`.

**Что происходит.** После простоя `reconcile_jobs_with_db` ставит **все**
недоставленные напоминания на `now + 1 min`. При сотнях пользователей это
сотни `sendMessage` в одну минуту → `TelegramRetryAfter`. Он ловится как
generic `Exception` → `retryable_error` → ретрай через фиксированную 1
минуту, игнорируя `e.retry_after`, и всё повторяется.

**Как исправить.**
1. В reconcile: `run_at_utc = now_utc + timedelta(minutes=1, seconds=2 * restored)`
   — линейное размазывание (≈30 отправок/мин).
2. В `_send_telegram_message` отдельная ветка
   `except TelegramRetryAfter as e: return None, ("retryable_error", e.retry_after)`
   (или третий элемент кортежа), а `_schedule_send_retry` принимает
   `min_delay_seconds` и берёт `max(backoff, retry_after + 1)`.
3. То же в `daily_briefs._send_safe` и родственниках: `RetryAfter` →
   `False` (ретрай на следующем тике уже есть), но задержать цикл
   `await asyncio.sleep(e.retry_after)` перед следующим пользователем,
   чтобы не долбить API.

**Тест.** `reconcile` с 10 просроченными one-off: `run_date` у джобов
строго возрастают; `_send_telegram_message` с `TelegramRetryAfter(retry_after=30)`
→ retry-job на `now + ≥30 s`.

---

## Фаза 3 — инфраструктура, наблюдаемость, гигиена

### 3.1 HTTP rate-limiter за reverse-proxy лимитирует всех как одного

**Где:** `bot/services/webserver.py::_http_rate_limit_middleware`.

`request.remote` за nginx/Caddy — всегда IP прокси, значит 30 запросов на
10 с делят все пользователи `.ics`-фида и Mini App. Добавить
`config.TRUSTED_PROXY: bool = False`; при `True` брать первый адрес из
`X-Forwarded-For`, иначе `request.remote`. Задокументировать в `DEPLOYMENT.md`.
Тест: при `TRUSTED_PROXY=True` два запроса с разными `X-Forwarded-For`
не делят лимит.

### 3.2 Heartbeat не отражает связь с Telegram

**Где:** `bot/__main__.py::_write_heartbeat`.

Файл пишет APScheduler-джоб, поэтому «жив планировщик» ≠ «жив polling».
Сделать job асинхронным: `await asyncio.wait_for(bot.get_me(), timeout=10)`
и писать файл только при успехе; три подряд неудачи → `logger.critical` +
`stop_event.set()` с ненулевым кодом выхода (в связке с 1.1 это даст
честный рестарт через `restart: always`, даже без autoheal из 1.5).
Тест: `bot.get_me` бросает `TelegramNetworkError` — файл не обновляется.

### 3.3 `stop_grace_period`

**Где:** `docker-compose.yml`. Добавить `stop_grace_period: 30s`, чтобы
деплой посреди рассылки брифов давал циклу по пользователям доработать
(после 1.1 SIGTERM обрабатывается корректно). Без теста.

### 3.4 Sync-jobstore и лишние обращения

**Где:** `bot/services/scheduler.py::reconcile_jobs_with_db`,
`remove_orphan_scheduler_jobs`; `bot/__main__.py`.

`SQLAlchemyJobStore` синхронный — каждый `get_job`/`add_job` блокирует
event loop на SQLite-запрос. `reconcile` делает `get_job` на **каждое**
pending-напоминание. Заменить на один `existing = {j.id for j in self.scheduler.get_jobs()}`
до цикла. После 1.4 (вариант A) в `default`-jobstore останутся только
напоминания, и `remove_orphan_scheduler_jobs` перестанет перебирать
служебные записи. Тест: замокать `scheduler.get_job` и убедиться, что он
не вызывается в reconcile.

### 3.5 Мелкая гигиена состояния

- `bot/services/fsm_storage.py`: в `set_state(None)` и `set_data({})`
  удалять строку, если после операции `state is None and data_json == "{}"`
  — таблица `fsm_state` перестанет держать по строке на каждого
  пользователя навсегда (сейчас её чистит только 24-часовой cleanup).
- `bot/handlers/reminders_snooze.py::callback_snooze_act`:
  `callback.message.text` → `(callback.message.text or callback.message.caption or "")`
  (входит в 2.2, но не забыть).
- `bot/handlers/reminders_listing.py::callback_recovery_done_all`: при
  ошибке планировщика на N-й задаче цикл уже удалил джобы у первых N-1;
  после `rollback()` вызвать `_reschedule_current_execution` для них или
  просто перенести `remove_*_job` **после** успешного коммита всей пачки.

### 3.6 Документация и предупреждения

- `README.md` / `README.es.md`: «135 tests» → актуальное число (сейчас 442);
  тесты лежат в `tests/`, а не «рядом с кодом в `bot/services/`».
- `bot/services/webserver.py`: `app["session_pool"]` и т. п. → `web.AppKey`
  (убирает `NotAppKeyWarning` в тестах, поведение не меняется).
- `REWORK_PLAN_5.md` можно пометить как выполненный (все 4 фазы в истории
  коммитов) или удалить, чтобы агент не пытался выполнять его повторно.

---

## Что проверено и признано корректным (не трогать)

Чтобы агент не «чинил» работающее:

- DST-безопасная арифметика (`next_occurrence_utc`, `_next_quiet_end_utc`,
  `local_time_*`) — корректна, покрыта тестами.
- Атомарные claim'ы слотов брифов/дайджестов/отчётов через условный `UPDATE`
  и их release при retryable-ошибке — корректны.
- `HabitEventDAO.record` с `begin_nested()` — корректно.
- `forbidden_strikes`, `send_retry_count`, `last_fired_at` и логика
  «уже доставлено» в `reconcile_jobs_with_db` — корректны.
- `mark_done` для recurring с уже сдвинутым `execution_time` — корректен.
- `TelegramNetworkError` — подкласс `TelegramAPIError`, поэтому
  `_set_bot_commands` при недоступном Telegram **не** роняет процесс (проверено).
- `PRAGMA journal_mode=WAL`, `busy_timeout`, `foreign_keys` — на месте;
  `scripts/backup.sh` использует online backup API, что правильно для WAL.
- Индексы `habit_events(user_id, local_date)`, `habit_events(reminder_id)`,
  `reminders(user_id/status/execution_time)` — есть.
- Лексиконы `ru/en/es` содержат одинаковые 330 ключей.
