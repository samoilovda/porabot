# План: кнопка «Not today», учёт Done/Not today и недельные/месячные итоги привычек

Документ — задание для реализации. Читать целиком до первого редактирования: решения в
разделе «Принятые решения» уже согласованы с автором продукта и не подлежат пересмотру
по ходу работы.

---

## 0. Контекст: как устроены привычки сейчас

Привычки — это строки в таблице `reminders`, а не отдельная сущность. Есть два вида:

**Fixed-привычки** (`bot/handlers/habits.py`, `state_habit_time`): `is_habit=True`,
`is_recurring=True`, `rrule_string="FREQ=DAILY"`, `is_nagging=True`. Живут по циклам:
`execution_time` (UTC naive) — время следующего срабатывания, `habit_active_due_at` —
due текущего активного цикла, `habit_last_completed_due_at` — due последнего засчитанного.
Стрик считает `ReminderDAO.apply_habit_streak_completion`: выполнение засчитывается,
только если пришло не позже чем через 24 часа после due; иначе `habit_streak_current`
обнуляется. Для FREQ=DAILY «due + 24 часа» совпадает со следующим плановым оповещением —
это важно для раздела 3.

**Fluid-привычки** (`is_fluid_habit=True`): без фиксированного времени, учёт по локальным
датам в `fluid_last_completed_date` (строка `YYYY-MM-DD`), стрик в `fluid_streak_current` /
`fluid_streak_best`. `execution_time` у них — заглушка на +5 лет.

Что существенно для задачи:

- **Событий выполнения нигде не логируется.** Есть только счётчики-стрики в строке
  `reminders`. Из них невозможно получить ни «5 из 7 за неделю», ни месячный срез, ни
  откатить отметку. Поэтому раздел 1 вводит таблицу событий — это фундамент всей фичи.
- **Миграции — «мягкие»**, через `ALTER TABLE ... ADD COLUMN` в `bot/database/engine.py`
  (`init_db`), обёрнутые в `try/except OperationalError`. Alembic в проекте нет.
- **Кнопка «Not done» в вечернем wrap-up уже существует** (`get_evening_wrapup_keyboard`,
  хендлер `callback_wrapup_not_done` в `bot/handlers/reminders.py:945`) и сейчас чисто
  косметическая — она только перерисовывает ряд клавиатуры и ничего не пишет в БД.
- **Привычки удаляются жёстко**: `cb_del_habit` → `ReminderDAO.delete_by_id`. Каскадов в
  схеме нет.
- **Фоновые джобы** регистрируются в `bot/__main__.py` через `setup_*(scheduler)` и ходят
  в БД через `bot.services.scheduler._instance` (module-level синглтон). Образец —
  `bot/services/daily_briefs.py`: минутный cron, который для каждого пользователя сравнивает
  локальное `%H:%M` с настройкой.

---

## Принятые решения

Эти четыре развилки уже закрыты — реализуй как написано.

1. **«Not today» обнуляет стрик**, ровно как автопропуск. `habit_streak_current` → 0
   (для fluid — `fluid_streak_current` → 0). Стрик означает «сделано без перерывов».
2. **Месячный отчёт — отдельным сообщением сразу после недельного**, в то же
   воскресенье. Период месячного: **с 1-го числа по день отправки включительно**. Хвост
   месяца после последнего воскресенья попадёт в следующий месячный отчёт — это
   осознанный компромисс, зафиксируй его комментарием в коде отчёта.
3. **День fluid-привычки закрывается в локальную полночь** отдельным свипером, а не в
   вечернем брифе.
4. **Кнопка «Not done» в вечернем wrap-up начинает писать событие `not_today`** — но
   только для привычек. Для обычных задач она остаётся косметической.

---

## 1. Таблица событий `habit_events`

Новая модель в `bot/database/models.py`. Одна строка = один разрешённый цикл привычки.

