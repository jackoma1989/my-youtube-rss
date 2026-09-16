import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


@dataclass
class Config:
    # YouTube channel or playlist URL
    channel_url: str = ""

    # Cloudflare R2 Credentials (S3 compatible)
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = ""
    r2_public_url: str = ""  # e.g., https://pub-xxxx.r2.dev or https://podcast.example.com

    # Podcast feed overrides (optional)
    podcast_title: Optional[str] = None
    podcast_description: Optional[str] = None
    podcast_author: Optional[str] = None
    podcast_image_url: Optional[str] = None
    podcast_language: str = "zh-cn"
    podcast_category: str = "Technology"

    # Retention & Execution
    max_episodes: int = 15
    dry_run: bool = False
    output_dir: Path = field(default_factory=lambda: Path("./output"))

    # Optional YouTube cookies (can be raw text or path to cookies.txt)
    youtube_cookies: Optional[str] = None

    @classmethod
    def from_env(cls, env_path: Optional[Path] = None) -> "Config":
        if env_path is None:
            env_path = Path(".env")
        load_env_file(env_path)

        def get_bool(key: str, default: bool = False) -> bool:
            v = os.environ.get(key, "").strip().lower()
            if not v:
                return default
            return v in ("1", "true", "yes", "on")

        def get_int(key: str, default: int) -> int:
            v = os.environ.get(key, "").strip()
            if not v:
                return default
            try:
                return int(v)
            except ValueError:
                return default

        output_dir_str = os.environ.get("OUTPUT_DIR", "./output").strip()
        public_url = os.environ.get("R2_PUBLIC_URL", "").strip().rstrip("/")

        return cls(
            channel_url=os.environ.get("YOUTUBE_CHANNEL_URL", "").strip(),
            r2_account_id=os.environ.get("R2_ACCOUNT_ID", "").strip(),
            r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
            r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
            r2_bucket_name=os.environ.get("R2_BUCKET_NAME", "").strip(),
            r2_public_url=public_url,
            podcast_title=os.environ.get("PODCAST_TITLE", "").strip() or None,
            podcast_description=os.environ.get("PODCAST_DESCRIPTION", "").strip() or None,
            podcast_author=os.environ.get("PODCAST_AUTHOR", "").strip() or None,
            podcast_image_url=os.environ.get("PODCAST_IMAGE_URL", "").strip() or None,
            podcast_language=os.environ.get("PODCAST_LANGUAGE", "zh-cn").strip(),
            podcast_category=os.environ.get("PODCAST_CATEGORY", "Technology").strip(),
            max_episodes=get_int("MAX_EPISODES", 15),
            dry_run=get_bool("DRY_RUN", False),
            output_dir=Path(output_dir_str),
            youtube_cookies=os.environ.get("YOUTUBE_COOKIES", "").strip() or None,
        )

    def validate(self) -> None:
        """Validate required configuration."""
        if not self.channel_url:
            raise ValueError("Missing required environment variable: YOUTUBE_CHANNEL_URL")

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
