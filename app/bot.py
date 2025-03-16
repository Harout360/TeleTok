import logging
import sys
import instaloader
import os
from pathlib import Path
import random
import asyncio
import tempfile
import shutil
from video_processor import process_video_file  # Import from new module

from aiogram import Bot, Dispatcher, F
from aiogram.types import BufferedInputFile, Message
from aiogram.enums import ParseMode

from settings import settings
from tiktok.api import TikTokAPI
from urllib.parse import urlparse

# Butler-style processing messages
INSTAGRAM_BUTLER_MESSAGES = [
    "🎩 Right away, sir! Fetching your Instagram reel...",
    "🧐 Splendid choice! Allow me to retrieve that for you...",
    "🎬 Ah, excellent taste! One moment while I prepare your video...",
    "🎭 With pleasure! Acquiring your entertainment posthaste...",
    "🎪 Most certainly! Your video shall arrive momentarily...",
    "🎠 Delighted to assist! Fetching your content with utmost haste..."
]

TIKTOK_BUTLER_MESSAGES = [
    "🎩 Ah, TikTok! I shall fetch that for you promptly...",
    "🧐 A TikTok request! Allow me to retrieve that masterpiece...",
    "🎬 Excellent choice of TikTok! One moment, if you please...",
    "🎭 With pleasure! Your TikTok video shall arrive shortly...",
    "🎪 Splendid TikTok selection! Processing with utmost care...",
    "🎠 Right away! Preparing your TikTok entertainment..."
]

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
    max_connection_attempts=5,
    request_timeout=30,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    quiet=True
)