| Поле | Тип | Смысл |
|---|---|---|
| `id` | INTEGER PK autoincrement | |
| `user_id` | BigInteger, FK `users.id`, not null | владелец |
| `reminder_id` | Integer, FK `reminders.id`, not null | привычка |
| `habit_text` | String, not null | снимок названия на момент события |
| `cycle_key` | String, not null | ключ цикла, см. ниже |
| `local_date` | String, not null | `YYYY-MM-DD` в таймзоне юзера — бакет для отчётов |
| `due_at` | DateTime, nullable | due цикла (UTC naive); `NULL` у fluid |
| `outcome` | String, not null | `done` \| `not_today` \| `missed` |
| `source` | String, not null | `button` \| `wrapup` \| `auto` |
| `created_at` | DateTime, default `func.now()` | |

**`cycle_key`** — то, что делает запись идемпотентной:
- fixed: `f"due:{int(due_at.replace(tzinfo=timezone.utc).timestamp())}"`
- fluid: `f"day:{local_date}"`

Ограничения и индексы в `__table_args__`:
- `UniqueConstraint("reminder_id", "cycle_key", name="uq_habit_events_cycle")`
- `Index("idx_habit_events_user_date", "user_id", "local_date")` — основной запрос отчётов
- `Index("idx_habit_events_reminder", "reminder_id")` — для удаления вместе с привычкой

**Почему `habit_text` дублируется, а не берётся джойном:** привычки удаляются жёстко, а
отчёт не должен падать на висящем FK и не должен задним числом переписывать историю при
переименовании привычки.

**Почему `outcome` различает `not_today` и `missed`, хотя в отчёте они складываются:**
автор просил показывать их одним числом «Not today», но осознанный отказ и молчание —
разные сигналы. Разделение в хранении ничего не стоит и оставляет возможность показать
их отдельно позже. В тексте отчёта складывай.

**Миграция.** В `init_db` (`bot/database/engine.py`) `Base.metadata.create_all` создаст
таблицу сам — отдельный `ALTER TABLE` не нужен. Но добавь в список soft-миграций `users`
три новые колонки из раздела 5.

**Удаление привычки.** В `cb_del_habit` (`bot/handlers/habits.py:549`) перед
`delete_by_id` вызови новый `HabitEventDAO.delete_for_reminder(task_id)`. Иначе висящие
FK. Удаление привычки посреди недели убирает её из отчёта — так и задумано.

---

## 2. `HabitEventDAO`

Новый файл `bot/database/dao/habit_event.py`, наследник `BaseDAO[HabitEvent]` — смотри
`bot/database/dao/reminder.py` как образец стиля.

```python
async def record(
    self,
    *,
    reminder,            # Reminder ORM-объект
    user_tz: str,
    outcome: str,        # done | not_today | missed
    source: str,
    due_at_utc_naive: datetime | None = None,
    local_date: str | None = None,
) -> bool:
    """Записать исход цикла. Возвращает True, если строка создана.

    Идемпотентно: если по (reminder_id, cycle_key) строка уже есть, НИЧЕГО не
    перезаписывает и возвращает False. Явная отметка юзера всегда побеждает
    более поздний автопропуск, потому что приходит раньше.
    """
```

Внутри: посчитать `cycle_key` и `local_date` (для fixed — локальная дата от `due_at`,
переведённого в таймзону юзера; для fluid — переданный `local_date`), сделать
`SELECT ... WHERE reminder_id=? AND cycle_key=?`, при отсутствии — `session.add` +
`flush`. Гонку добивает `UniqueConstraint`: оберни `flush` в `try/except IntegrityError`
→ `return False`.

Ещё методы:

```python
async def delete_for_cycle(self, reminder_id: int, cycle_key: str) -> None
async def delete_for_reminder(self, reminder_id: int) -> None
async def get_events_in_range(self, user_id: int, start_date: str, end_date: str) -> Sequence[HabitEvent]
    # включительно с обеих сторон, сравнение строк YYYY-MM-DD корректно лексикографически
```

---

## 3. Автоматический «Not today»

**Единственный писатель событий `missed` — новый свипер, а не планировщик.** Причина:
привязка к моменту оповещения теряет пропуски при простое бота, блокировке бота юзером и
при отключённом nagging. Свипер отрабатывает те же случаи из состояния БД.

