"""
Porabot — Composition Root.

Wires:  config → database → middleware → routers → scheduler → polling.
No business logic lives here.
"""

import asyncio
import logging
import signal
import sys
import time

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BotCommand, ErrorEvent
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

from bot.config import config, validate_config

validate_config()

from bot.database.engine import create_engine, create_session_maker, dispose_engine, init_db
from bot.handlers import all_routers
from bot.handlers.reminders import _cleanup_stale_timers
from bot.middlewares.database import DatabaseMiddleware
from bot.middlewares.private_chat_only import PrivateChatOnlyMiddleware
from bot.middlewares.rate_limit import RateLimitMiddleware
from bot.services.daily_briefs import setup_daily_briefs
from bot.services.delete_cleanup import setup_delete_cleanup
from bot.services.fsm_storage import SQLAlchemyFSMStorage, cleanup_stale_fsm_state
from bot.services.habit_reports import setup_habit_reports
from bot.services.habit_sweeper import setup_habit_sweeper
from bot.services.missed_recovery import setup_missed_task_recovery
from bot.services.retention_cleanup import setup_retention_cleanup
from bot.services.scheduler import SchedulerService, remove_orphan_scheduler_jobs_job
from bot.services.webserver import HTTP_RATE_LIMITER_KEY, create_app, start_web_server


