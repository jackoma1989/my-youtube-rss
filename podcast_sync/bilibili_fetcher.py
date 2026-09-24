import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import yt_dlp

logger = logging.getLogger(__name__)


def parse_bilibili_cookies(cookie_input: Optional[str] = None) -> Dict[str, str]:
    """Parse Bilibili cookies from string or environment variables."""
    cookies = {}

    raw = cookie_input or os.environ.get("BILI_COOKIES") or ""
    if raw:
        # Support key=val; key2=val2 or multi-line
        parts = re.split(r"[;\n]", raw)
        for part in parts:
            part = part.strip()
            if not part or "=" not in part:
                continue
            k, v = part.split("=", 1)
            cookies[k.strip()] = v.strip()

    # Fallback to individual env vars
    if "SESSDATA" not in cookies and os.environ.get("BILI_SESSDATA"):
        cookies["SESSDATA"] = os.environ["BILI_SESSDATA"].strip()
    if "buvid3" not in cookies and os.environ.get("BILI_BUVID3"):
        cookies["buvid3"] = os.environ["BILI_BUVID3"].strip()
    if "bili_jct" not in cookies and os.environ.get("BILI_BILI_JCT"):
        cookies["bili_jct"] = os.environ["BILI_BILI_JCT"].strip()

    return cookies


def create_netscape_cookie_file(cookies: Dict[str, str], target_path: Optional[Path] = None) -> Path:
    """Generate a standard Netscape cookie file for yt-dlp."""
    lines = [
        "# Netscape HTTP Cookie File",
        "# Generated for Bilibili yt-dlp authentication",
        "",
    ]
    for k, v in cookies.items():
        # domain, domain_flag, path, secure, expiration, name, value
        lines.append(f".bilibili.com\tTRUE\t/\tFALSE\t1893456000\t{k}\t{v}")

    if target_path:
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text("\n".join(lines), encoding="utf-8")
        return target_path

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
    tmp.write("\n".join(lines))
    tmp.close()
    return Path(tmp.name)


