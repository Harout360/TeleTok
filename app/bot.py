import logging
import sys
import instaloader
import os
from pathlib import Path
import html
import random
import re
import asyncio
import tempfile
import shutil
from contextlib import suppress
from video_processor import process_video_file  # Import from new module

from aiogram import Bot, Dispatcher, F
from aiogram.types import (
    BufferedInputFile,
    InputMediaPhoto,
    InputMediaVideo,
    Message,
)
from aiogram.enums import ParseMode

import instaloader_patch
from settings import settings
from tiktok.api import TikTokAPI
from urllib.parse import urlparse

# Butler-style processing messages
INSTAGRAM_BUTLER_MESSAGES = [
    "🎩 Another one? Very well. Fetching it, since you clearly can't...",
    "🧐 Riveting. I'll retrieve this for you, as is apparently my lot in life...",
    "🎬 Ah yes, THIS post. Truly the pinnacle of human achievement. Downloading...",
    "🎭 Say no more. Actually, please do say less. Working on it...",
    "🎪 One Instagram post, coming right up. Do try to contain your excitement...",
    "🎠 I live to serve, allegedly. Fetching your little video..."
]

TIKTOK_BUTLER_MESSAGES = [
    "🎩 TikTok. Naturally. Fetching it before you ask again...",
    "🧐 Ah, TikTok — for when Instagram was too intellectual. Retrieving...",
    "🎬 I'll get it. I always get it. That's the whole arrangement, isn't it...",
    "🎭 Another TikTok for the collection. Your standards remain consistent...",
    "🎪 Downloading. Please enjoy responsibly, or however you enjoy these things...",
    "🎠 Right. Fetching your TikTok. This is what I trained for, apparently..."
]

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Initializing bot dispatcher...")

# Instagram's current API breaks instaloader 4.15.2; see app/instaloader_patch.py
instaloader_patch.apply()

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
    # The i.instagram.com endpoint used to fetch higher-quality video rejects web-session
    # cookies with "login_required", so every download burned ~8s on doomed retries before
    # falling back to the standard video. Skip straight to the fallback.
    iphone_support=False,
    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    quiet=True
)

# Try to login to Instagram if credentials are provided.
# Note: settings is a dataclass, so hasattr() is always True here - check the values.
if settings.instagram_username and settings.instagram_password:
    # Lives on a volume (see compose.yaml) so the session outlives the container.
    session_file = Path(settings.session_dir) / f"session-{settings.instagram_username}"

    async def login_to_instagram(force_new=False):
        try:
            logger.info("Logging into Instagram...")
            session_file.parent.mkdir(parents=True, exist_ok=True)

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
                logger.info(f"Successfully created new session at {session_file}")

            return True
        except Exception as e:
            logger.error(f"Failed to login to Instagram: {e}")
            # Don't leave a half-written session behind for the next start to load.
            session_file.unlink(missing_ok=True)
            return False

    # Create startup handler to initialize Instagram login
    @dp.startup()
    async def on_startup():
        await login_to_instagram()
else:
    logger.warning(
        "No Instagram credentials provided. Some features might be limited.")

# Path segments that precede a shortcode in an Instagram media URL. Instagram serves the
# same content under several of these: /p/ for posts (including reels shared as posts),
# /reel/ and /reels/ for reels, /tv/ for legacy IGTV.
INSTAGRAM_MEDIA_PATHS = {"p", "reel", "reels", "tv"}


def extract_shortcode(url: str) -> str | None:
    """Pull the shortcode out of an Instagram media URL, or None if there isn't one.

    Handles the profile-scoped forms (/<username>/reel/<shortcode>/) as well as the bare
    ones by scanning for a known media segment rather than assuming it comes first.
    """
    path_parts = [part for part in urlparse(url).path.split("/") if part]
    for index, part in enumerate(path_parts[:-1]):
        if part in INSTAGRAM_MEDIA_PATHS:
            return path_parts[index + 1]
    return None


# Extensions instaloader writes that we actually want to forward. It also drops a
# .txt caption and a .json.xz metadata file next to them, which we ignore.
PHOTO_EXTENSIONS = (".jpg", ".jpeg")
VIDEO_EXTENSIONS = (".mp4",)

