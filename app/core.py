import logging
import sys
import instaloader
import os
import asyncio
from urllib.parse import urlparse
from pathlib import Path

# Testing the downloader code directly

# Set up logging
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)
logger.info("Instagram Downloader started...")

# Initialize Instaloader
insta_loader = instaloader.Instaloader()


async def download_instagram_reel(url):
    try:
        parsed_url = urlparse(url)
        path_parts = parsed_url.path.strip("/").split("/")

        if len(path_parts) >= 2 and path_parts[0] == "reel":
            shortcode = path_parts[1].strip()  # Clean up any whitespace
        else:
            logger.error(f"Invalid Instagram reel URL: {url}")
            return

        # Construct the target directory path using pathlib
        target_dir = Path("downloads") / f"instagram_{shortcode}"
        logger.info(f"Creating directory: {target_dir}")
        # Create the directory if it doesn't exist
        target_dir.mkdir(parents=True, exist_ok=True)

        # Load the post using the shortcode
        post = instaloader.Post.from_shortcode(insta_loader.context, shortcode)

        # Download the post
        insta_loader.download_post(post, target_dir)

        # Find the video file in the download directory
        video_file = next(target_dir.glob("*.mp4"), None)

        if video_file:
            logger.info(f"Downloaded video to: {video_file}")

            # Wait for 2 seconds before deleting the downloaded files
            await asyncio.sleep(2)

            # Cleanup: Remove downloaded files after waiting
            for file in target_dir.iterdir():
                file.unlink()  # Remove the file
            target_dir.rmdir()  # Remove the directory after cleaning up files
            logger.info(f"Deleted downloaded files from: {target_dir}")
        else:
            logger.error("No video file found.")

    except Exception as e:
        logger.error(f"Error downloading Instagram video: {e}")


# Test the downloader directly
if __name__ == "__main__":
    # Replace <shortcode> with a valid shortcode
    test_url = "https://www.instagram.com/reel/DAqS5YtO10X/"
    asyncio.run(download_instagram_reel(test_url))