def _write_heartbeat() -> None:
    """Touch config.HEARTBEAT_FILE with the current time (4.4).

    docker-compose's `restart: always` only reacts to the process exiting —
    a polling loop that's alive but hung (deadlocked, stuck on a network
    call that never times out) looks identical to a healthy container from
    the outside. Written once directly at startup (main()) and then, IF a
    live Telegram connectivity check passes, every minute by
    HeartbeatMonitor.check_and_write (3.2) — this function itself never
    checks connectivity, it just does the file write half of that.
    docker-compose's healthcheck fails once this file goes stale.
    """
    try:
        with open(config.HEARTBEAT_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError as e:
        logger.warning("Failed to write heartbeat file %s: %s", config.HEARTBEAT_FILE, e)


class HeartbeatMonitor:
    """3.2: "the process is alive and scheduling jobs" is not the same
    claim as "the process can actually talk to Telegram" — a polling loop
    stuck on a hung network call (no timeout ever fires) used to keep
    _write_heartbeat touching the file every minute like nothing was
    wrong, so the healthcheck stayed green and `restart: always` never
    triggered. That's the exact "alive but dead" scenario this file
    exists to catch in the first place.

    A class (not a closure inside main()) so this is unit-testable on its
    own — construct one with a fake bot/stop_event, call check_and_write()
    directly, and assert on consecutive_failures / stop_event.is_set()
    without needing to run all of main().

    Registered as a periodic job on the "memory" jobstore (1.4 moved every
    service/cron job there) — a bound method is only unsafe to schedule
    when it gets PICKLED (the persistent SQLAlchemyJobStore's problem, see
    1.4), and MemoryJobStore never pickles anything.
    """

    def __init__(
        self,
        bot: Bot,
        stop_event: asyncio.Event,
        *,
        failure_limit: int = 3,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.bot = bot
        self.stop_event = stop_event
        self.failure_limit = failure_limit
        self.timeout_seconds = timeout_seconds
        self.consecutive_failures = 0

    async def check_and_write(self) -> None:
        try:
            await asyncio.wait_for(self.bot.get_me(), timeout=self.timeout_seconds)
        except Exception as e:
            self.consecutive_failures += 1
            logger.warning(
                "Heartbeat: Telegram connectivity check failed (%d/%d): %s",
                self.consecutive_failures,
                self.failure_limit,
                e,
            )
            if self.consecutive_failures >= self.failure_limit:
                logger.critical(
                    "Heartbeat: Telegram connectivity failed %d times in a row — "
                    "triggering shutdown so restart: always can recover.",
                    self.consecutive_failures,
                )
                # Same graceful path a real SIGTERM takes (see 1.1) — not a
                # hard kill, so an in-flight send still gets to finish.
                self.stop_event.set()
            return
        self.consecutive_failures = 0
        _write_heartbeat()


def _start_polling_coro(dp: Dispatcher, bot: Bot):
    """dp.start_polling(...), broken out to a top-level function so a test
    can assert on the call without exercising the rest of main() (DB
    engine, real Bot/scheduler setup, ...).

    handle_signals=False is load-bearing, not cosmetic: aiogram's own
    start_polling() calls loop.add_signal_handler(SIGTERM/SIGINT, ...) by
    default, which REPLACES (not chains) the handlers main() registers
    itself just before this call — see 1.1's fix note at the call site in
    main(). Without this, stop_event is never set by a real shutdown
    signal, and _run_until_stopped sees polling end on its own and raises
    "Polling stopped unexpectedly" on every single deploy/`docker stop`.
    """
    return dp.start_polling(bot, handle_signals=False)


async def _run_until_stopped(polling_coro, stop_event: asyncio.Event) -> None:
    """Run *polling_coro* until either *stop_event* is set or it ends on its own.

    Waiting on stop_event alone (the earlier version of this function) left
    an unhandled exception from polling silently un-retrieved: the process
    would just hang forever with dead polling, Docker would never see a
    non-zero exit, and `restart: always` would never kick in. Waiting on
    FIRST_COMPLETED between the two means a crash surfaces immediately.
    """
    polling_task = asyncio.ensure_future(polling_coro)
    stop_task = asyncio.ensure_future(stop_event.wait())
    done, pending = await asyncio.wait({polling_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in pending:
        try:
            await task
        except asyncio.CancelledError:
            pass

    if polling_task in done and not stop_event.is_set():
        # Polling ended on its own, not via a shutdown signal — surface
        # whatever it raised (or a plain error if it just returned) so the
        # process exits non-zero instead of quietly staying alive but dead.
        exc = polling_task.exception()
        if exc is not None:
            raise exc
        raise RuntimeError("Polling stopped unexpectedly without a shutdown signal.")


async def handle_dispatcher_error(event: ErrorEvent) -> None:
    """1.2: last-resort handler for exceptions no router/middleware caught.

    Without this, a handler that raises (a malformed callback_data an
    attacker crafted, a message too long for edit_text, ...) just logs and
    goes silent from the user's point of view — for a CallbackQuery that
    means Telegram shows a spinning "loading" state on the tapped button
    until it times out, since callback.answer() was never reached.

    DatabaseMiddleware already rolls back the session on the same
    exception before it reaches here — nothing left to clean up on that
    front, this is purely "tell the user something went wrong".

    2.4: this used to always answer in Russian (get_l10n(None)) regardless
    of the user's actual language. The fix is NOT "accept l10n as a
    parameter and let aiogram's DI fill it from data['l10n']" — verified
    empirically that this does not work: ErrorsMiddleware re-dispatches to
    the "error" event type via `self.router.propagate_event(..., **data)`,
    but that *data* is ErrorsMiddleware's own outer-middleware-chain
    snapshot from BEFORE the inner "message"/"callback_query" observer's
    own middleware chain (where DatabaseMiddleware actually runs and sets
    data["l10n"]) — TelegramEventObserver.trigger's `**kwargs` unpacking
    creates a fresh dict at that boundary, so a later middleware's
    mutation of ITS OWN data dict never propagates back up to
    ErrorsMiddleware's copy. A parameter named `l10n` on this function is
    consequently always None from aiogram's DI, silently.
    Instead, read the language straight off the Update's own embedded
    user — every update type that can reach a handler carries a
    from_user with a language_code Telegram itself reports (the client's
    UI language) — via inner_event(event.update).from_user. Not the same
    as the user's saved in-bot language preference (which only
    DatabaseMiddleware's User row has, out of reach here — see above),
    but a solid, always-available signal, same trick 2.3 already uses for
    RateLimitMiddleware's notice, which runs under the same constraint.

    2.1: a "message is not modified" TelegramBadRequest is not a failure
    from the user's point of view — it means they tapped Refresh, a
    filter, or a Back button that happened to land on a screen identical
    to the one already showing. bot/utils/telegram.py's safe_edit_text/
    safe_edit_reply_markup already swallow this at the handful of call
    sites most likely to hit it, but with ~60 edit_text call sites across
    the handlers, this is the backstop for everywhere else: no alert, no
    error log, just acknowledge the tap so the button stops spinning.
    """
    if isinstance(event.exception, TelegramBadRequest) and "message is not modified" in str(event.exception).lower():
        logger.debug(
            "Update %s: edit_text/edit_reply_markup was a no-op (unchanged content).", event.update.update_id
        )
        callback_query = event.update.callback_query
        if callback_query is not None:
            try:
                await callback_query.answer()
            except Exception as e:
                logger.warning("Could not answer callback after a not-modified no-op: %s", e)
        return

    logger.error(
        "Unhandled error processing update %s: %s",
        event.update.update_id,
        event.exception,
        exc_info=event.exception,
    )
    from bot.lexicon import get_l10n
    from bot.utils.telegram import inner_event

    target_for_lang = inner_event(event.update)
    from_user = getattr(target_for_lang, "from_user", None) if target_for_lang is not None else None
    l10n = get_l10n(getattr(from_user, "language_code", None))
    text = l10n.get("generic_error", "❌ Something went wrong. Please try again.")
    update = event.update
    try:
        if update.callback_query is not None:
            try:
                await update.callback_query.answer(text, show_alert=True)
            except Exception as e:
                logger.warning("Could not answer callback after error: %s", e)
        elif update.message is not None:
            try:
                await update.message.answer(text)
            except Exception as e:
                logger.warning("Could not notify user after error: %s", e)
    except Exception as e:
        logger.error("Error handler itself failed: %s", e, exc_info=True)


async def _set_bot_commands(bot: Bot) -> None:
    """Populate Telegram's command menu (N2) — otherwise /help and /cancel are
    invisible unless a user already knows to type them.

    Cosmetic, not load-bearing: polling must start whether or not this
    succeeds. A crash-restart loop (of any cause) hammers SetMyCommands on
    every boot, and that method has a tight per-bot flood-control window —
    Telegram then makes every subsequent boot's call raise
    TelegramRetryAfter for several minutes. Letting that propagate used to
    take the whole process down with it (main() has nothing catching this),
    which turned one transient rate limit into an indefinite outage. Catch
    TelegramAPIError per language and move on; the menu just stays stale
    for that language until a future successful boot.
    """
    from aiogram.exceptions import TelegramAPIError

    from bot.lexicon import get_l10n

    for lang in (None, "en", "ru", "es"):
        l10n = get_l10n(lang)
        commands = [
            BotCommand(command="start", description=l10n.get("cmd_desc_start", "Main menu")),
            BotCommand(command="help", description=l10n.get("cmd_desc_help", "Show help")),
            BotCommand(command="cancel", description=l10n.get("cmd_desc_cancel", "Cancel current action")),
            BotCommand(command="find", description=l10n.get("cmd_desc_find", "Search your tasks")),
            BotCommand(command="donate", description=l10n.get("cmd_desc_donate", "Support Porabot")),
        ]
        try:
            await bot.set_my_commands(commands, language_code=lang)
        except TelegramAPIError as e:
            logger.warning("Failed to set bot commands for lang=%s: %s", lang, e)


async def _start_web_server_if_enabled(session_pool):
    """fix(1.1): opening an HTTP port is opt-in (WEB_SERVER_ENABLED), not a
    side effect of upgrading an existing install. Returns the runner if
    started, None otherwise — callers must skip cleanup() when None."""
    if not config.WEB_SERVER_ENABLED:
        logger.info("Web server disabled (WEB_SERVER_ENABLED=False)")
        return None
    logger.info(
        "Web server enabled, binding %s:%s", config.WEB_SERVER_HOST, config.WEB_SERVER_PORT
    )
    web_app = create_app(
        session_pool, bot_token=config.BOT_TOKEN.get_secret_value(), trusted_proxy=config.TRUSTED_PROXY
    )
    return await start_web_server(web_app, config.WEB_SERVER_HOST, config.WEB_SERVER_PORT)


async def main() -> None:
    logger.info("Starting Porabot (TZ=%s)", config.TZ)

    # Database
    engine = create_engine(config.DATABASE_URL)
    session_pool = create_session_maker(engine)
    await init_db(engine)
    logger.info("Database initialised.")

    # Telegram bot
    bot = Bot(
        token=config.BOT_TOKEN.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )

    # 3.2: stop_event/signal handlers, moved up from just before polling
    # used to start — the heartbeat job below (registered further down,
    # before scheduler.start()) needs to reference stop_event in its
    # closure, and doing that before this existed relied on the job's
    # first 1-minute tick landing after this assignment purely by timing.
    # Registering the signal handlers this early is also a genuine fix on
    # its own: previously, a SIGTERM arriving during the (potentially
    # slow) DB-init/scheduler-setup window below fell through to Python's
    # default disposition — an immediate hard kill, no graceful shutdown
    # at all — since the handler wasn't registered yet. Docker sends
    # SIGTERM on `docker compose up -d --build` recreate / `stop`; without
    # converting it to a normal asyncio shutdown, there's no chance to
    # finish an in-flight brief/reminder send before its "already sent"
    # flag is committed, deploy or not.
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            # add_signal_handler isn't supported on Windows event loops —
            # Ctrl+C there still falls through to the KeyboardInterrupt
            # handler at the bottom of this file.
            pass

    # 1.1: durable FSM storage — the default MemoryStorage silently drops
    # every in-progress wizard (time picker, timezone entry, nag-limit
    # prompt) on restart, and docker-compose's `restart: always` plus a
    # deploy recreating the container means that happens routinely. See
    # bot/services/fsm_storage.py's module docstring.
    fsm_storage = SQLAlchemyFSMStorage(session_pool)
    dp = Dispatcher(storage=fsm_storage)
    # 1.2: without this, an exception a handler doesn't catch just logs and
    # goes silent for the user — see handle_dispatcher_error's docstring.
    dp.errors.register(handle_dispatcher_error)
    await _set_bot_commands(bot)

    # Scheduler
    # Note: SQLAlchemyJobStore's own pickle_protocol already defaults to the
    # highest available protocol, so nothing needs to be set here explicitly.
    #
    # 1.4: two jobstores, not one. "default" (SQLAlchemyJobStore) holds only
    # reminder jobs (SchedulerService.schedule_reminder/_schedule_send_retry/
    # schedule_execution_retry/resume_nagging_if_stalled) — these must
    # survive a restart. "memory" (MemoryJobStore) holds every periodic
    # service/cron job below (heartbeat, the five per-minute cron jobs, the
    # cleanup sweeps). Those are all re-registered with replace_existing=True
    # on every single startup anyway, so persistence buys nothing — but
    # SQLAlchemyJobStore serializes a job by PICKLING it, and for a job
    # registered on a BOUND METHOD (rate_limit_middleware.cleanup_expired,
    # http_rate_limiter.cleanup_expired below) that means pickling the
    # method's __self__ too. Every scheduled run then unpickles and calls
    # cleanup_expired() on a FRESH COPY of that instance — the live object
    # actually receiving rate-limit hits is never touched, and its dict
    # grows forever despite the periodic sweep. MemoryJobStore never
    # pickles anything, so a bound method's job correctly acts on the live
    # instance. (Top-level-function jobs weren't broken by this, but move
    # here too for the same "no reason to persist" logic and so the
    # persistent store only ever holds reminder jobs.)
    scheduler = AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(url=config.SCHEDULER_DB_URL),
            "memory": MemoryJobStore(),
        },
        # Default is 1 second — any downtime (deploy, restart, crash) would make
        # APScheduler silently discard every job whose run_date fell during it.
        job_defaults={"misfire_grace_time": 3600, "coalesce": True},
    )
    scheduler_service = SchedulerService(scheduler, bot, session_pool)
    setup_daily_briefs(scheduler)
    setup_missed_task_recovery(scheduler)
    setup_habit_sweeper(scheduler)
    setup_habit_reports(scheduler)
    setup_delete_cleanup(scheduler)
    setup_retention_cleanup(scheduler)
    scheduler.add_job(
        _cleanup_stale_timers,
        "interval",
        minutes=10,
        id="cleanup_stale_timers",
        replace_existing=True,
        jobstore="memory",
    )
    # 1.1: drop abandoned FSM rows (see fsm_storage.cleanup_stale_fsm_state) —
    # same reasoning as cleanup_stale_timers/cleanup_rate_limit_hits below.
    scheduler.add_job(
        cleanup_stale_fsm_state,
        "interval",
        hours=1,
        id="cleanup_stale_fsm_state",
        replace_existing=True,
        jobstore="memory",
    )
    # 4.4: written now so the healthcheck doesn't see a stale/missing file
    # during startup (Dockerfile's schema init, etc.), then kept fresh by
    # the periodic job below, but ONLY while a live Telegram connectivity
    # check keeps passing — see HeartbeatMonitor's docstring for why a
    # plain "still scheduling jobs" touch (the old behavior) isn't enough.
    _write_heartbeat()
    heartbeat_monitor = HeartbeatMonitor(bot, stop_event)
    scheduler.add_job(
        heartbeat_monitor.check_and_write,
        "interval",
        minutes=1,
        id="write_heartbeat",
        replace_existing=True,
        jobstore="memory",
    )
    logger.info("Scheduler configured.")

    # Middleware — whitelist intentionally disabled (open access is a
    # deliberate product choice), but open access with literally no cap
    # meant one user could flood the DB and the CPU-heavy, globally
    # locked NLP parser. RateLimitMiddleware goes first so a throttled
    # update never reaches the DB or the parser.
    # To re-enable the whitelist, restore WhitelistMiddleware registration
    # before DatabaseMiddleware too.
    # dp.update.middleware(WhitelistMiddleware(allowed_users=config.ALLOWED_USERS, admin_id=config.ADMIN_ID))
    # 2.6: PrivateChatOnlyMiddleware goes first of all — a rejected-outright
    # group/channel update shouldn't even count toward a user's rate-limit
    # window. No handler in this codebase filters on chat.type; without
    # this, a group the bot is in (or one with privacy mode off) hits the
    # exact same free-text-parses-as-task flow a DM does, notifying a
    # chat_id the member may never have opened with the bot.
    dp.update.middleware(PrivateChatOnlyMiddleware())
    rate_limit_middleware = RateLimitMiddleware()
    dp.update.middleware(rate_limit_middleware)
    dp.update.middleware(DatabaseMiddleware(session_pool=session_pool))
    # 3.1: RateLimitMiddleware's per-user hit dict never dropped an entry on
    # its own — a user who sent a handful of messages and never returned
    # left a permanent entry behind. Same cadence as cleanup_stale_timers.
    scheduler.add_job(
        rate_limit_middleware.cleanup_expired,
        "interval",
        minutes=10,
        id="cleanup_rate_limit_hits",
        replace_existing=True,
        jobstore="memory",
    )

    # Routers
    for router in all_routers:
        dp.include_router(router)

    # Inject services into handler kwargs
    dp.workflow_data.update({"scheduler_service": scheduler_service, "config": config})

    scheduler.start()
    await scheduler_service.reconcile_jobs_with_db()
    # 2.5: the jobstore is a cache derived from the DB, not the source of
    # truth — this is the symmetric "remove what shouldn't be there any
    # more" counterpart to reconcile_jobs_with_db's "add what's missing",
    # run once at startup and then hourly (see below).
    await scheduler_service.remove_orphan_scheduler_jobs()
    scheduler.add_job(
        remove_orphan_scheduler_jobs_job,
        "interval",
        hours=1,
        id="remove_orphan_scheduler_jobs",
        replace_existing=True,
        jobstore="memory",
    )

    # 4.4/4.6: aiohttp server for the .ics feed and (once MINI_APP_URL is
    # configured) the Mini App — runs alongside long polling, not instead
    # of it. See bot/services/webserver.py's module docstring for scope.
    # Opt-in via WEB_SERVER_ENABLED (see bot/config.py) — opening an HTTP
    # port is a deliberate deployment decision, not a side effect of
    # upgrading an existing install.
    web_runner = await _start_web_server_if_enabled(session_pool)
    if web_runner is not None:
        # 3.1: same reasoning as cleanup_rate_limit_hits above, applied to
        # the HTTP-route IP rate limiter (bot/services/webserver.py) —
        # without a periodic sweep its per-IP dict only ever grows.
        scheduler.add_job(
            web_runner.app[HTTP_RATE_LIMITER_KEY].cleanup_expired,
            "interval",
            minutes=10,
            id="cleanup_http_rate_limit_hits",
            replace_existing=True,
            jobstore="memory",
        )

    logger.info("Starting polling…")

    try:
        # See _start_polling_coro's docstring for why handle_signals=False
        # is required here, not optional.
        await _run_until_stopped(_start_polling_coro(dp, bot), stop_event)
    finally:
        # Stop the scheduler BEFORE closing the bot session/engine: a job
        # mid-send (a reminder, a brief) that races past this point would
        # otherwise hit a closed aiohttp session or a disposed engine.
        # wait=False still lets an in-flight async job coroutine that's
        # already running continue to completion (APScheduler cancels
        # only pending/scheduled runs, not one already executing) — it
        # just stops issuing NEW ones.
        scheduler.shutdown(wait=False)

        try:
            await bot.session.close()
        except Exception as e:
            logger.warning("Error closing Telegram session: %s", e)

        if web_runner is not None:
            try:
                await web_runner.cleanup()
            except Exception as e:
                logger.warning("Error shutting down web server: %s", e)

        try:
            await dispose_engine(engine)
        except Exception as e:
            logger.warning("Error disposing engine: %s", e)

        logger.info("Bot stopped cleanly.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped by user.")
