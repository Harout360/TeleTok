import json
import os
from dataclasses import dataclass
from typing import Optional


@dataclass
class Settings:
    api_token: str
    allowed_ids: list[int]
    reply_to_message: bool
    with_captions: bool
    instagram_username: Optional[str]
    instagram_password: Optional[str]


def parse_env_list(key: str) -> list[int]:
    return list(map(int, json.loads(os.getenv(key, "[]"))))


def parse_env_bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).lower() in ("yes", "true", "1", "on")


settings = Settings(
    api_token=os.getenv("API_TOKEN", ""),
    allowed_ids=parse_env_list("ALLOWED_IDS"),
    reply_to_message=parse_env_bool("REPLY_TO_MESSAGE", default="true"),
    with_captions=parse_env_bool("WITH_CAPTIONS", default="true"),
    instagram_username=os.getenv("INSTAGRAM_USERNAME"),
    instagram_password=os.getenv("INSTAGRAM_PASSWORD"),
)
