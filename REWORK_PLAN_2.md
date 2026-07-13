# План доработки Porabot, итерация 2 (для исполнителя: Sonnet)

Результат повторного код-ревью после выполнения REWORK_PLAN.md (23 коммита,
ветка `claude/rework-plan-ilnq1p`). Часть находок — недоделки/побочные эффекты
первой итерации, часть — старые баги, не попавшие в первый план.

## Правила выполнения

1. Выполняй фазы по порядку: Фаза 1 (высокие), потом 2, 3.
2. Один шаг = один атомарный коммит (`fix: ...`, `refactor: ...`).
3. Окружение: `python3 -m venv .venv && pip install "setuptools<81" wheel && pip install -r requirements.lock pytest pytest-asyncio`.
4. После каждого шага: `python -m pytest -q` — все 68 существующих тестов зелёные.
5. Для каждого бага Фаз 1–2 — регрессионный тест в `bot/services/test_*.py`.
6. Инварианты не трогать: naive-UTC в БД, `completed_for_execution_time`,
   anti-drift снуза привычек, ключи лексикона добавлять сразу в ru/en/es
   (`test_l10n_key_coverage.py` это проверяет).

---

## Фаза 1 — Высокие

### Шаг 1.1. Reconcile повторно рассылает старые неподтверждённые напоминания при каждом рестарте

**Проблема.** `reconcile_jobs_with_db` (`bot/services/scheduler.py:102-150`)
пересоздаёт job для КАЖДОГО pending-реминдера без job в jobstore. Но one-off
реминдер после срабатывания остаётся `status == "pending"` навсегда, пока
пользователь не нажмёт Done (`mark_done` — единственный путь в completed), а
его date-job APScheduler удаляет после исполнения. Итог: все когда-либо
сработавшие, но не закрытые пользователем one-off реминдеры получают
«догоняющий» job `now + 1 минута` при КАЖДОМ рестарте бота — пользователь
раз за разом получает дубли старых уведомлений. Вдобавок reconcile не
фильтрует `forbidden_strikes` — воскрешает напоминания, от перепланирования
которых бот уже отказался после блокировки (обесценивает фикс
FORBIDDEN_STRIKES_LIMIT).

**Что сделать.**
1. Добавить в `Reminder` колонку `last_fired_at: Optional[datetime]`
   (naive UTC) + soft-миграцию `("last_fired_at", "DATETIME")` в
   `bot/database/engine.py`.
2. В `_execute_reminder` для не-nagging исполнений проставлять
   `reminder.last_fired_at = now_utc_naive` (до commit; независимо от успеха
   отправки — за blocked отвечает forbidden_strikes; ветка quiet-hours
   deferral возвращается раньше и не должна его ставить).
3. В запрос reconcile добавить фильтр
   `Reminder.forbidden_strikes < FORBIDDEN_STRIKES_LIMIT`.
4. Для просроченного НЕрекуррентного реминдера создавать catch-up job только
   если `last_fired_at is None or last_fired_at < execution_time` (этот цикл
   ещё ни разу не исполнялся); иначе пропускать — уведомление уже доставлено
   и ждёт действий пользователя.

**Проверка.** Тесты: (a) one-off с прошедшим `execution_time` и
`last_fired_at >= execution_time` → reconcile job НЕ создаёт; (b) с
`last_fired_at is None` → создаёт catch-up; (c) `forbidden_strikes == limit` →
пропущен; (d) `_execute_reminder` проставляет `last_fired_at`.

### Шаг 1.2. Повторное срабатывание после снуза портит habit_active_due_at (стрик уходит не в тот цикл)

**Проблема.** В `_execute_reminder` (`bot/services/scheduler.py`, habit-блок
~238-259) на каждом не-nagging исполнении безусловно выполняется
`reminder.habit_active_due_at = reminder.execution_time`. Сценарий: привычка
срабатывает в 09:00 → `active_due = сегодня 09:00`, `execution_time`
продвигается на завтра. Пользователь жмёт снуз +2ч (anti-drift сохраняет
`execution_time = завтра`). В 11:00 снуз-job срабатывает как обычное
исполнение → `active_due` перезаписывается на ЗАВТРА, клавиатура встраивает
`cycle_due_ts` завтрашнего цикла. Done в 11:05 засчитывает стрик за
ЗАВТРАШНИЙ день (`habit_last_completed_due_at = завтра`), а завтрашнее
реальное Done упирается в `already_counted`. Затронуты все пути refire:
пресет-снуз, кастомный снуз, `recovery_snooze_all`.

