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
    """Ensure channel ID is safe for URLs and filenames (converts Chinese to pinyin automatically)."""
    decoded = urllib.parse.unquote(raw_id or "").strip().lstrip("@")
    try:
        from pypinyin import lazy_pinyin
        decoded = "".join(lazy_pinyin(decoded))
    except Exception:
        pass
    cleaned = re.sub(r"[^a-zA-Z0-9_\-]", "", decoded)
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

    # Telegram Notifications (optional)
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None

    # WeChat Notifications via Tencent iLink Bot (optional, dual protection with Telegram)
    wechat_bot_token: Optional[str] = None
    wechat_user_id: Optional[str] = None
    wechat_context_token: Optional[str] = None
    wechat_base_url: str = "https://ilinkai.weixin.qq.com"

    # Feishu Notifications via Lark / Feishu Open API or Webhook (optional)
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_receive_id: Optional[str] = None
    feishu_receive_id_type: Optional[str] = None
    feishu_webhook_url: Optional[str] = None

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

        # Load optional channels from channels.json (empty by default)
        json_channels: List[ChannelConfig] = []
        if channels_file is None:
            channels_file = Path("channels.json")

        if channels_file.exists():
            try:
                with open(channels_file, "r", encoding="utf-8") as f:
                    items = json.load(f)
                    if isinstance(items, list):
                        for item in items:
                            if isinstance(item, dict) and item.get("url") and item.get("enabled", True):
                                json_channels.append(ChannelConfig.from_dict(item))
            except Exception as e:
                print(f"Warning: Failed to parse {channels_file}: {e}")

        # Active channels configured via GitHub Secrets / environment variables (YOUTUBE_CHANNEL_URL*)
        env_channel_items = [
            (env_key, env_val.strip())
            for env_key, env_val in os.environ.items()
            if env_key.startswith("YOUTUBE_CHANNEL_URL") and env_val.strip()
        ]
        def _sort_key(item):
            suffix = item[0].replace("YOUTUBE_CHANNEL_URL", "").strip("_")
            return int(suffix) if suffix.isdigit() else 0
        env_channel_items.sort(key=_sort_key)

        if env_channel_items:
            # Secrets act as the authoritative list of active channels
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

                suffix = env_key.replace("YOUTUBE_CHANNEL_URL", "").strip("_")
                custom_id = (
                    os.environ.get(f"YOUTUBE_CHANNEL_ID_{suffix.upper()}")
                    or os.environ.get(f"YOUTUBE_CHANNEL_ID_{suffix}")
                    or os.environ.get(f"YOUTUBE_CHANNEL_ID{suffix}")
                    or (os.environ.get("YOUTUBE_CHANNEL_ID") if not suffix else None)
                )
                custom_name = (
                    os.environ.get(f"YOUTUBE_CHANNEL_NAME_{suffix.upper()}")
                    or os.environ.get(f"YOUTUBE_CHANNEL_NAME_{suffix}")
                    or os.environ.get(f"YOUTUBE_CHANNEL_NAME{suffix}")
                    or (os.environ.get("YOUTUBE_CHANNEL_NAME") if not suffix else None)
                )

                if norm_url in json_by_norm_url:
                    ch_obj = json_by_norm_url[norm_url]
                    if custom_id:
                        ch_obj.id = sanitize_channel_id(custom_id)
                    if custom_name:
                        ch_obj.name = custom_name
                    channels.append(ch_obj)
                else:
                    raw_id = custom_id or ""
                    if not raw_id:
                        if "@" in url_val:
                            raw_id = url_val.split("@")[-1].split("/")[0].split("?")[0]
                        elif "channel/" in url_val:
                            raw_id = url_val.split("channel/")[-1].split("/")[0].split("?")[0]
                        else:
                            raw_id = f"channel_{suffix.lower()}" if suffix else "default"

                    channels.append(
                        ChannelConfig(
                            id=sanitize_channel_id(raw_id),
                            url=url_val,
                            name=custom_name,
                            max_episodes=int(os.environ.get("MAX_EPISODES", 15)),
                            category=os.environ.get("PODCAST_CATEGORY", "News"),
                            language=os.environ.get("PODCAST_LANGUAGE", "zh-cn"),
                        )
                    )
        else:
            channels = json_channels

        # WeChat credentials (from environment or local Hermes account auto-discovery)
        wc_token = os.environ.get("WECHAT_BOT_TOKEN", "").strip() or os.environ.get("ILINK_BOT_TOKEN", "").strip() or None
        wc_user = os.environ.get("WECHAT_USER_ID", "").strip() or None
        wc_ctx = os.environ.get("WECHAT_CONTEXT_TOKEN", "").strip() or None
        wc_base = os.environ.get("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com").strip() or "https://ilinkai.weixin.qq.com"

        if not wc_token or not wc_user:
            try:
                hermes_home = os.environ.get("HERMES_HOME", os.path.expandvars(r"%LOCALAPPDATA%\hermes"))
                acc_dir = Path(hermes_home) / "weixin" / "accounts"
                if acc_dir.is_dir():
                    import glob
                    acc_files = glob.glob(str(acc_dir / "*@im.bot.json"))
                    if acc_files:
                        acc_files.sort(key=os.path.getmtime, reverse=True)
                        target_acc = acc_files[0]
                        with open(target_acc, "r", encoding="utf-8") as af:
                            acc_data = json.load(af)
                        wc_token = wc_token or acc_data.get("token")
                        wc_user = wc_user or acc_data.get("user_id")
                        wc_base = wc_base or acc_data.get("base_url", "https://ilinkai.weixin.qq.com")
                        prefix = Path(target_acc).stem
                        tokens_file = acc_dir / f"{prefix}.context-tokens.json"
                        if not wc_ctx and tokens_file.exists():
                            with open(tokens_file, "r", encoding="utf-8") as tf:
                                tokens_data = json.load(tf)
                                wc_ctx = tokens_data.get(wc_user, "")
            except Exception:
                pass

        # Feishu credentials (from environment or local Hermes auto-discovery)
        fs_app_id = os.environ.get("FEISHU_APP_ID", "").strip() or None
        fs_app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip() or None
        fs_recv_id = (
            os.environ.get("FEISHU_RECEIVE_ID", "").strip()
            or os.environ.get("FEISHU_HOME_CHANNEL", "").strip()
            or None
        )
        fs_recv_type = os.environ.get("FEISHU_RECEIVE_ID_TYPE", "").strip() or None
        fs_webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip() or None

        if not fs_app_id or not fs_app_secret or not fs_recv_id:
            try:
                hermes_home = os.environ.get("HERMES_HOME", os.path.expandvars(r"%LOCALAPPDATA%\hermes"))
                hermes_env_file = Path(hermes_home) / ".env"
                if hermes_env_file.is_file():
                    with open(hermes_env_file, "r", encoding="utf-8") as hef:
                        for line in hef:
                            line = line.strip()
                            if not line or line.startswith("#") or "=" not in line:
                                continue
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'\"")
                            if k == "FEISHU_APP_ID" and not fs_app_id:
                                fs_app_id = v
                            elif k == "FEISHU_APP_SECRET" and not fs_app_secret:
                                fs_app_secret = v
                            elif (k in ("FEISHU_HOME_CHANNEL", "FEISHU_RECEIVE_ID")) and not fs_recv_id:
                                fs_recv_id = v
                
                # Also check channel_directory.json if receive_id still missing
                if not fs_recv_id:
                    chan_dir_file = Path(hermes_home) / "channel_directory.json"
                    if chan_dir_file.is_file():
                        with open(chan_dir_file, "r", encoding="utf-8") as cdf:
                            cdata = json.load(cdf)
                            feishu_channels = cdata.get("platforms", {}).get("feishu", [])
                            if feishu_channels and isinstance(feishu_channels, list):
                                fs_recv_id = feishu_channels[0].get("id")
            except Exception:
                pass

        return cls(
            channels=channels,
            r2_account_id=os.environ.get("R2_ACCOUNT_ID", "").strip(),
            r2_access_key_id=os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
            r2_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
            r2_bucket_name=os.environ.get("R2_BUCKET_NAME", "").strip(),
            r2_public_url=public_url,
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None,
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None,
            wechat_bot_token=wc_token,
            wechat_user_id=wc_user,
            wechat_context_token=wc_ctx,
            wechat_base_url=wc_base,
            feishu_app_id=fs_app_id,
            feishu_app_secret=fs_app_secret,
            feishu_receive_id=fs_recv_id,
            feishu_receive_id_type=fs_recv_type,
            feishu_webhook_url=fs_webhook,
            dry_run=get_bool("DRY_RUN", False),
            output_dir=Path(output_dir_str),
            youtube_cookies=os.environ.get("YOUTUBE_COOKIES", "").strip() or None,
        )

    def validate(self) -> None:
        """Validate required configuration when channels are present."""
        if not self.channels:
            return

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
