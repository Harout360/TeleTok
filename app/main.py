import asyncio
import logging
import sys

from aiogram import Bot

from bot import dp
from settings import settings

logger = logging.getLogger(__name__)


async def start() -> None:
    logger.info("Starting Telegram bot...")
    bot = Bot(token=settings.api_token)

    try:
        logger.info("Bot is running. Waiting for messages...")
        await dp.start_polling(bot)
    except Exception as e:
        logger.exception(f"Error running bot: {e}")
        raise


if __name__ == "__main__":
    try:
        asyncio.run(start())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
    except Exception as e:
        logger.exception(f"Fatal error: {e}")
        sys.exit(1)
