import json
import os
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


def load_env_file(dotenv_path: Path) -> None:
    """Simple parser to load key-value pairs from a .env file if it exists."""
    if not dotenv_path.exists():
        return
    with open(dotenv_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val


def sanitize_channel_id(raw_id: str) -> str:
    """Ensure channel ID is safe for URLs and filenames."""
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", raw_id)
    return cleaned.lower() if cleaned else "podcast"


@dataclass
class ChannelConfig:
    id: str
    url: str
    name: Optional[str] = None
    max_episodes: int = 15
    category: str = "Technology"
    language: str = "zh-cn"
    title: Optional[str] = None
    author: Optional[str] = None
    description: Optional[str] = None
    image_url: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "ChannelConfig":
        raw_id = d.get("id") or ""
        url = d.get("url") or ""
        if not raw_id and "@" in url:
            raw_id = url.split("@")[-1].split("/")[0]
        if not raw_id:
            raw_id = "default"

        return cls(
            id=sanitize_channel_id(raw_id),
            url=url.strip(),
            name=d.get("name"),
            max_episodes=int(d.get("max_episodes", 15)),
            category=d.get("category", "Technology"),
            language=d.get("language", "zh-cn"),
            title=d.get("title"),
            author=d.get("author"),
            description=d.get("description"),
            image_url=d.get("image_url"),
        )


@dataclass
class Config:
    channels: List[ChannelConfig] = field(default_factory=list)

    # Cloudflare R2 Credentials (S3 compatible)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_url: str = ""  # e.g., https://podcast.hemajia.fun (no trailing slash)

    # Execution Options
    dry_run: bool = False
    output_dir: Path = field(default_factory=lambda: Path("./output"))
    youtube_cookies: Optional[str] = None

    @classmethod
    def from_env(
        cls,
        env_path: Optional[Path] = None,
        channels_file: Optional[Path] = None,
    ) -> "Config":
        if env_path is None:
            env_path = Path(".env")
        load_env_file(env_path)

        def get_bool(key: str, default: bool = False) -> bool:
            v = os.environ.get(key, "").strip().lower()
            if not v:
                return default
            return v in ("1", "true", "yes", "on")

        output_dir_str = os.environ.get("OUTPUT_DIR", "./output").strip()
        public_url = os.environ.get("R2_PUBLIC_URL", "").strip().rstrip("/")

        # Load channels from channels.json
        json_channels: List[ChannelConfig] = []
        if channels_file is None:
            channels_file = Path("channels.json")

        if channels_file.exists():
            try:
                with open(channels_file, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and item.get("url"):
                                json_channels.append(ChannelConfig.from_dict(item))
            except Exception as e:
                print(f"Warning: Failed to parse {channels_file}: {e}")

        # Check if active channels are configured via GitHub Secrets / environment variables
        env_channel_items = [
            (env_key, env_val.strip())
            for env_key, env_val in os.environ.items()
            if env_key.startswith("YOUTUBE_CHANNEL_URL") and env_val.strip()
        ]

        if env_channel_items:
            # When Secrets are provided, Secrets act as the authoritative list of active channels!
            channels: List[ChannelConfig] = []
            json_by_norm_url = {
                urllib.parse.unquote(ch.url).lower().rstrip("/"): ch
                for ch in json_channels
            }
            seen_urls = set()

            for env_key, url_val in env_channel_items:
                norm_url = urllib.parse.unquote(url_val).lower().rstrip("/")
                if norm_url in seen_urls:
                    continue
                seen_urls.add(norm_url)

                if norm_url in json_by_norm_url:
                    # Inherit metadata (custom id, name, category) from channels.json
                    channels.append(json_by_norm_url[norm_url])
                else:
                    raw_id = "channel"
                    if "@" in url_val:
                        raw_id = url_val.split("@")[-1].split("/")[0].split("?")[0]
                    elif "channel/" in url_val:
                        raw_id = url_val.split("channel/")[-1].split("/")[0].split("?")[0]
                    else:
                        suffix = env_key.replace("YOUTUBE_CHANNEL_URL", "").strip("_").lower()
                        raw_id = f"channel_{suffix}" if suffix else "default"

                    channels.append(
                        ChannelConfig(
                            id=sanitize_channel_id(raw_id),
                            url=url_val,
                            max_episodes=int(os.environ.get("MAX_EPISODES", 15)),
                            category=os.environ.get("PODCAST_CATEGORY", "News"),
                            language=os.environ.get("PODCAST_LANGUAGE", "zh-cn"),
                        )
                    )
        else:
            channels = json_channels

        return cls(
            channels=channels,
            r2_account_id=os.environ.get("R2_ACCOUNT_ID", "").strip(),
            r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
            r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
            r2_bucket_name=os.environ.get("R2_BUCKET_NAME", "").strip(),
            r2_public_url=public_url,
            dry_run=get_bool("DRY_RUN", False),
            output_dir=Path(output_dir_str),
            youtube_cookies=os.environ.get("YOUTUBE_COOKIES", "").strip() or None,
        )

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.channels:
            raise ValueError(
                "No YouTube channels configured. Please add channels to channels.json or set YOUTUBE_CHANNEL_URL."
            )

        for ch in self.channels:
            if not ch.url:
                raise ValueError(f"Channel '{ch.id}' is missing a 'url'.")

        if not self.dry_run:
            missing = []
            if not self.r2_account_id:
                missing.append("R2_ACCOUNT_ID")
            if not self.r2_access_key_id:
                missing.append("R2_ACCESS_KEY_ID")
            if not self.r2_secret_access_key:
                missing.append("R2_SECRET_ACCESS_KEY")
            if not self.r2_bucket_name:
                missing.append("R2_BUCKET_NAME")
            if not self.r2_public_url:
                missing.append("R2_PUBLIC_URL")

            if missing:
                raise ValueError(
                    f"Missing required Cloudflare R2 credentials for online sync: {', '.join(missing)}. "
                    f"Set DRY_RUN=true if you wish to run locally without uploading to R2."
                )
