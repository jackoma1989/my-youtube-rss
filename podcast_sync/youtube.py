import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yt_dlp

from .config import Config

logger = logging.getLogger(__name__)


class YouTubeFetcher:
    def __init__(self, config: Config):
        self.config = config
        self._cookie_file: Optional[str] = None
        self._setup_cookies()

    def _setup_cookies(self):
        """Prepare cookie file if provided in config."""
        if not self.config.youtube_cookies:
            return

        # If it's an existing file path
        if os.path.exists(self.config.youtube_cookies):
            self._cookie_file = self.config.youtube_cookies
            return

        # Otherwise treat as raw Netscape cookie content and write to a temp file
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(self.config.youtube_cookies)
        tmp.close()
        self._cookie_file = tmp.name

    def _get_base_ydl_opts(self) -> dict:
        opts = {
            "quiet": False,
            "no_warnings": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
            "ignoreerrors": True,
        }
        if self._cookie_file and os.path.exists(self._cookie_file):
            opts["cookiefile"] = self._cookie_file
        return opts

    def get_channel_info_and_entries(self) -> Tuple[Dict[str, str], List[dict]]:
        """
        Fetch channel metadata and the list of recent video items.
        Returns:
            channel_info: {title, description, author, image_url, link}
            entries: list of video info dicts (at most config.max_episodes)
        """
        url = self.config.channel_url.strip()
        # If it's a channel URL without tab, target /videos to get latest uploads
        if ("youtube.com/@" in url or "youtube.com/channel/" in url or "youtube.com/c/" in url) and not (
            url.endswith("/videos") or url.endswith("/streams") or url.endswith("/shorts")
        ):
            fetch_url = url.rstrip("/") + "/videos"
        else:
            fetch_url = url

        ydl_opts = self._get_base_ydl_opts()
        ydl_opts["playlist_items"] = f"1:{self.config.max_episodes}"

        logger.info(f"Fetching channel info from: {fetch_url}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(fetch_url, download=False)

        if not data:
            raise RuntimeError(f"Could not extract channel data from {fetch_url}")

        channel_title = (
            self.config.podcast_title
            or data.get("channel")
            or data.get("uploader")
            or data.get("title")
            or "YouTube Podcast"
        )
        channel_desc = (
            self.config.podcast_description
            or data.get("description")
            or f"Audio podcast synced from {channel_title}"
        )
        channel_author = (
            self.config.podcast_author
            or data.get("channel")
            or data.get("uploader")
            or channel_title
        )
        channel_link = data.get("webpage_url") or data.get("channel_url") or url

        # Pick best channel avatar or cover image
        image_url = self.config.podcast_image_url
        if not image_url:
            thumbnails = data.get("thumbnails") or []
            if thumbnails:
                # Prefer square/large thumbnails
                image_url = thumbnails[-1].get("url")
            if not image_url and data.get("thumbnail"):
                image_url = data.get("thumbnail")

        channel_info = {
            "title": channel_title,
            "description": channel_desc,
            "author": channel_author,
            "image_url": image_url or "",
            "link": channel_link,
        }

        raw_entries = data.get("entries") or []
        entries = [e for e in raw_entries if e and e.get("id")]
        logger.info(f"Found {len(entries)} recent videos for {channel_title}")
        return channel_info, entries

    def download_audio_for_video(self, video_id: str, output_dir: Path) -> dict:
        """
        Download audio for a single video using yt-dlp.
        Extracts format 140 (AAC) or falls back to best audio, saving as .m4a.
        Returns:
            dict containing video metadata and local audio path.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        video_url = f"https://www.youtube.com/watch?v={video_id}"
        out_template = str(output_dir / f"{video_id}.%(ext)s")
        target_audio_file = output_dir / f"{video_id}.m4a"

        ydl_opts = {
            "format": "140/bestaudio[ext=m4a]/bestaudio/best",
            "outtmpl": out_template,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                }
            ],
            "quiet": False,
            "no_warnings": True,
            "ignoreerrors": False,
        }
        if self._cookie_file and os.path.exists(self._cookie_file):
            ydl_opts["cookiefile"] = self._cookie_file

        logger.info(f"Downloading audio for {video_id} -> {target_audio_file}")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(video_url, download=True)

        if not target_audio_file.exists():
            # Check if downloaded with another extension
            candidates = list(output_dir.glob(f"{video_id}.*"))
            if not candidates:
                raise FileNotFoundError(f"Audio download failed for {video_id}, file not found")
            target_audio_file = candidates[0]

        # Parse upload date / datetime
        pub_dt = None
        if info.get("timestamp"):
            pub_dt = datetime.fromtimestamp(info["timestamp"], tz=timezone.utc)
        elif info.get("upload_date"):
            try:
                pub_dt = datetime.strptime(info["upload_date"], "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        if not pub_dt:
            pub_dt = datetime.now(timezone.utc)

        # Pick best thumbnail
        thumbnails = info.get("thumbnails") or []
        thumb_url = thumbnails[-1].get("url") if thumbnails else info.get("thumbnail")

        file_size = target_audio_file.stat().st_size

        return {
            "video_id": video_id,
            "title": info.get("title", f"Video {video_id}"),
            "description": info.get("description", ""),
            "pub_date": pub_dt,
            "duration_seconds": int(info.get("duration") or 0),
            "local_audio_path": target_audio_file,
            "file_size_bytes": file_size,
            "thumbnail_url": thumb_url,
            "webpage_url": info.get("webpage_url") or video_url,
        }