Новый файл `bot/services/habit_sweeper.py`, минутный cron (копируй регистрацию из
`setup_daily_briefs`, `bot/services/daily_briefs.py:211`), функция `sweep_habit_cycles()`,
регистрация `setup_habit_sweeper(scheduler)` в `bot/__main__.py` рядом со строкой 63.

**Fixed-привычки.** Для каждой активной fixed-привычки (`is_habit`, не `is_fluid_habit`,
`status == "pending"`):

```
due = reminder.habit_active_due_at
закрывать, если:
  due is not None
  и reminder.habit_last_completed_due_at != due          # не засчитана как done
  и now_utc >= due + 24h                                  # грейс истёк = пришло следующее плановое
  и due >= reminder.created_at                            # не выдумываем циклы до создания
  и now_utc < due + 24h + 7 дней                          # окно ретроспективы
→ HabitEventDAO.record(outcome="missed", source="auto", due_at_utc_naive=due)
→ reminder.habit_streak_current = 0
```

Окно в 7 дней обязательно: без него первый деплой сгенерирует событие для каждой
привычки с давним `habit_active_due_at`. Порог `due + 24h` совпадает с грейсом
`apply_habit_streak_completion` — для FREQ=DAILY это и есть «следующее плановое
оповещение», как в постановке.

Существующий сброс стрика в `SchedulerService._execute_reminder`
(`bot/services/scheduler.py:216-229`) **не трогай** — на него опираются тесты в
`bot/services/test_habit_streaks.py`. Свипер и планировщик оба ставят стрик в 0, обе
операции идемпотентны, конфликта нет.

**Fluid-привычки.** Для каждой активной fluid-привычки:

```
yesterday = (локальная дата юзера) - 1 день
закрывать, если:
  локальное время юзера уже >= 00:00 текущего дня (то есть всегда — проверяем вчера)
  и reminder.fluid_last_completed_date != yesterday
  и нет события с cycle_key = f"day:{yesterday}"
  и yesterday >= локальная дата created_at привычки
→ HabitEventDAO.record(outcome="missed", source="auto", local_date=yesterday)
```

Стрик fluid уже чинит `reset_stale_fluid_streak_if_needed` — отдельно не сбрасывай.
Ограничь ретроспективу теми же 7 днями: при первом запуске проверяй только вчерашний
день, не весь backlog.

Свипер обязан быть безопасен при падении на одном юзере: оборачивай тело цикла по юзерам
в `try/except` с логом, как это сделано в `process_daily_briefs`.

---

## 4. Кнопка «Not today» и запись отметок

### 4.1 Клавиатура

`get_task_done_keyboard` в `bot/keyboards/inline.py:233` — добавь параметр
`show_not_today: bool = False`. Когда `True`, во **второй ряд** (сразу под «Done», до
snooze-кнопок) добавь одну кнопку `l10n["btn_not_today"]` с callback:

```
not_today_{reminder_id}                    # fluid
not_today_{reminder_id}_{cycle_due_ts}     # fixed, если cycle_due_ts известен
```

Формат зеркалит уже существующий `done_task_{id}_{ts}` — парсинг делай так же, как в
`callback_task_done` (`bot/handlers/reminders.py:974`).

В `SchedulerService._execute_reminder` (`bot/services/scheduler.py:236`) передавай
`show_not_today=self._is_habit_like(reminder) or reminder.is_fluid_habit`.

Внимание: `_is_habit_like` в `scheduler.py:155` возвращает `False` для fluid-привычек —
это намеренно, не «чини» его, просто добавь `or reminder.is_fluid_habit` в вызове.

### 4.2 Хендлер `not_today_`

Новый хендлер в `bot/handlers/habits.py` (фича habits-only). Зависимости приедут из
middleware: `reminder_dao`, `habit_event_dao`, `scheduler_service`, `user`, `l10n`.

Логика:

1. Распарсить `reminder_id` и опциональный `cycle_due_ts`.
2. Достать привычку; если её нет или она не habit-like и не fluid → `l10n["invalid_action"]`.
3. Идемпотентность: если цикл уже разрешён (событие есть) → ответить
   `l10n.get("already_done", ...)` и выйти.
