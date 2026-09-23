import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from curl_cffi import requests

# Ensure scratch_repo is available on sys.path
SCRATCH_REPO_PATH = Path(__file__).resolve().parent.parent / "scratch_repo"
if str(SCRATCH_REPO_PATH) not in sys.path:
    sys.path.insert(0, str(SCRATCH_REPO_PATH))

from src.interface.account import Account
from src.testers import Params

logger = logging.getLogger("douyin_fetcher")


class DouyinFetcher:
    def __init__(self, cookie: Optional[str] = None, proxy: Optional[str] = None):
        self.cookie = cookie or self._load_cookie()
        self.uifid = self._extract_uifid(self.cookie)
        self.proxy = proxy or os.environ.get("CHINA_PROXY", "").strip() or None
        if self.proxy:
            logger.info(f"DouyinFetcher using proxy: {self.proxy}")

    def _load_cookie(self) -> str:
        # Check environment variable first
        env_cookie = os.environ.get("DOUYIN_COOKIE", "").strip()
        if env_cookie:
            return env_cookie

        # Fallback to scratch_tk/Volume/settings.json
        settings_path = Path("scratch_tk/Volume/settings.json")
        if settings_path.exists():
            try:
                with open(settings_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("cookie", "").strip()
            except Exception as e:
                logger.warning(f"Failed to read cookie from {settings_path}: {e}")
        return ""

    def _extract_uifid(self, cookie: str) -> str:
        m = re.search(r"UIFID=([^;]+)", cookie)
        return m.group(1).strip() if m else ""

    def resolve_sec_uid(self, url: str) -> str:
        """Resolve short link or user homepage URL to sec_user_id."""
        url = url.strip()
        if "/user/" in url:
            sec_uid = url.split("/user/")[-1].split("?")[0].strip("/").strip()
            return sec_uid

        # Resolve short share link (e.g. https://v.douyin.com/xxxx/)
        try:
            req_kwargs = {"impersonate": "chrome120", "allow_redirects": True, "timeout": 10}
            if self.proxy:
                req_kwargs["proxies"] = {"all": self.proxy}
            resp = requests.get(url, **req_kwargs)
            final_url = resp.url
            if "/user/" in final_url:
                return final_url.split("/user/")[-1].split("?")[0].strip("/").strip()
        except Exception as e:
            logger.warning(f"Failed to resolve Douyin URL '{url}': {e}")

        return url

    async def fetch_creator_posts(
        self,
        sec_user_id: str,
        max_posts: int = 15,
        pages: int = 1,
    ) -> List[Dict]:
        """Fetch latest posts for a creator using dual-signed API, sorted newest first."""
        if not self.cookie:
            logger.error("No Douyin Cookie found. Please configure DOUYIN_COOKIE in .env or settings.json")
            return []

        # Populate test_cookie.ini so Params() initializes cleanly without warning
        try:
            ini_path = SCRATCH_REPO_PATH / "Volume" / "test_cookie.ini"
            ini_path.parent.mkdir(parents=True, exist_ok=True)
            ini_path.write_text(
                f"[dy]\ncookie = {self.cookie}\nuifid = {self.uifid}\nmsToken = \n\n[tk]\ncookie = \nmsToken = \n",
                encoding="utf-8",
            )
        except Exception:
            pass

        async with Params() as params:
            params.cookie_str = self.cookie
            params.headers["Cookie"] = self.cookie
            params.max_retry = 2
            params.timeout = 15
            params.max_pages = pages

            if self.proxy:
                params.proxy = self.proxy
                from src.tools import create_client
                params.client = create_client(
                    timeout=params.timeout,
                    proxy=self.proxy,
                    impersonate=params.impersonate,
                )

            # Fetch extra posts so pinned (is_top) older posts don't push out recent ones
            fetch_count = min(max(max_posts + 5, 10), 20)
            account = Account(
                params=params,
                cookie=self.cookie,
                sec_user_id=sec_user_id,
                tab="post",
                pages=pages,
                count=fetch_count,
            )
            # Inject UIFID to enable WebSign
            account.params["uifid"] = self.uifid

            posts_raw, _, _ = await account.run()
            if not posts_raw:
                logger.warning(f"No posts returned for sec_user_id: {sec_user_id}")
                return []

            posts = []
            for item in posts_raw:
                aweme_id = item.get("aweme_id")
                desc = item.get("desc", "").strip()
                create_time = item.get("create_time", 0)
                pub_date = datetime.fromtimestamp(create_time, tz=timezone.utc)

                music = item.get("music", {})
                music_urls = music.get("play_url", {}).get("url_list", [])
                audio_url = music_urls[0] if music_urls else ""
                duration = int(music.get("duration", 0))

                video = item.get("video", {})
                cover_urls = video.get("cover", {}).get("url_list", [])
                cover_url = cover_urls[0] if cover_urls else ""

                author = item.get("author", {})
                avatar_thumb = author.get("avatar_thumb", {}).get("url_list", [])
                avatar_url = avatar_thumb[0] if avatar_thumb else ""
                nickname = author.get("nickname", "抖音博主")

                # Derive clean title from description
                first_line = desc.split("\n")[0].strip() if desc else f"Episode {aweme_id}"
                # Clean hashtag suffixes from title
                title_clean = re.sub(r"#\S+", "", first_line).strip() or first_line

                posts.append({
                    "video_id": aweme_id,
                    "title": title_clean,
                    "description": desc,
                    "pub_date": pub_date,
                    "duration_seconds": duration,
                    "audio_url": audio_url,
                    "cover_url": cover_url,
                    "avatar_url": avatar_url,
                    "author_name": nickname,
                    "webpage_url": f"https://www.douyin.com/video/{aweme_id}",
                    "is_top": bool(item.get("is_top", 0)),
                })

            # Sort strictly newest first by pub_date
            posts.sort(key=lambda p: p["pub_date"], reverse=True)
            return posts[:max_posts]

    def download_audio(self, audio_url: str, output_path: Path) -> bool:
        """Download direct MP3 stream from Douyin CDN."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            req_kwargs = {"impersonate": "chrome120", "timeout": 30}
            if self.proxy:
                req_kwargs["proxies"] = {"all": self.proxy}
            resp = requests.get(audio_url, **req_kwargs)
            if resp.status_code == 200 and len(resp.content) > 1000:
                output_path.write_bytes(resp.content)
                logger.info(f"Downloaded audio to {output_path} ({len(resp.content) / 1024 / 1024:.2f} MB)")
                return True
            else:
                logger.error(f"Download failed with status {resp.status_code} for {audio_url}")
                return False
        except Exception as e:
            logger.error(f"Exception downloading audio from {audio_url}: {e}")
            return False

    def download_image(self, image_url: str, output_path: Path) -> bool:
        """Download cover or avatar image."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            req_kwargs = {"impersonate": "chrome120", "timeout": 20}
            if self.proxy:
                req_kwargs["proxies"] = {"all": self.proxy}
            resp = requests.get(image_url, **req_kwargs)
            if resp.status_code == 200 and len(resp.content) > 500:
                output_path.write_bytes(resp.content)
                logger.info(f"Downloaded image to {output_path}")
                return True
            return False
        except Exception as e:
            logger.warning(f"Failed to download image from {image_url}: {e}")
            return False