# Telegram's limits: 10 items per album, 1024 characters per caption.
MEDIA_GROUP_LIMIT = 10
CAPTION_LIMIT = 1024


def _natural_sort_key(path: str) -> list:
    """Sort key that compares digit runs numerically.

    Instaloader names carousel items <target>_1.jpg, <target>_2.mp4, ... and a plain
    lexicographic sort would put _10 before _2. Carousels go up to 20 items, so the
    post's original order depends on this.
    """
    return [
        int(chunk) if chunk.isdigit() else chunk
        for chunk in re.split(r"(\d+)", os.path.basename(path))
    ]


def collect_media_files(directory: str) -> list[str]:
    """Return every photo/video instaloader downloaded, in the post's original order."""
    media = [
        os.path.join(root, f)
        for root, _, files in os.walk(directory)
        for f in files
        if f.lower().endswith(PHOTO_EXTENSIONS + VIDEO_EXTENSIONS)
    ]
    return sorted(media, key=_natural_sort_key)


def build_caption(post) -> str | None:
    """Escape and truncate a post caption so Telegram will accept it.

    We send with parse_mode=HTML, so a caption containing < or & would otherwise make
    Telegram reject the whole message.
    """
    if not settings.with_captions or not post.caption:
        return None

    caption = html.escape(post.caption)
    if len(caption) > CAPTION_LIMIT:
        caption = caption[:CAPTION_LIMIT - 1]
        # Truncating can slice an escaped entity in half (&amp; -> &am), which is
        # invalid HTML and would be rejected. Drop any trailing partial entity.
        caption = re.sub(r"&[#a-zA-Z0-9]*$", "", caption) + "…"
    return caption


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

    if not urls:
        return

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
            with suppress(Exception):
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

    if not urls:
        return

    processing_msg = await message.answer(random.choice(INSTAGRAM_BUTLER_MESSAGES), reply_to_message_id=message.message_id)
    start_time = asyncio.get_event_loop().time()
    TIMEOUT_SECONDS = 120  # 2 minutes timeout

    try:
        await _process_instagram_urls(urls, message, bot, start_time, TIMEOUT_SECONDS)
    finally:
        # Always clear the "fetching..." placeholder, on success or on any error path.
        # Deletion can legitimately fail (already gone, or older than Telegram allows).
        with suppress(Exception):
            await processing_msg.delete()


async def _send_single_photo(path: str, caption: str | None,
                             message: Message, bot: Bot) -> None:
    with open(path, 'rb') as photo_file:
        photo = BufferedInputFile(photo_file.read(), filename="insta_photo.jpg")

    logger.info(f"Sending Instagram photo to chat ID: {message.chat.id}")
    if settings.reply_to_message:
        await message.reply_photo(
            photo=photo, caption=caption, parse_mode=ParseMode.HTML)
    else:
        await bot.send_photo(
            chat_id=message.chat.id, photo=photo, caption=caption,
            parse_mode=ParseMode.HTML)


async def _send_single_video(path: str, caption: str | None,
                             message: Message, bot: Bot) -> None:
    with open(path, 'rb') as video_file:
        video_data = video_file.read()

    logger.info("Processing video file...")
    processed_video, width, height = await process_video_file(
        video_data, "instagram_video.mp4")
    video = BufferedInputFile(processed_video, filename="insta_video.mp4")

    logger.info(
        f"Sending Instagram video to chat ID: {message.chat.id} with dimensions {width}x{height}")
    if settings.reply_to_message:
        await message.reply_video(
            video=video, caption=caption, parse_mode=ParseMode.HTML,
            width=width, height=height, supports_streaming=True)
    else:
        await bot.send_video(
            chat_id=message.chat.id, video=video, caption=caption,
            parse_mode=ParseMode.HTML, width=width, height=height,
            supports_streaming=True)


