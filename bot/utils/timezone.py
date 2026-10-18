"""Canonical IANA or fixed UTC-offset timezone resolution."""

import re

import pytz


def resolve_timezone(name):
    match = re.fullmatch(r"UTC([+-])(\d{2}):(\d{2})", name or "")
    if not match:
        return pytz.timezone(name)
    sign, hours, minutes = match.groups()
    total = int(hours) * 60 + int(minutes)
    signed = total if sign == "+" else -total
    if int(minutes) > 59 or signed < -720 or signed > 840:
        raise pytz.UnknownTimeZoneError(name)
    tz = pytz.FixedOffset(signed)
    if signed:
        tz.zone = name
    return tz