**Что сделать.** Перезаписывать `habit_active_due_at` только когда текущее
исполнение соответствует хранимому циклу — `execution_time` ещё не продвинут
в будущее:
```python
current_due = to_utc_naive(reminder.execution_time)
if current_due <= now_utc_naive + timedelta(minutes=5):
    reminder.habit_active_due_at = current_due
# иначе (снуз-refire уже продвинутого цикла) — оставить прежний active_due
```
Проверить, что стрик-reset (`prev_due + 24h`) в той же ветке продолжает
работать: он выполняется до перезаписи и от неё не зависит.

**Проверка.** Тест: fire (active_due=D0, exec→D1) → снуз-refire →
`habit_active_due_at == D0`; `apply_habit_streak_completion(due=D0)`
считается; повторный Done за D1 на следующий день НЕ `already_counted`.

### Шаг 1.3. Кнопки главного меню перехватываются FSM-состояниями других роутеров

**Проблема.** Порядок роутеров: admin, commands, settings, habits, reminders
(`bot/handlers/__init__.py`). Хендлеры кнопок меню разбросаны по роутерам:
«Новая задача»/«Мои задачи» в reminders (последний), «Настройки» в settings,
«Привычки» в habits. FSM-хендлеры без текст-фильтров стоят в более ранних
роутерах и съедают меню-тексты:
- state `SettingsState.waiting_for_brief_time` (`settings.py:326`) + тап
  «📅 Мои задачи» → текст уходит в валидатор времени → «неверный формат»;
- state `HabitState.waiting_for_name` (`habits.py:269`) + тап «📅 My Tasks» →
  создаётся привычка с именем «📅 My Tasks»;
- аналогично `waiting_for_fluid_name`, `waiting_for_time`,
  `waiting_for_quiet_time`, `waiting_for_timezone` для кнопок из более
  поздних роутеров.