class BilibiliFetcher:
    """Fetcher for Bilibili user spaces and video streams using yt-dlp."""

    def __init__(self, cookies: Optional[Dict[str, str]] = None, proxy: Optional[str] = None):
        self.cookies = cookies or parse_bilibili_cookies()
        # Only use proxy if explicitly provided or CHINA_PROXY is set (do NOT inherit overseas Clash proxy)
        self.proxy = proxy or os.environ.get("CHINA_PROXY")
        self._cookie_file: Optional[Path] = None
        if self.cookies:
            self._cookie_file = create_netscape_cookie_file(self.cookies)
            logger.info(f"Bilibili authentication cookie active (keys: {list(self.cookies.keys())})")
        else:
            logger.warning("No Bilibili cookies provided; download quality may be restricted to low bitrate.")

    def _get_base_opts(self) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
        }
        if self._cookie_file and self._cookie_file.exists():
            opts["cookiefile"] = str(self._cookie_file)
        if self.proxy:
            opts["proxy"] = self.proxy
        else:
            # Explicitly disable proxy so yt-dlp doesn't pick up ambient HTTP_PROXY
            opts["proxy"] = ""
        return opts

    def normalize_space_url(self, url_or_mid: str) -> str:
        """Convert MID or space URL to clean space URL."""
        raw = str(url_or_mid).strip()
        if raw.isdigit():
            return f"https://space.bilibili.com/{raw}"
        m = re.search(r"space\.bilibili\.com/(\d+)", raw)
        if m:
            mid = m.group(1)
            return f"https://space.bilibili.com/{mid}"
        return raw

    def fetch_recent_videos(self, space_url_or_mid: str, limit: int = 3) -> List[dict]:
        """Fetch list of recent video entries from UP space."""
        space_url = self.normalize_space_url(space_url_or_mid)
        logger.info(f"Scanning Bilibili UP space: {space_url} (limit={limit})")

        opts = self._get_base_opts()
        opts.update({
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlist_items": f"1-{limit}",
        })

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(space_url, download=False)
                entries = list(info.get("entries", [])) if info else []
        except Exception as e:
            logger.error(f"Failed to scan space {space_url}: {e}")
            return []

        results = []
        for e in entries:
            video_id = e.get("id")
            if not video_id:
                continue
            results.append({
                "video_id": video_id,
                "url": f"https://www.bilibili.com/video/{video_id}",
                "title": e.get("title"),
                "duration": e.get("duration"),
            })
        logger.info(f"Found {len(results)} recent video(s) in space.")
        return results

    def fetch_video_metadata(self, video_id: str) -> Optional[dict]:
        """Fetch full video metadata without downloading."""
        video_url = f"https://www.bilibili.com/video/{video_id}"
        opts = self._get_base_opts()
        opts["skip_download"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                return info
        except Exception as e:
            logger.error(f"Failed to fetch metadata for [{video_id}]: {e}")
            return None

    def download_audio_for_video(self, video_id: str, output_dir: Path) -> Optional[dict]:
        """Download high quality audio (.m4a) and optional subtitles for a video."""
        output_dir.mkdir(parents=True, exist_ok=True)
        video_url = f"https://www.bilibili.com/video/{video_id}"
        out_template = str(output_dir / f"{video_id}.%(ext)s")
        target_audio_file = output_dir / f"{video_id}.m4a"

        opts = self._get_base_opts()
        opts.update({
            "format": "ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio/best",
            "outtmpl": out_template,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["ai-zh", "zh-Hans", "zh-CN", "zh"],
            "subtitlesformat": "vtt",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "m4a",
                }
            ],
            "quiet": False,
            "no_warnings": False,
            "ignoreerrors": False,
        })

        logger.info(f"Downloading Bilibili audio for [{video_id}] -> {target_audio_file}")
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(video_url, download=True)
        except Exception as e:
            logger.error(f"Failed to download audio for [{video_id}]: {e}")
            return None

        if not target_audio_file.exists():
            # Check if any audio file was created
            candidates = list(output_dir.glob(f"{video_id}.*"))
            audio_cand = [c for c in candidates if c.suffix.lower() in (".m4a", ".mp3", ".opus", ".aac")]
            if audio_cand:
                target_audio_file = audio_cand[0]
            else:
                logger.error(f"Downloaded audio file not found for [{video_id}]")
                return None

        # Parse publication date
        pub_date = None
        upload_date_str = info.get("upload_date")
        if upload_date_str and len(upload_date_str) == 8:
            try:
                pub_date = datetime.strptime(upload_date_str, "%Y%m%d").replace(tzinfo=timezone.utc)
            except Exception:
                pass
        if not pub_date:
            timestamp = info.get("timestamp")
            if timestamp:
                pub_date = datetime.fromtimestamp(timestamp, tz=timezone.utc)
            else:
                pub_date = datetime.now(timezone.utc)

        duration = int(info.get("duration") or 0)
        title = info.get("title") or video_id
        description = info.get("description") or ""
        thumbnail = info.get("thumbnail") or ""
        uploader = info.get("uploader") or ""

        # Check for downloaded subtitles (.vtt or .srt)
        subtitles = []
        for sub_file in output_dir.glob(f"{video_id}*.*"):
            if sub_file.suffix.lower() == ".vtt":
                subtitles.append({
                    "local_path": sub_file,
                    "filename": sub_file.name,
                })
            elif sub_file.suffix.lower() == ".srt":
                vtt_target = sub_file.with_suffix(".vtt")
                try:
                    content = sub_file.read_text(encoding="utf-8", errors="replace")
                    vtt_lines = ["WEBVTT\n"]
                    for line in content.splitlines():
                        if " --> " in line:
                            line = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", line)
                        vtt_lines.append(line)
                    vtt_target.write_text("\n".join(vtt_lines), encoding="utf-8")
                    subtitles.append({
                        "local_path": vtt_target,
                        "filename": vtt_target.name,
                    })
                except Exception as e:
                    logger.warning(f"Failed to convert SRT to VTT for {sub_file}: {e}")

        return {
            "video_id": video_id,
            "audio_file": target_audio_file,
            "file_size": target_audio_file.stat().st_size,
            "duration": duration,
            "title": title,
            "description": description,
            "pub_date": pub_date,
            "thumbnail_url": thumbnail,
            "uploader": uploader,
            "webpage_url": video_url,
            "subtitles": subtitles,
        }
