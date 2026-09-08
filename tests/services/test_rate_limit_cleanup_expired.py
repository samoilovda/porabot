"""Regression (REWORK_PLAN_3 3.1): RateLimitMiddleware._hits only ever grew.
A user's deque is trimmed lazily by that SAME user's own future events — a
user who sends a handful of messages and never returns leaves their dict
entry (and whatever hits hadn't aged out at the time) in memory forever, for
the lifetime of the process.
"""

from bot.middlewares.rate_limit import RateLimitMiddleware


def test_cleanup_expired_drops_entries_with_no_hits_left_in_window() -> None:
    mw = RateLimitMiddleware(max_updates=20, window_seconds=10.0)
    # A hit far enough in the past that it's already outside the window.
    mw._hits[111].append(0.0)
    mw._hits[222].append(1_000_000.0)  # a hit "now" (monotonic clocks are large)

    import time

    now = time.monotonic()
    # Make the fixture deterministic regardless of how long the test takes
    # to reach this point: force user 222's hit to be "now".
    mw._hits[222][0] = now

    mw.cleanup_expired()

    assert 111 not in mw._hits
    assert 222 in mw._hits


def test_cleanup_expired_is_a_noop_when_nothing_is_stale() -> None:
    mw = RateLimitMiddleware(max_updates=20, window_seconds=10.0)
    import time

    mw._hits[1].append(time.monotonic())

    mw.cleanup_expired()

    assert 1 in mw._hits


def test_cleanup_expired_does_not_grow_unboundedly_for_one_off_visitors() -> None:
    """The actual leak scenario: many distinct users each send one message
    and never return. Without cleanup, every one of them stays in the dict
    forever."""
    mw = RateLimitMiddleware(max_updates=20, window_seconds=10.0)
    import time

    old = time.monotonic() - 1000  # long past the 10s window
    for user_id in range(500):
        mw._hits[user_id].append(old)

    assert len(mw._hits) == 500

    mw.cleanup_expired()

    assert len(mw._hits) == 0
