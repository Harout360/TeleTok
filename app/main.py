import asyncio
import logging
import sys

from aiogram import Bot

from bot import dp
from settings import settings

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

async def start() -> None:
    bot = Bot(token=settings.api_token)
    # logger = logging.getLogger(__name__)
    # logger.info("Bot started...")
    # logger.error("Bot started for sure no errors just checking logger...")
    # print("Waiting for TikTok messages")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(start())
