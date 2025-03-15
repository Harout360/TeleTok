import json
import random
import string
import logging
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from tiktok.data import ItemStruct
from utils import DifferentPageError, NoDataError, NoScriptError, retries

logger = logging.getLogger(__name__)


class AsyncTikTokClient(httpx.AsyncClient):
    def __init__(self) -> None:
        super().__init__(
            headers={
                "Referer": "https://www.tiktok.com/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
            },
            timeout=30,
            cookies={
                "tt_webid_v2": f"{random.randint(10 ** 18, (10 ** 19) - 1)}",
            },
            follow_redirects=True,
        )

    @retries(times=3)
    async def get_page_data(self, url: str) -> ItemStruct:
        page = await self.get(url)
        logger.info(f"TikTok redirected URL: {page.url}")

        # Extract video ID from the URL
        page_id = page.url.path.rsplit("/", 1)[-1]
        if not page_id.isdigit():
            # Try to extract from query parameters
            from urllib.parse import parse_qs, urlparse
            parsed_url = urlparse(str(page.url))
            query_params = parse_qs(parsed_url.query)
            share_item_id = query_params.get('share_item_id', [None])[0]
            if share_item_id:
                page_id = share_item_id

        soup = BeautifulSoup(page.text, "html.parser")

        # Try different script tags that might contain the video data
        scripts = [
            soup.select_one('script[id="__UNIVERSAL_DATA_FOR_REHYDRATION__"]'),
            soup.select_one('script[id="SIGI_STATE"]'),
            *soup.select('script[type="application/json"]'),
        ]

        for script in scripts:
            if not script:
                continue

            try:
                data = json.loads(script.text)

                # Try different known data structures
                try:
                    # New structure
                    if "webapp.video-detail" in str(data):
                        item_data = data["__DEFAULT_SCOPE__"]["webapp.video-detail"]["itemInfo"]["itemStruct"]
                    # Alternative structure
                    elif "ItemModule" in str(data):
                        item_data = next(
                            iter(data.get("ItemModule", {}).values()))
                    else:
                        continue

                    if str(item_data.get("id", "")) == str(page_id):
                        return ItemStruct.parse(item_data)
                except (KeyError, AttributeError) as e:
                    logger.debug(f"Failed to parse data structure: {e}")
                    continue
            except json.JSONDecodeError:
                continue

        raise NoDataError("Could not find video data in any known structure")

    async def get_video(self, url: str) -> bytes | None:
        resp = await self.get(url)
        if resp.is_error:
            logger.error(f"Failed to download video: {resp.status_code}")
            return None
        return resp.content