4. Записать событие `outcome="not_today"`, `source="button"`.
5. Сбросить стрик: fixed → `habit_streak_current = 0`; fluid → `fluid_streak_current = 0`.
6. Закрыть цикл, **не помечая выполненным** — см. 4.3.
7. `scheduler_service.remove_nagging_job(reminder.id)`.
8. Убрать клавиатуру, дописать к тексту `l10n["habit_not_today_saved"]`. Копируй
   обработку `TelegramBadRequest` из `callback_task_done` — параллельный тап штатен.

### 4.3 Новый метод `ReminderDAO.mark_habit_not_today`

**Не переиспользуй `mark_done`.** `mark_done` (`bot/database/dao/reminder.py:231`) ставит
`completed_at`, а вечерний бриф считает выполненное именно по `completed_at`
(`get_today_tasks_by_status`, ветка `status == "completed"`). Привычка с «Not today»
уехала бы в «✅ Выполнено».

Нужное поведение:

```python
async def mark_habit_not_today(self, reminder_id: int) -> None:
    """Закрыть текущий цикл привычки как неразрешённый.

    Скрывает цикл из активных списков (как mark_done), но НЕ ставит completed_at,
    поэтому в вечерний бриф привычка не попадает как выполненная.
    """
```

- `completed_for_execution_time = execution_time`, но **только если**
  `execution_time <= now_utc` — та же защита, что в `mark_done:266`, иначе спрячешь
  следующий цикл, уже перекаченный планировщиком.
- `completed_at` — не трогать.
- `last_nag_chat_id` / `last_nag_message_id` → `None`.
- `flush`.

Для fluid-привычек «цикл разрешён на сегодня» определяется **наличием события за
локальную дату**, а не колонкой. Новую колонку не заводи.

Следствие: в `bot/services/daily_briefs.py:194` строка

```python
pending_fluid = [h for h in fluid_habits if h.fluid_last_completed_date != today_str]
```

должна стать «нет события за `today_str`» — иначе юзер, нажавший «Not today» утром,
получит вечером просьбу отметить эту же привычку.

### 4.4 Отметка Done тоже пишет событие

В `callback_task_done` (`bot/handlers/reminders.py:966`) — после успешного
`apply_habit_streak_completion` / `mark_fluid_habit_done_today` — записывай событие
`outcome="done"`, `source="button"`. Для fixed используй тот же `due_at`, который ушёл в
`apply_habit_streak_completion`, иначе `cycle_key` разъедется с событием автопропуска и
дедупликация не сработает.

То же самое в `_mark_wrapup_task_done` (`bot/handlers/reminders.py:862`), но с
`source="wrapup"`.

### 4.5 Wrap-up «Not done» → событие

`callback_wrapup_not_done` (`bot/handlers/reminders.py:945`) сейчас только перерисовывает
ряд. Добавь: достать reminder; **если это привычка** (`_is_habit_like(reminder)` или
`is_fluid_habit`) — записать `outcome="not_today"`, `source="wrapup"`, сбросить стрик и
вызвать `mark_habit_not_today`. Для обычных задач поведение не меняется.

Хендлеру сейчас не инжектится `reminder_dao` — добавь параметр, middleware его
предоставит (см. `callback_wrapup_done` рядом).

### 4.6 Undo обязан удалять событие

`callback_done_undo` (`bot/handlers/reminders.py:1143`) сейчас откатывает
`status`/`completed_at`, но **не трогает стрик-поля привычки** — это существующая дыра, и
с появлением отчётов она начнёт врать цифрами. В рамках этой задачи:

- удалить событие цикла (`delete_for_cycle`);
- откатить `habit_last_completed_due_at` на предыдущий засчитанный цикл — если предыдущего
  события `done` нет, ставь `None`, а `habit_streak_current` уменьшай на 1 (не ниже 0);
  `habit_streak_best` не трогай (это исторический максимум).

Если полный откат стрика окажется дороже, чем выглядит, — допустимо ограничиться удалением
события и уменьшением `habit_streak_current`, но **удаление события не опционально**:
без него отчёт покажет выполнение, которое юзер отменил.

---

## 5. Настройки отчётов

