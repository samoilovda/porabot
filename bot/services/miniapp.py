"""4.6: Telegram Mini App support — initData auth and JSON payload shaping.

Deliberately free of aiohttp/DB imports: this module is pure functions over
plain data so it's trivially unit-testable, and bot/services/webserver.py
does the I/O (auth header, DB session, JSON response) around it.

initData validation follows Telegram's documented scheme
(https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app):

  1. secret_key = HMAC_SHA256(key="WebAppData", msg=bot_token)
  2. data_check_string = all initData fields except "hash", "key=value"
     pairs sorted alphabetically by key and joined with "\n"
  3. computed_hash = HMAC_SHA256(key=secret_key, msg=data_check_string),
     hex-encoded
  4. valid iff computed_hash == the "hash" field, compared in constant time

The actual HMAC check (step 2-4) delegates to aiogram's own
`aiogram.utils.web_app.safe_parse_webapp_init_data` (fix 1.2) rather than a
hand-rolled implementation, so it tracks Bot API changes upstream instead
of drifting. Confirmed against a real initData string carrying Bot API
7.10's "signature" field (Ed25519, for third-party validation): aiogram's
data-check-string excludes only "hash" — "signature" is treated as a
regular field and included, same as this module always did — so that field
does not break the HMAC check either before or after this change.

This is a real auth boundary: the client can put ANY user_id it wants into
the unsigned parts of a request, so nothing here ever trusts a client-
supplied user id — only the "user" field's id from a request whose hash
validated against the bot's own token counts.
"""

import time
from datetime import date, timedelta
from typing import Any, Optional

from aiogram.utils.web_app import safe_parse_webapp_init_data
from pydantic import ValidationError

# initData is short-lived proof of an active Mini App session, not a
# long-term credential — Telegram itself recommends treating anything
# older than a day as stale.
_MAX_INIT_DATA_AGE_SECONDS = 86400
# auth_date is a Unix timestamp Telegram itself generated — a "future"
# value would only ever mean clock skew, but bound it anyway rather than
# accept an arbitrarily-forged future date.
_CLOCK_SKEW_TOLERANCE_SECONDS = 60


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = _MAX_INIT_DATA_AGE_SECONDS,
    now: Optional[int] = None,
) -> Optional[dict]:
    """Validate a Telegram Mini App `initData` string.

    Returns {"user_id": int, "user": dict, "auth_date": int} on success,
    None on any validation failure (bad signature, missing/malformed
    fields, expired auth_date). Never raises on malformed input.
    """
    if not init_data or not bot_token:
        return None

    try:
        parsed = safe_parse_webapp_init_data(token=bot_token, init_data=init_data)
    except (ValueError, ValidationError, TypeError):
        # ValueError: bad/missing hash (aiogram's own signature check).
        # ValidationError/TypeError: well-signed but malformed field shape
        # (e.g. "user" isn't valid JSON/object) — still not trustworthy.
        return None

    if parsed.user is None:
        return None

    auth_date = int(parsed.auth_date.timestamp())
    current = now if now is not None else int(time.time())
    if current - auth_date > max_age_seconds:
        return None
    if auth_date - current > _CLOCK_SKEW_TOLERANCE_SECONDS:
        return None

    user_obj = parsed.user.model_dump(exclude_none=True)
    user_id = user_obj.get("id")
    if not isinstance(user_id, int):
        return None

    return {"user_id": user_id, "user": user_obj, "auth_date": auth_date}


def build_heatmap_payload(events: Any, start: date, end: date) -> dict:
    """Turn a flat list of HabitEvent-like rows (.local_date, .outcome) for
    an arbitrary number of habits into one day-by-day completion heatmap
    covering [start, end] inclusive — days with no events at all still
    appear, with all-zero counts, so the frontend grid has no gaps."""
    by_date: dict[str, dict[str, int]] = {}
    cursor = start
    while cursor <= end:
        by_date[cursor.isoformat()] = {"done": 0, "not_today": 0, "missed": 0}
        cursor += timedelta(days=1)

    for event in events:
        bucket = by_date.get(event.local_date)
        if bucket is None:
            continue
        outcome = event.outcome if event.outcome in ("done", "not_today", "missed") else "missed"
        bucket[outcome] += 1

    days = [
        {
            "date": day,
            "done": counts["done"],
            "not_today": counts["not_today"],
            "missed": counts["missed"],
            "total": counts["done"] + counts["not_today"] + counts["missed"],
        }
        for day, counts in sorted(by_date.items())
    ]
    return {"start": start.isoformat(), "end": end.isoformat(), "days": days}


def build_scores_payload(habits_with_events: list[tuple[Any, Any]], score_fn) -> dict:
    """*habits_with_events* is [(reminder, events_oldest_first), ...];
    *score_fn* is compute_habit_score (injected instead of imported to keep
    this module dependency-light and easy to unit test)."""
    habits = []
    for reminder, events in habits_with_events:
        score = score_fn(events) if events else 0
        is_fluid = bool(getattr(reminder, "is_fluid_habit", False))
        current = reminder.fluid_streak_current if is_fluid else reminder.habit_streak_current
        best = reminder.fluid_streak_best if is_fluid else reminder.habit_streak_best
        habits.append(
            {
                "id": reminder.id,
                "text": reminder.reminder_text,
                "score": score,
                "streak_current": max(0, int(current or 0)),
                "streak_best": max(0, int(best or 0)),
            }
        )
    return {"habits": habits}
