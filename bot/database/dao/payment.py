"""PaymentDAO — data access for the Payment model (A-19).

Reused BaseDAO.create() covers recording a payment; the one thing worth a
dedicated method here is looking one back up for /paysupport.
"""

from typing import Optional, Sequence

from sqlalchemy import select

from bot.database.dao.base import BaseDAO
from bot.database.models import Payment


class PaymentDAO(BaseDAO[Payment]):
    model = Payment

    async def get_recent_for_user(self, user_id: int, limit: int = 5) -> Sequence[Payment]:
        """Most recent payments first — /paysupport shows these so the
        user can pick which one (if more than one) to request a refund
        for."""
        result = await self.session.execute(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc())
            .limit(max(1, int(limit)))
        )
        return result.scalars().all()

    async def get_owned(self, payment_id: int, user_id: int) -> Optional[Payment]:
        """Same IDOR-prevention shape as ReminderDAO.get_owned — a
        /paysupport refund button must only ever act on the tapping
        user's own payment."""
        payment = await self.get_by_id(payment_id)
        if payment is None or payment.user_id != user_id:
            return None
        return payment