Три новые колонки в модели `User` (`bot/database/models.py`) + soft-миграция в
`init_db`, в существующий список для таблицы `users`:

| Колонка | Тип / DDL | Дефолт |
|---|---|---|
| `habit_reports_enabled` | `BOOLEAN DEFAULT 1` | `True` |
| `habit_report_weekday` | `INTEGER DEFAULT 6` | `6` = воскресенье (Python `weekday()`: Пн=0) |
| `habit_report_time` | `VARCHAR DEFAULT '23:50'` | `'23:50'` |

UI — по образцу «Briefs setup»:

- Кнопка `btn_habit_reports_setup` в `get_settings_keyboard` (`bot/keyboards/inline.py:552`).
- `get_habit_reports_setup_keyboard(l10n, enabled, weekday, time_str)`: тумблер
  `habit_reports_toggle`, выбор дня `habit_report_edit_day`, выбор времени
  `habit_report_edit_time`, назад `settings_back`.
- Выбор дня — инлайн-клавиатура из 7 кнопок `habit_report_day_{0..6}`, без FSM.
- Ввод времени — новый `SettingsState.waiting_for_habit_report_time`, валидация строго
  регуляркой `^(\d{1,2}):(\d{2})$` + диапазон, **точно как** `state_briefs_set_time`
  (`bot/handlers/settings.py`). Не используй `InputParser` — он для фраз-напоминаний, а не
  для конфигурации времени; это уже описано как BUG-H4 в коде.
- Хранение через `UserDAO` — добавь метод по образцу `update_briefs_settings`.

---

## 6. Сервис отчётов

Новый файл `bot/services/habit_reports.py`, минутный cron, `setup_habit_reports(scheduler)`
регистрируется в `bot/__main__.py`.

```
для каждого юзера с habit_reports_enabled и хотя бы одной привычкой:
    now_local = datetime.now(tz юзера)
    если now_local.strftime("%H:%M") != user.habit_report_time: continue
    если now_local.weekday() != user.habit_report_weekday: continue

    отправить недельный отчёт: окно = [now_local.date() - 6 дней, now_local.date()]

    если (now_local.date() + 7 дней).month != now_local.date().month:
        # это последний такой день недели в месяце
        отправить месячный отчёт: окно = [1-е число месяца, now_local.date()]
```

Проверка «последнее воскресенье» через `+7 дней выпадает на другой месяц` — единственный
корректный способ, не изобретай арифметику по числам 25–31.

**Тихие часы отчёты игнорируют.** Дефолтное время 23:50 попадает в дефолтное окно тихих
часов (23:00–07:00), и фильтр как в `daily_briefs._is_quiet_local` означал бы, что юзер с
включёнными тихими часами никогда не получит отчёт, который сам же и настроил. Это
намеренное отличие от брифов — зафиксируй комментарием в коде.

**Агрегация.** `get_events_in_range` → сгруппировать по `reminder_id`, для каждой группы:
`done = count(outcome == "done")`, `not_today = count(outcome in ("not_today", "missed"))`,
`total = done + not_today`, `rate = round(done / total * 100)`. Имя брать из `habit_text`
последнего события группы. Текущий стрик — из живой строки `reminders`, если привычка ещё
существует; для удалённых стрик не показывать.

Порядок в отчёте: по `rate` убыванием, при равенстве — по `done` убыванием. Так лучшие
недели сверху, и отчёт читается как повод продолжить, а не как список провалов.

**Если событий в окне нет — сообщение не отправлять вовсе.** Пустой отчёт каждое
воскресенье — это шум, ради которого юзер отключит фичу целиком.

Отправка — через локальный аналог `_send_safe` из `daily_briefs.py` (глушит
`TelegramForbiddenError` / `TelegramBadRequest`).

Формат сообщения (RU, аналогично для EN/ES; `parse_mode="Markdown"`, как у брифов):

```
📊 **Итоги недели** (10.07 – 16.07)

🫧 Тренировка — 5/7 (71%) · 🔥 3
🫧 Вода — 7/7 (100%) · 🔥 12
🫧 Отдых — 2/7 (29%)

**Итого:** ✅ 14 · ❌ 7 (67%)
```

