import logging
import sys
import instaloader
import os
from pathlib import Path
import random
import asyncio
import tempfile
import shutil
import ffmpeg  # We'll need this for video processing

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


async def process_video_file(video_data: bytes, filename: str) -> tuple[bytes, int, int]:
    """Process video to ensure correct aspect ratio and format for Telegram.
    Returns tuple of (processed_video_bytes, width, height)"""
    with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_in:
        temp_in.write(video_data)
        temp_in.flush()

        # Create a temporary output file
        temp_out = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        temp_out.close()

        try:
            # Get video information
            probe = ffmpeg.probe(temp_in.name)
            video_info = next(
                s for s in probe['streams'] if s['codec_type'] == 'video')
            width = int(video_info['width'])
            height = int(video_info['height'])

            # Process video while maintaining aspect ratio
            stream = ffmpeg.input(temp_in.name)
            stream = ffmpeg.output(
                stream,
                temp_out.name,
                vcodec='h264',
                acodec='aac',
                format='mp4',      # Explicitly set container format
                video_bitrate='2M',
                audio_bitrate='128k',
                vf=f'scale={width}:{height}:force_original_aspect_ratio=decrease',
                strict='experimental'
            )

            ffmpeg.run(stream, capture_stdout=True,
                       capture_stderr=True, overwrite_output=True)

            # Read the processed video
            with open(temp_out.name, 'rb') as f:
                return f.read(), width, height
        finally:
            # Clean up temporary files
            os.unlink(temp_in.name)
            os.unlink(temp_out.name)


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
                        logger.warning(
                            f"Retry {retry_count}/{max_retries} after error: {e}")

                # Create a temporary directory that will be automatically cleaned up
                with tempfile.TemporaryDirectory() as temp_dir:
                    # Download the post to the temporary directory
                    insta_loader.dirname_pattern = temp_dir
                    insta_loader.download_post(post, target=shortcode)

                    # Find the video file in the temporary directory
                    video_path = next(
                        (os.path.join(root, f)
                         for root, _, files in os.walk(temp_dir)
                         for f in files if f.endswith('.mp4')),
                        None
                    )

                    if video_path:
                        with open(video_path, 'rb') as video_file:
                            video_data = video_file.read()

                        # Process video to maintain aspect ratio
                        processed_video, width, height = await process_video_file(video_data, "instagram_video.mp4")
                        video = BufferedInputFile(
                            processed_video, filename="insta_video.mp4")
                        caption = post.caption if settings.with_captions else None

                        logger.info(
                            f"Sending Instagram video to chat ID: {message.chat.id} with dimensions {width}x{height}")

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

                        await processing_msg.delete()
                    else:
                        logger.warning(
                            f"No video file found for post {shortcode}")
                        await processing_msg.delete()
                        await message.reply("Sorry, couldn't find a video in this Instagram post.")

            except instaloader.exceptions.ConnectionException as e:
                if "429" in str(e):
                    logger.error("Rate limited by Instagram")
                    await processing_msg.delete()
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
                    await processing_msg.delete()
                else:
                    logger.exception(f"Instagram connection error: {e}")
                    await processing_msg.delete()
                    await message.reply("Error connecting to Instagram. Please try again later.")
            except Exception as e:
                if "403" in str(e):
                    logger.error(f"Instagram access forbidden: {e}")
                    await processing_msg.delete()
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
                    await processing_msg.delete()
                else:
                    logger.exception(f"Error processing Instagram reel: {e}")
                    await processing_msg.delete()
                    await message.reply("Sorry, there was an error processing your Instagram link.")

        except Exception as e:
            logger.exception(f"Error downloading Instagram video: {e}")
            await processing_msg.delete()
            await message.reply("Sorry, there was an error processing your Instagram link.")
