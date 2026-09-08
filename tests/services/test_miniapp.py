"""4.6: Mini App initData HMAC validation and JSON payload shaping
(bot/services/miniapp.py). No aiohttp/DB here — see test_webserver.py for
the route-level integration."""

import hashlib
import hmac
import json
import time
from datetime import date
from types import SimpleNamespace
from urllib.parse import urlencode

from bot.services.miniapp import build_heatmap_payload, build_scores_payload, validate_init_data

BOT_TOKEN = "123456:AAFake-Bot-Token-For-Tests"


def _make_init_data(user: dict, *, auth_date: int = None, bot_token: str = BOT_TOKEN, tamper: bool = False) -> str:
    """Build a signed initData string the same way Telegram's client would,
    so tests exercise the real HMAC scheme end to end."""
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "query_id": "AAabc123",
        "user": json.dumps(user, separators=(",", ":")),
        "auth_date": str(auth_date),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if tamper:
        computed_hash = "0" * 64
    fields["hash"] = computed_hash
    return urlencode(fields)


def test_valid_init_data_returns_user_id() -> None:
    init_data = _make_init_data({"id": 42, "first_name": "Alice"})
    result = validate_init_data(init_data, BOT_TOKEN)
    assert result is not None
    assert result["user_id"] == 42
    assert result["user"]["first_name"] == "Alice"


def test_tampered_hash_is_rejected() -> None:
    init_data = _make_init_data({"id": 42}, tamper=True)
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_wrong_bot_token_is_rejected() -> None:
    init_data = _make_init_data({"id": 42})
    assert validate_init_data(init_data, "999999:WrongToken") is None


def test_missing_hash_is_rejected() -> None:
    assert validate_init_data("query_id=abc&user=%7B%7D", BOT_TOKEN) is None


def test_expired_auth_date_is_rejected() -> None:
    stale = int(time.time()) - 90000  # >25h old
    init_data = _make_init_data({"id": 42}, auth_date=stale)
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_future_auth_date_is_rejected() -> None:
    future = int(time.time()) + 3600
    init_data = _make_init_data({"id": 42}, auth_date=future)
    assert validate_init_data(init_data, BOT_TOKEN) is None


def test_empty_init_data_is_rejected() -> None:
    assert validate_init_data("", BOT_TOKEN) is None


def test_malformed_user_json_is_rejected() -> None:
    fields = {"user": "not-json", "auth_date": str(int(time.time()))}
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    assert validate_init_data(urlencode(fields), BOT_TOKEN) is None


def test_client_supplied_user_id_only_trusted_after_signature_check() -> None:
    # Same forged user id, but signed with the WRONG token — must fail even
    # though the payload "looks" like a legitimate request for user 999.
    forged = _make_init_data({"id": 999}, bot_token="attacker-token")
    assert validate_init_data(forged, BOT_TOKEN) is None


def test_real_init_data_with_signature_field_is_accepted() -> None:
    """fix(1.2): Bot API 7.10 added a "signature" field (Ed25519, for
    third-party validation) to initData. Telegram's docs are ambiguous
    about whether it belongs in the HMAC data-check-string; the wrong
    answer would make every Mini App request fail HMAC validation. Build a
    signed initData string carrying "signature" the same way a real
    Telegram client would (i.e. NOT excluded from the data-check-string —
    confirmed against aiogram's own reference implementation) and check it
    is accepted end to end."""
    fields = {
        "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
        "user": json.dumps({"id": 777, "first_name": "Bob"}, separators=(",", ":")),
        "auth_date": str(int(time.time())),
        "signature": "3q2-7_ed25519-looking-base64url-signature",
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    result = validate_init_data(urlencode(fields), BOT_TOKEN)

    assert result is not None
    assert result["user_id"] == 777
    assert result["user"]["first_name"] == "Bob"


def test_build_heatmap_payload_fills_gaps_with_zero_days() -> None:
    events = [SimpleNamespace(local_date="2026-01-02", outcome="done")]
    payload = build_heatmap_payload(events, date(2026, 1, 1), date(2026, 1, 3))
    assert [d["date"] for d in payload["days"]] == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert payload["days"][0]["total"] == 0
    assert payload["days"][1]["done"] == 1
    assert payload["days"][1]["total"] == 1


def test_build_heatmap_payload_aggregates_multiple_events_same_day() -> None:
    events = [
        SimpleNamespace(local_date="2026-01-01", outcome="done"),
        SimpleNamespace(local_date="2026-01-01", outcome="done"),
        SimpleNamespace(local_date="2026-01-01", outcome="missed"),
    ]
    payload = build_heatmap_payload(events, date(2026, 1, 1), date(2026, 1, 1))
    day = payload["days"][0]
    assert day["done"] == 2
    assert day["missed"] == 1
    assert day["total"] == 3


def test_build_scores_payload_uses_fluid_streaks_for_fluid_habits() -> None:
    reminder = SimpleNamespace(
        id=1, reminder_text="Meditate", is_fluid_habit=True,
        fluid_streak_current=5, fluid_streak_best=9,
        habit_streak_current=0, habit_streak_best=0,
    )
    events = ["e1", "e2"]
    payload = build_scores_payload([(reminder, events)], score_fn=lambda evs: 77)
    assert payload["habits"] == [
        {"id": 1, "text": "Meditate", "score": 77, "streak_current": 5, "streak_best": 9}
    ]


def test_build_scores_payload_empty_events_scores_zero_without_calling_score_fn() -> None:
    reminder = SimpleNamespace(
        id=2, reminder_text="Run", is_fluid_habit=False,
        habit_streak_current=0, habit_streak_best=0,
    )
    called = []
    payload = build_scores_payload([(reminder, [])], score_fn=lambda evs: called.append(1) or 100)
    assert payload["habits"][0]["score"] == 0
    assert called == []