async def _build_media_group_item(path: str, index: int, caption: str | None):
    """Wrap one downloaded file as an album item, re-encoding videos if needed."""
    with open(path, 'rb') as media_file:
        data = media_file.read()

    if path.lower().endswith(VIDEO_EXTENSIONS):
        processed_video, width, height = await process_video_file(
            data, os.path.basename(path))
        return InputMediaVideo(
            media=BufferedInputFile(
                processed_video, filename=f"insta_video_{index}.mp4"),
            caption=caption,
            parse_mode=ParseMode.HTML if caption else None,
            width=width,
            height=height,
            supports_streaming=True,
        )

    return InputMediaPhoto(
        media=BufferedInputFile(data, filename=f"insta_photo_{index}.jpg"),
        caption=caption,
        parse_mode=ParseMode.HTML if caption else None,
    )


async def send_instagram_media(media_files: list[str], caption: str | None,
                               message: Message, bot: Bot) -> None:
    """Send a post's media: a single photo/video, or a carousel as album(s)."""
    if len(media_files) == 1:
        path = media_files[0]
        if path.lower().endswith(VIDEO_EXTENSIONS):
            await _send_single_video(path, caption, message, bot)
        else:
            await _send_single_photo(path, caption, message, bot)
        return

    # Only the very first item carries the caption - that is how Telegram renders a
    # caption for the album as a whole.
    items = [
        await _build_media_group_item(path, index, caption if index == 0 else None)
        for index, path in enumerate(media_files)
    ]

    # Telegram allows at most 10 items per album, so longer carousels go out as
    # consecutive albums.
    for chunk_start in range(0, len(items), MEDIA_GROUP_LIMIT):
        chunk = items[chunk_start:chunk_start + MEDIA_GROUP_LIMIT]

        logger.info(
            f"Sending album of {len(chunk)} item(s) to chat ID: {message.chat.id}")
        await bot.send_media_group(
            chat_id=message.chat.id,
            media=chunk,
            reply_to_message_id=(
                message.message_id if settings.reply_to_message else None),
        )


async def _process_instagram_urls(urls, message: Message, bot: Bot,
                                  start_time: float, TIMEOUT_SECONDS: int) -> None:
    for url in urls:
        try:
            logger.info(f"Starting to process Instagram URL: {url}")
            shortcode = extract_shortcode(url)
            if shortcode:
                logger.info(f"Extracted shortcode: {shortcode}")
            else:
                logger.warning(f"Unrecognized Instagram URL: {url}")
                await message.reply("That doesn't look like an Instagram post or reel link.")
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
                except TypeError as e:
                    # Instagram returned an empty/invalid response, usually because
                    # the session is expired or unauthenticated (rate-limited).
                    if "NoneType" in str(e):
                        logger.warning(
                            "Instagram returned an empty response, session may be invalid. "
                            "Attempting to refresh login...")
                        await login_to_instagram(force_new=True)
                        retry_count += 1
                        last_error = e
                        if retry_count == max_retries:
                            raise
                        continue
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

                # Collect every photo/video in the post, not just the first video -
                # a post can be a single photo, a single video, or a carousel of both.
                media_files = collect_media_files(temp_dir)

                if not media_files:
                    logger.warning(f"No media files found for post {shortcode}")
                    await message.reply("Sorry, couldn't find any media in this Instagram post.")
                    continue

                logger.info(
                    f"Found {len(media_files)} media file(s) for post {shortcode}")
                caption = build_caption(post)

                try:
                    await send_instagram_media(
                        media_files, caption, message, bot)
                    logger.info("Successfully sent Instagram media to user")
                except Exception as e:
                    logger.error(f"Failed to send Instagram media: {e}")
                    raise


        except TimeoutError:
            logger.error(
                f"Processing timed out after {TIMEOUT_SECONDS} seconds")
            await message.reply("Sorry, the request timed out. Please try again later.")
        except instaloader.exceptions.AbortDownloadException as e:
            # Instagram is demanding a checkpoint/challenge/feedback verification on this
            # account - retrying won't help, this needs manual intervention.
            logger.error(f"Instagram aborted the download, account may be flagged: {e}")
            await message.reply(
                "Sorry, Instagram is requiring additional verification on this account "
                "right now. This isn't something retrying will fix.")
        except Exception as e:
            logger.exception(f"Error downloading Instagram post: {e}")
            await message.reply(f"Sorry, there was an error processing your Instagram link: {str(e)}")
