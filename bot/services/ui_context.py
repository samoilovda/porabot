"""Durable, expiring contexts keep long queries and batch IDs off Telegram buttons."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from bot.database.models import UiContext


def now_utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_context(session, user_id, kind, payload, *, lifetime=timedelta(hours=24)):
    await session.execute(delete(UiContext).where(UiContext.expires_at <= now_utc()))
    context = UiContext(
        token=secrets.token_hex(6),
        user_id=user_id,
        kind=kind,
        payload_json=json.dumps(payload, ensure_ascii=False),
        expires_at=now_utc() + lifetime,
    )
    session.add(context)
    await session.commit()
    return context


async def get_context(session, token, user_id, kind=None):
    context = await session.scalar(
        select(UiContext).where(
            UiContext.token == token, UiContext.user_id == user_id, UiContext.expires_at > now_utc()
        )
    )
    return context if context and (kind is None or kind == context.kind) else None