# Try to login to Instagram if credentials are provided
if hasattr(settings, 'instagram_username') and hasattr(settings, 'instagram_password'):
    async def login_to_instagram(force_new=False):
        try:
            logger.info("Logging into Instagram...")
            session_file = Path("session-" + settings.instagram_username)

            if not force_new and session_file.exists():
                logger.info("Loading existing session...")
                try:
                    insta_loader.load_session_from_file(
                        settings.instagram_username, session_file)
                    logger.info("Successfully loaded existing session")
                except Exception as e:
                    logger.warning(f"Failed to load existing session: {e}")
                    return await login_to_instagram(force_new=True)
            else:
                if session_file.exists():
                    session_file.unlink()  # Remove old session file
                logger.info("Creating new session...")
                insta_loader.login(settings.instagram_username,
                                   settings.instagram_password)
                insta_loader.save_session_to_file(session_file)
                logger.info("Successfully created new session")

            return True
        except Exception as e:
            logger.error(f"Failed to login to Instagram: {e}")
            if session_file.exists():
                session_file.unlink()  # Remove failed session file
            return False

    # Create startup handler to initialize Instagram login
    @dp.startup()
    async def on_startup():
        await login_to_instagram()
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
            logger.warning(f"No video data found for TikTok URL: {urls[0]}")
            continue

        processing_msg = await message.answer(random.choice(TIKTOK_BUTLER_MESSAGES), reply_to_message_id=message.message_id)

        try:
            # Process video to maintain aspect ratio
            processed_video, width, height = await process_video_file(tiktok.video, "tiktok_video.mp4")
            video = BufferedInputFile(processed_video, filename="video.mp4")
            caption = tiktok.caption if settings.with_captions else None

            logger.info(
                f"Sending TikTok video to chat ID: {message.chat.id} with dimensions {width}x{height}")

            if settings.reply_to_message:
                await message.reply_video(
                    video=video,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    width=width,
                    height=height,
                    supports_streaming=True
                )
            else:
                await bot.send_video(
                    chat_id=message.chat.id,
                    video=video,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    width=width,
                    height=height,
                    supports_streaming=True
                )

        except Exception as e:
            logger.error(f"Failed to process TikTok video: {e}")
            await message.reply("🎭 My sincerest apologies, but I encountered difficulties processing this TikTok video.")
        finally:
            await processing_msg.delete()


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

    processing_msg = await message.answer(random.choice(INSTAGRAM_BUTLER_MESSAGES), reply_to_message_id=message.message_id)
    start_time = asyncio.get_event_loop().time()
    TIMEOUT_SECONDS = 120  # 2 minutes timeout

    for url in urls:
        try:
            logger.info(f"Starting to process Instagram URL: {url}")
            # Parse the URL to get the path
            parsed_url = urlparse(url)
            path_parts = parsed_url.path.strip("/").split("/")

            # Check if the URL path is valid and contains 'reel' followed by a shortcode
            if len(path_parts) >= 2 and path_parts[0] == "reel":
                shortcode = path_parts[1]
                logger.info(f"Extracted shortcode: {shortcode}")
            else:
                logger.warning(f"Invalid Instagram reel URL: {url}")
                await message.reply("Invalid Instagram reel URL. Please send a valid reel link.")
                continue

            # Load the post using the shortcode with improved retry logic
            max_retries = 5
            retry_count = 0
            last_error = None
            post = None

            while retry_count < max_retries and post is None:
                try:
                    if asyncio.get_event_loop().time() - start_time > TIMEOUT_SECONDS:
                        raise TimeoutError("Processing took too long")

                    if retry_count > 0:
                        logger.info(
                            f"Attempting retry {retry_count}/{max_retries}")
                        await asyncio.sleep(2 ** retry_count)

                    logger.info(
                        f"Fetching post data for shortcode: {shortcode}")
                    post = instaloader.Post.from_shortcode(
                        insta_loader.context, shortcode)
                    logger.info("Successfully fetched post data")

                except instaloader.exceptions.ConnectionException as e:
                    retry_count += 1
                    last_error = e
                    logger.warning(
                        f"Retry {retry_count}/{max_retries} after connection error: {e}")
                    if retry_count == max_retries:
                        raise
                except instaloader.exceptions.BadResponseException as e:
                    if "login_required" in str(e):
                        logger.info(
                            "Session expired, attempting to refresh...")
                        if await login_to_instagram(force_new=True):
                            retry_count += 1
                            continue
                    retry_count += 1
                    last_error = e
                    logger.warning(
                        f"Retry {retry_count}/{max_retries} after bad response: {e}")
                    if retry_count == max_retries:
                        raise

            if post is None:
                raise Exception("Failed to fetch post data after all retries")

            # Create a temporary directory that will be automatically cleaned up
            with tempfile.TemporaryDirectory() as temp_dir:
                if asyncio.get_event_loop().time() - start_time > TIMEOUT_SECONDS:
                    raise TimeoutError("Processing took too long")

                logger.info(
                    f"Downloading post to temporary directory: {temp_dir}")
                insta_loader.dirname_pattern = temp_dir
                insta_loader.download_post(post, target=shortcode)
                logger.info("Post download completed")

                # Find the video file in the temporary directory
                video_path = next(
                    (os.path.join(root, f)
                     for root, _, files in os.walk(temp_dir)
                     for f in files if f.endswith('.mp4')),
                    None
                )

                if not video_path:
                    logger.warning(f"No video file found for post {shortcode}")
                    await processing_msg.delete()
                    await message.reply("Sorry, couldn't find a video in this Instagram post.")
                    continue

                logger.info(f"Found video file at: {video_path}")
                with open(video_path, 'rb') as video_file:
                    video_data = video_file.read()

                logger.info("Processing video file...")
                processed_video, width, height = await process_video_file(video_data, "instagram_video.mp4")
                video = BufferedInputFile(
                    processed_video, filename="insta_video.mp4")
                caption = post.caption if settings.with_captions else None

                logger.info(
                    f"Sending Instagram video to chat ID: {message.chat.id} with dimensions {width}x{height}")

                try:
                    if settings.reply_to_message:
                        await message.reply_video(
                            video=video,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            width=width,
                            height=height,
                            supports_streaming=True
                        )
                    else:
                        await bot.send_video(
                            chat_id=message.chat.id,
                            video=video,
                            caption=caption,
                            parse_mode=ParseMode.HTML,
                            width=width,
                            height=height,
                            supports_streaming=True
                        )
                    logger.info("Successfully sent video to user")
                except Exception as e:
                    logger.error(f"Failed to send video: {e}")
                    raise

                await processing_msg.delete()

        except TimeoutError:
            logger.error(
                f"Processing timed out after {TIMEOUT_SECONDS} seconds")
            await processing_msg.delete()
            await message.reply("Sorry, the request timed out. Please try again later.")
        except Exception as e:
            logger.exception(f"Error downloading Instagram video: {e}")
            await processing_msg.delete()
            await message.reply(f"Sorry, there was an error processing your Instagram link: {str(e)}")
