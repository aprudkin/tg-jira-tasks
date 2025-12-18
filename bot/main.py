import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.handlers import tasks
from bot.middlewares.auth import AuthMiddleware
from bot.services.notifications import notification_service

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Точка входа в приложение."""
    # Создание бота с настройками по умолчанию
    bot = Bot(
        token=settings.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()

    # Регистрация middleware и роутеров
    dp.message.middleware(AuthMiddleware())
    dp.include_router(tasks.router)

    # Запуск сервиса уведомлений
    notification_service.start(bot)

    logger.info("Starting bot...")
    try:
        await dp.start_polling(bot)
    finally:
        # Остановка сервиса уведомлений
        await notification_service.stop()
        # Корректное завершение сессии бота
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
