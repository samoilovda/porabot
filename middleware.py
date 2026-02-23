from typing import Callable, Dict, Any, Awaitable
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, User as TelegramUser
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from models import User
from loader import logger

class DbSessionMiddleware(BaseMiddleware):
    def __init__(self, session_pool: async_sessionmaker):
        super().__init__()
        self.session_pool = session_pool

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        async with self.session_pool() as session:
            data["session"] = session
            
            # Получаем User из event (если есть)
            tg_user: TelegramUser = data.get("event_from_user")
            
            if tg_user:
                try:
                    # Ищем или создаем пользователя в БД
                    # Используем scalar_one_or_none для оптимизации
                    result = await session.execute(select(User).where(User.id == tg_user.id))
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        user = User(
                            id=tg_user.id,
                            username=tg_user.username,
                            timezone="UTC" # Дефолтная зона
                        )
                        session.add(user)
                        await session.commit()
                        logger.info(f"New user created: {user.id} ({user.username})")
                    
                    data["user"] = user
                except Exception as e:
                    logger.error(f"Error getting/creating user in middleware: {e}", exc_info=True)
                    # Не блокируем обработку, но user может не попасть в handler?
                    # Если user обязателен, то handler упадет.
                    raise e
            
            return await handler(event, data)
