"""Single quiet-hours delivery decision for every scheduled message kind
(step 5, 2026-09-26 audit remediation).

Reminders, daily briefs, habit reports, and missed-task recovery used to
each decide quiet hours their own way — regular reminders defer (reschedule
the job for quiet hours' end), missed-recovery skips the tick entirely and
retries later, and habit_reports.py used to ignore quiet hours outright
(its own comment said so) because deferring a summary the user explicitly
scheduled would silently move it. Meanwhile README promised quiet hours
"suppress all notifications", which none of the summary kinds actually did.

Owner's decision (docs/plans/2026-09-26-audit-remediation.md): a scheduled
SUMMARY (brief/report) is delivered on time, every time, but SILENTLY
(disable_notification=True) inside quiet hours instead of suppressed or
deferred. A REMINDER or the missed-recovery digest keeps its existing
defer/skip behavior — unchanged, just decided through this one function
now instead of a bare is_quiet_hours() check at each call site.
"""

from datetime import datetime
from typing import Literal

from bot.utils.time_ext import is_quiet_hours

NotificationKind = Literal["reminder", "missed_recovery", "brief", "report"]
NotificationDecision = Literal["deliver", "silent", "suppress"]

# Only a SUMMARY the user scheduled for a specific time is delivered
# silently rather than suppressed — see module docstring.
_SILENCED_KINDS = frozenset({"brief", "report"})


def notification_policy(
    user, kind: NotificationKind, now_local: datetime, *, is_habit: bool = False
) -> NotificationDecision:
    """Decide how to deliver *kind* to *user* right now.

    Returns:
        "deliver":  outside quiet hours — send normally.
        "silent":   inside quiet hours, kind is a summary — send anyway,
                    with disable_notification=True.
        "suppress": inside quiet hours, kind is a reminder or the missed-
                    recovery digest — caller must defer or skip, not send.
    """
    if not is_quiet_hours(user, now_local, is_habit=is_habit):
        return "deliver"
    return "silent" if kind in _SILENCED_KINDS else "suppress"
