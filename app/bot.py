import logging
import sys
import instaloader
import os
from pathlib import Path
import random
import asyncio

from aiogram import Bot, Dispatcher, F
from aiogram.types import BufferedInputFile, Message

from settings import settings
from tiktok.api import TikTokAPI
from urllib.parse import urlparse

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Initializing bot dispatcher...")

# Initialize dispatcher only (bot is initialized in main.py)
dp = Dispatcher()

# Initialize Instagram loader with login if credentials are provided
insta_loader = instaloader.Instaloader(
    download_videos=True,
    download_video_thumbnails=False,
    download_geotags=False,
    download_comments=False,
    save_metadata=False,
    compress_json=False,
    max_connection_attempts=3,
    request_timeout=15,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    quiet=True
)

# Try to login to Instagram if credentials are provided
if hasattr(settings, 'instagram_username') and hasattr(settings, 'instagram_password'):
    try:
        logger.info("Logging into Instagram...")
        session_file = Path("session-" + settings.instagram_username)

        # Try to load session
        if session_file.exists():
            logger.info("Loading existing session...")
            insta_loader.load_session_from_file(
                settings.instagram_username, session_file)
        else:
            logger.info("No existing session found, logging in...")
            insta_loader.login(settings.instagram_username,
                               settings.instagram_password)
            # Save session for future use
            insta_loader.save_session_to_file(session_file)

        logger.info("Successfully logged into Instagram")
    except Exception as e:
        logger.error(f"Failed to login to Instagram: {e}")
else:
    logger.warning(
        "No Instagram credentials provided. Some features might be limited.")

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

    logger.info(f"Processing Tiktok link: {urls[0]}")

    async for tiktok in TikTokAPI.download_tiktoks(urls):
        if not tiktok.video:
            continue

        await message.answer("Tiktok Link Processing...", reply_to_message_id=message.message_id)

        video = BufferedInputFile(tiktok.video, filename="video.mp4")
        caption = tiktok.caption if settings.with_captions else None

        logger.info(f"Sending TikTok video to chat ID: {message.chat.id}")

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

    await message.answer("IG Link Processing...", reply_to_message_id=message.message_id)

    for url in urls:
        try:
            # Parse the URL to get the path
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.strip("/").split("/")

            # Check if the URL path is valid and contains 'reel' followed by a shortcode
            if len(path_parts) >= 2 and path_parts[0] == "reel":
                shortcode = path_parts[1]
            else:
                logger.warning(f"Invalid Instagram reel URL: {url}")
                await message.reply("Invalid Instagram reel URL. Please send a valid reel link.")
                continue

            # Add more random delay between 2-5 seconds before each request
            await asyncio.sleep(random.uniform(2, 5))

            # Construct the target directory path using pathlib
            target_dir = Path("downloads") / f"instagram_{shortcode}"
            logger.info(f"Creating directory: {target_dir}")
            # Create the directory if it doesn't exist
            target_dir.mkdir(parents=True, exist_ok=True)

            try:
                # Load the post using the shortcode with retry logic
                max_retries = 3
                retry_count = 0
                while retry_count < max_retries:
                    try:
                        post = instaloader.Post.from_shortcode(
                            insta_loader.context, shortcode)
                        break
                    except (instaloader.exceptions.ConnectionException,
                            instaloader.exceptions.BadResponseException) as e:
                        retry_count += 1
                        if retry_count == max_retries:
                            raise
                        # Increase delay between retries (exponential backoff)
                        delay = random.uniform(3, 7) * retry_count
                        logger.warning(
                            f"Retry {retry_count}/{max_retries} after error: {e}. Waiting {delay:.1f}s")
                        await asyncio.sleep(delay)

                # Download the post
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

                    logger.info(
                        f"Sending Instagram video to chat ID: {message.chat.id}")

                    if settings.reply_to_message:
                        await message.reply_video(video=video)
                    else:
                        await bot.send_video(chat_id=message.chat.id, video=video)
                else:
                    logger.warning(f"No video file found in {target_dir}")
                    await message.reply("Sorry, couldn't find a video in this Instagram post.")

            except instaloader.exceptions.ConnectionException as e:
                if "429" in str(e):
                    logger.error("Rate limited by Instagram")
                    await message.reply("Instagram is rate limiting us. Please try again in a few minutes.")
                elif "401" in str(e):
                    logger.error("Instagram authentication required")
                    # Try to refresh the session
                    try:
                        logger.info(
                            "Attempting to refresh Instagram session...")
                        insta_loader.login(
                            settings.instagram_username, settings.instagram_password)
                        await message.reply("Please try sending the reel again, refreshed authentication.")
                    except Exception as login_error:
                        logger.error(
                            f"Failed to refresh session: {login_error}")
                        await message.reply("This reel requires authentication. Please try another reel or contact the bot administrator.")
                else:
                    logger.exception(f"Instagram connection error: {e}")
                    await message.reply("Error connecting to Instagram. Please try again later.")
            except Exception as e:
                if "403" in str(e):
                    logger.error(f"Instagram access forbidden: {e}")
                    await message.reply("Sorry, Instagram is currently blocking our requests. Please try again in a few minutes.")
                elif "401" in str(e):
                    logger.error("Instagram authentication required")
                    # Try to refresh the session
                    try:
                        logger.info(
                            "Attempting to refresh Instagram session...")
                        insta_loader.login(
                            settings.instagram_username, settings.instagram_password)
                        await message.reply("Please try sending the reel again, refreshed authentication.")
                    except Exception as login_error:
                        logger.error(
                            f"Failed to refresh session: {login_error}")
                        await message.reply("This reel requires authentication. Please try another reel or contact the bot administrator.")
                else:
                    logger.exception(f"Error processing Instagram reel: {e}")
                    await message.reply("Sorry, there was an error processing your Instagram link.")

            # Cleanup: Remove downloaded files after sending
            try:
                for file in os.listdir(target_dir):
                    os.remove(os.path.join(target_dir, file))
                os.rmdir(target_dir)
            except Exception as e:
                logger.error(f"Error cleaning up directory {target_dir}: {e}")

        except Exception as e:
            logger.exception(f"Error downloading Instagram video: {e}")
            await message.reply("Sorry, there was an error processing your Instagram link.")
