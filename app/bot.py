import logging
import sys
import instaloader
import os

from aiogram import Bot, Dispatcher, F
from aiogram.types import BufferedInputFile, Message

from settings import settings
from tiktok.api import TikTokAPI
from urllib.parse import urlparse

logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)
logger.info("Bot started...")
print("Waiting for TikTok messages")

dp = Dispatcher()

tiktokFilters = [
    F.text.contains("tiktok.com"),
    (not settings.allowed_ids)
    | F.chat.id.in_(settings.allowed_ids)
    | F.from_user.id.in_(settings.allowed_ids),
]


@dp.message(*tiktokFilters)
@dp.channel_post(*tiktokFilters)
async def handle_tiktok_request(message: Message, bot: Bot) -> None:
    entries = [
        message.text[e.offset: e.offset + e.length]
        for e in message.entities or []
        if message.text is not None
    ]

    urls = [
        u if u.startswith("http") else f"https://{u}"
        for u in filter(lambda e: "tiktok.com" in e, entries)
    ]

    async for tiktok in TikTokAPI.download_tiktoks(urls):
        if not tiktok.video:
            continue

        video = BufferedInputFile(tiktok.video, filename="video.mp4")
        caption = tiktok.caption if settings.with_captions else None

        logger.info(f"Sending message to chat ID: {message.chat.id}")

        if settings.reply_to_message:
            await message.reply_video(video=video, caption=caption)
        else:
            await bot.send_video(chat_id=message.chat.id, video=video, caption=caption)


# IG

igFilters = [
    F.text.contains("instagram.com"),
    (not settings.allowed_ids)
    | F.chat.id.in_(settings.allowed_ids)
    | F.from_user.id.in_(settings.allowed_ids),
]

insta_loader = instaloader.Instaloader()


@dp.message(*igFilters)
@dp.channel_post(*igFilters)
async def handle_instagram_request(message: Message, bot: Bot) -> None:
    entries = [
        message.text[e.offset: e.offset + e.length]
        for e in message.entities or []
        if message.text is not None
    ]

    urls = [
        u if u.startswith("http") else f"https://{u}"
        for u in filter(lambda e: "instagram.com" in e, entries)
    ]

    for url in urls:
        try:
            # Parse the URL to get the path
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.strip("/").split("/")

            # Check if the URL path is valid and contains 'reel' followed by a shortcode
            if len(path_parts) >= 2 and path_parts[0] == "reel":
                shortcode = path_parts[1]
            else:
                logger.error(f"Invalid Instagram reel URL: {url}")
                continue

            post = instaloader.Post.from_shortcode(
                insta_loader.context, shortcode)

            # Define the directory to download the post to
            target_dir = f"downloads/instagram_{shortcode}"
            os.makedirs(target_dir, exist_ok=True)

            # Download the post, which includes video and other media
            insta_loader.download_post(post, target_dir)

            # Find the video file in the download directory
            video_file = next(
                (f for f in os.listdir(target_dir) if f.endswith(".mp4")), None
            )

            if video_file:
                video_path = os.path.join(target_dir, video_file)
                with open(video_path, 'rb') as video_data:
                    video = BufferedInputFile(
                        video_data.read(), filename="insta_video.mp4")

                logger.info(f"Sending Instagram video to chat ID: {
                            message.chat.id}")

                if settings.reply_to_message:
                    await message.reply_video(video=video)
                else:
                    await bot.send_video(chat_id=message.chat.id, video=video)

            # Cleanup: Remove downloaded files after sending
            for file in os.listdir(target_dir):
                os.remove(os.path.join(target_dir, file))
            os.rmdir(target_dir)

        except Exception as e:
            logger.error(f"Error downloading Instagram video: {e}")