**Что сделать.** Создать `bot/handlers/menu.py` с router'ом, содержащим
ЧЕТЫРЕ хендлера кнопок меню (перенести `btn_new_task`, `btn_my_tasks` из
reminders, `btn_settings` из settings, `btn_habits` из habits — каждый
начинает с `state.clear()`, у `btn_new_task` добавить его). Зарегистрировать
в `all_routers` сразу после `commands_router`, до settings/habits/reminders.
`_MENU_TEXTS` в reminders оставить (нужен catch-all-guard'у) или
экспортировать из menu.py.

**Проверка.** Тест: state = `waiting_for_brief_time`, текст «📅 My Tasks» —
диспетчеризация по порядку роутеров разрешается в menu-роутер (проверить
фильтры хендлеров, как в `test_forwarded_task_state_filter.py`); state =
`waiting_for_name` + «⚙️ Settings» → привычка не создаётся.

### Шаг 1.4. Fluid-привычки невидимы в списке привычек — их нельзя удалить

**Проблема.** `cb_habit_list` (`bot/handlers/habits.py:339`) берёт задачи из
`get_user_reminders`, который фильтрует `is_fluid_habit.is_(False)`
(`bot/database/dao/reminder.py`, get_user_reminders). Весь рендер-код для
fluid-веток (`h.is_fluid_habit`, `_fluid_mode_label`, «anytime») мёртв:
fluid-привычка не отображается в «Мои привычки» и не имеет кнопки удаления.
Пользователь навсегда получает утренние/вечерние fluid-промпты — избавиться
можно только через «Clear all».

**Что сделать.** В `cb_habit_list` собирать список из двух источников:
`[r for r in await get_user_reminders(...) if _is_habit_entry(r)]` +
`await reminder_dao.get_active_fluid_habits(user.id)` (дедуп по id не нужен —
множества не пересекаются из-за фильтра). Убедиться, что `del_habit_{id}`
для fluid работает (уже работает: `get_owned` + `delete_by_id` +
`remove_reminder_job`).

**Проверка.** Тест: у пользователя fixed-привычка и fluid-привычка → в
списке обе, для обеих сгенерированы кнопки `del_habit_`.

---

## Фаза 2 — Средние

### Шаг 2.1. Утренний бриф без верхней границы окна — «доброе утро» в 23:30

**Проблема.** Оконная логика из первой итерации
(`bot/services/daily_briefs.py:133-142`): `morning_due = local >= morning_time
and last_morning != today` — без верхней границы. Если бот пролежал день (или
пользователь включил briefs вечером), в 23:30 уходит устаревший утренний бриф,
а следующей минутой — вечерний.

**Что сделать.** `morning_due` дополнительно требует
`local_time_str < evening_brief_time`. Если `local >= evening_brief_time`, а
утренний за сегодня не отправлялся — просто проставить
`last_morning_brief_date = today_str` без отправки (подавить устаревший бриф).

**Проверка.** Тест: 23:30, оба брифа не отправлены → отправлен только
вечерний, `last_morning_brief_date` помечен.

### Шаг 2.2. Reconcile вычисляет next occurrence в UTC, минуя DST-хелпер

**Проблема.** `reconcile_jobs_with_db` (`scheduler.py:128-141`) продвигает
просроченные рекуррентные через `rrulestr(...).after(now_utc)` с UTC-dtstart —
в обход `next_occurrence_utc`, введённого в первой итерации именно для
устранения DST-дрейфа. Рестарт бота через переход на летнее время сдвинет
локальное время ежедневных напоминаний на час.

**Что сделать.** Загрузить в reconcile таймзоны пользователей одним запросом
(`select(User.id, User.timezone)` → dict) и заменить прямой rrule-вызов на
`next_occurrence_utc(reminder.rrule_string, reminder.execution_time,
user_tz_map.get(reminder.user_id, "UTC"), now_utc_naive)`.

**Проверка.** Тест как `test_dst_recurring_reminder.py`: reconcile
просроченной daily-привычки пользователя Europe/Berlin через дату перехода —
локальный час не меняется.

### Шаг 2.3. Создание привычки не отклоняет прошедшее время

**Проблема.** `state_habit_time` (`bot/handlers/habits.py:277-330`) сохраняет
`result.parsed_datetime` без проверки на будущее (в отличие от
`_save_and_show_edit` после шага 2.6 первой итерации). Если парсер вернул
прошедшее время, job либо мгновенно срабатывает (в пределах
misfire_grace_time = 1ч), либо отбрасывается — и до рестарта привычка не
работает вовсе.

**Что сделать.** После `to_utc_naive`: если `execution_time_utc <= now`,
продвинуть на следующее вхождение —
`next_occurrence_utc("FREQ=DAILY", execution_time_utc, user.timezone, now)`
(fallback: `+1 день`). Привычка всегда стартует с ближайшего будущего слота.

**Проверка.** Тест: парсер вернул время в прошлом → созданная привычка имеет
`execution_time` в будущем, job запланирован на будущее.

### Шаг 2.4. Список задач без пагинации — большие списки вообще не отображаются

**Проблема.** `btn_my_tasks` / `callback_refresh_tasks`
(`bot/handlers/reminders.py:299-312, 646-660`) рендерят ВСЕ pending-задачи:
строка на задачу + 3 inline-кнопки на задачу (`get_tasks_list_keyboard`).
При ~30+ задачах — лимит Telegram на кнопки (~100) или 4096 символов текста →
`TelegramBadRequest`, список не показывается совсем.

**Что сделать.** Константа `_TASKS_PAGE_LIMIT = 25`; показывать первые 25 +
строка `l10n["tasks_more"]` («…и ещё {count}», новый ключ в ru/en/es);
клавиатуру строить только для показанных.

**Проверка.** Тест: 60 задач → 25 строк + суффикс «…и ещё 35», в клавиатуре
≤ 25×3 + 3 кнопки.

### Шаг 2.5. forbidden_strikes сбрасывается любой не-forbidden ошибкой

**Проблема.** `_execute_reminder`:
`reminder.forbidden_strikes = +1 if forbidden else 0`, а
`_send_telegram_message` возвращает `(None, False)` и для сетевых/прочих
ошибок. Блокировка → strike; случайный сетевой сбой на следующем цикле →
счётчик обнулён. Пользователь, заблокировавший бота, при периодических
сетевых ошибках не отсекается никогда.

**Что сделать.** `_send_telegram_message` возвращает исход тремя состояниями
(например `Message | None` + `outcome: Literal["ok","forbidden","error"]`,
или кортеж `(message, forbidden)` где forbidden=True/False плюс отдельный
признак успеха). Логика: forbidden → `strikes + 1`; успешная отправка →
`strikes = 0`; прочие ошибки → не менять. Прокинуть через
`_send_or_replace_nag_message`.

**Проверка.** Дополнить `test_forbidden_strikes_stop_rescheduling.py`:
generic-ошибка отправки при `strikes=2` → счётчик остаётся 2.

---

## Фаза 3 — Низкие / гигиена

### Шаг 3.1. `pickle_protocol` передан не в тот конструктор

`bot/__main__.py:57-61`: `AsyncIOScheduler(pickle_protocol=...)` — APScheduler
молча игнорирует неизвестный параметр (проверено); он принадлежит
`SQLAlchemyJobStore`. Перенести:
`SQLAlchemyJobStore(url=..., pickle_protocol=pickle.HIGHEST_PROTOCOL)` — или
просто удалить (дефолт jobstore уже 5 == HIGHEST на py3.11), убрав и
`import pickle`.

### Шаг 3.2. `get_or_create` не обновляет username

`bot/database/dao/user.py:13-29`: у существующего пользователя сменившийся
Telegram-username никогда не обновляется. Добавить:
`if user.username != username: user.username = username; flush()`.

### Шаг 3.3. Смена таймзоны не пересчитывает существующие напоминания

`settings.py:166-227`: после смены TZ хранённые UTC-времена остаются
привязаны к старой зоне: one-off сработают «по-старому», рекуррентные
переедут на новый локальный час только после следующего срабатывания
(next_occurrence_utc уже считает в новой зоне). Минимум: добавить в
`tz_success` (ru/en/es) предупреждение, что времена существующих задач
остаются прежними в абсолютном времени. Полный пересчёт — вне скоупа.

### Шаг 3.4. Мусор в корне репозитория

Удалить: `pytest_output.txt` (331 КБ), `audit_log.txt`,
`test_parser_debug.py` (одноразовый отладочный скрипт, дублирует
test_parser.py). Один коммит `chore:`.

### Шаг 3.5. Повышение nag-лимита не возобновляет остановленную цепочку

`reminders.py, state_nag_limit`: если лимит был исчерпан (nag-job снят) и
пользователь поднял его, follow-up'ы не возобновятся до следующего основного
срабатывания. Либо задокументировать в docstring, либо: если
`is_nagging and sent < new_limit and status != "completed" and` реминдер
просрочен — пересоздать nag-job на `now + NAGGING_INTERVAL_MINUTES`.
Выбрать второе, если реализация не потянет побочных эффектов; иначе — первое.

---

## Что НЕ трогать

- Инвариант naive-UTC в БД, `to_utc_aware`/`to_utc_naive`.
- Механику `completed_for_execution_time` и anti-drift снуза привычек
  (шаг 1.2 меняет только условие перезаписи `habit_active_due_at`).
- IDOR-защиту (`get_owned`) и экранирование Markdown из первой итерации.
- Структуру лексиконов — новые ключи сразу в ru/en/es.

## Порядок сдачи

После каждой фазы: `python -m pytest -q` — зелёный прогон, один push в
`claude/rework-plan-ilnq1p` (или ветку, указанную оператором). Коммиты
небольшие, по шагу на коммит. Если шаг ломает существующий тест — остановиться
и описать конфликт, а не подгонять тест.