Названия привычек экранируй — они пользовательские и ломают Markdown. В проекте есть
`bot/utils/markdown.py`; посмотри, что там есть, прежде чем писать своё.

---

## 7. Локализация

Строки — в `bot/lexicon/{en,ru,es}.py`. Два теста в
`bot/services/test_l10n_key_coverage.py` жёстко это стерегут:

1. `test_ru_and_es_cover_all_en_keys` — RU и ES обязаны покрывать **все** ключи EN.
2. `test_spanish_is_not_accidentally_falling_back_to_english` — ни один новый ES-ключ не
   должен совпадать по значению с EN, иначе тест упадёт. Реально переводи.

Новые ключи:

- `btn_not_today`, `habit_not_today_saved`
- `habit_report_weekly_title`, `habit_report_monthly_title`, `habit_report_line`,
  `habit_report_total`
- `btn_habit_reports_setup`, `btn_habit_reports_on`, `btn_habit_reports_off`,
  `btn_habit_report_day`, `btn_habit_report_time`, `habit_report_day_prompt`,
  `habit_report_time_prompt`
- `weekday_names` — список из 7 строк, индекс совпадает с `datetime.weekday()` (Пн=0).
  Значением ключа может быть список; тест покрытия проверяет только ключи.

---

## 8. Тесты

Стиль — как в `bot/services/test_habit_streaks.py` (pytest-asyncio, in-memory SQLite;
смотри `bot/conftest.py`). Новые файлы:

**`bot/services/test_habit_events.py`**
- `record` дважды с одним `cycle_key` → вторая возвращает `False`, строк в БД — одна.
- Явный `done` записан → более поздний `missed` от свипера **не** перезаписывает исход.
- `not_today` обнуляет `habit_streak_current`, но не трогает `habit_streak_best`.
- `mark_habit_not_today` не выставляет `completed_at` — регрессия на «привычка уехала в
  выполненные».
- Undo удаляет событие цикла.

**`bot/services/test_habit_sweeper.py`**
- fixed-привычка с due 25 часов назад и без отметки → появляется `missed`.
- fixed-привычка, засчитанная как done (`habit_last_completed_due_at == due`) → событий нет.
- fixed-привычка с due 10 дней назад → событий нет (окно ретроспективы).
- fluid-привычка, не отмеченная вчера → `missed` за вчера; отмеченная → событий нет.
- Привычка, созданная сегодня → вчерашний день не закрывается.

**`bot/services/test_habit_reports.py`**
- Окно недельного отчёта = 7 локальных дат включительно.
- Определение последнего воскресенья: месяц с 4 воскресеньями и месяц с 5; проверь, что
  для предпоследнего воскресенья месячный отчёт **не** уходит.
- Месячное окно = 1-е число .. день отправки.
- Пустое окно → сообщение не отправляется.
- Агрегация: `not_today` и `missed` складываются в одно число.

Прогон: `pytest` (конфиг в `pytest.ini`). Прогоняй **весь** набор, а не только новые
файлы: правки затрагивают `daily_briefs`, `scheduler` и `reminders`, у которых есть
собственные тесты.

---

## 9. Порядок работы

Каждый шаг оставляет репозиторий в зелёном состоянии — прогоняй `pytest` после каждого.

1. Модель `HabitEvent` + `HabitEventDAO` + миграции + инжекция DAO в middleware
   (`bot/middlewares/database.py` — посмотри, как туда попадает `reminder_dao`).
2. Колонки `User` + soft-миграция.
3. `mark_habit_not_today` в `ReminderDAO`.
4. Кнопка «Not today» + хендлер + запись `done`-событий в существующих путях (4.4–4.6).
5. Свипер автопропусков.
6. Правка `pending_fluid` в `daily_briefs` (4.3).
7. Сервис отчётов.
8. Настройки (UI + FSM).
9. Локализация всех трёх языков.
10. Тесты.

**Что доложить в конце:** какие шаги сделаны и прошли тесты; отдельно — трогал ли ты
откат стрика в `callback_done_undo` полностью или ограниченным вариантом из 4.6.
