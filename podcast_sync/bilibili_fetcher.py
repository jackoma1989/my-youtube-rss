import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
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

    # Auxiliary browser tracking cookies to reduce WAF risk
    if "CURRENT_FNVAL" not in cookies:
        cookies["CURRENT_FNVAL"] = "4048"
    if "_uuid" not in cookies:
        cookies["_uuid"] = f"{uuid.uuid4()}{int(time.time() * 1000) % 100000:05d}infoc"
    if "b_nut" not in cookies:
        cookies["b_nut"] = str(int(time.time()))

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


def patch_bilibili_cdn():
    """
    Bilibili API defaults to returning PCDN (mcdn.bilivideo.cn / mountaintoys.cn) in 'baseUrl',
    which severely throttles download speeds to ~100 KB/s.
    This monkey-patch inspects 'backupUrl' and promotes high-speed official
    UPOS / Cloud CDN mirrors (cn-*.bilivideo.com, upos-sz-*.bilivideo.com)
    as primary baseUrl, boosting download speeds from 100 KB/s to 5-10 MB/s (50x boost).
    """
    try:
        from yt_dlp.extractor.bilibili import BiliBiliIE
        if getattr(BiliBiliIE, "_cdn_patched", False):
            return

        orig_extract_formats = BiliBiliIE.extract_formats

        def fast_extract_formats(self, play_info):
            dash = play_info.get("dash") or {}
            for stream_type in ("audio", "video"):
                for s in dash.get(stream_type) or []:
                    base = s.get("baseUrl") or s.get("base_url") or ""
                    backups = s.get("backupUrl") or s.get("backup_url") or []
                    fast_mirrors = [u for u in backups if "bilivideo.com" in u or "upos" in u or "akamaized" in u]
                    if fast_mirrors and ("mcdn" in base or "mountaintoys" in base or not base):
                        s["baseUrl"] = fast_mirrors[0]
                        if "base_url" in s:
                            s["base_url"] = fast_mirrors[0]

            return orig_extract_formats(self, play_info)

        BiliBiliIE.extract_formats = fast_extract_formats
        BiliBiliIE._cdn_patched = True
        logger.info("Successfully patched yt-dlp Bilibili extractor to bypass slow PCDN.")
    except Exception as e:
        logger.warning(f"Failed to patch Bilibili CDN: {e}")


class BilibiliFetcher:
    """Fetcher for Bilibili user spaces and video streams using yt-dlp."""

    def __init__(self, cookies: Optional[Dict[str, str]] = None, proxy: Optional[str] = None):
        patch_bilibili_cdn()
        self.cookies = cookies or parse_bilibili_cookies()
        # Only use proxy if explicitly provided or CHINA_PROXY is set (do NOT inherit overseas Clash proxy)
        self.proxy = proxy or os.environ.get("CHINA_PROXY")
        self._cookie_file: Optional[Path] = None
        if self.cookies:
            self._cookie_file = create_netscape_cookie_file(self.cookies)
            logger.info(f"Bilibili authentication cookie active (keys: {list(self.cookies.keys())})")
        else:
            logger.warning("No Bilibili cookies provided; download quality may be restricted to low bitrate.")

    def _get_base_opts(self, use_proxy: bool = False) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "js_runtimes": {"node": {}},
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
            },
        }
        if self._cookie_file and self._cookie_file.exists():
            opts["cookiefile"] = str(self._cookie_file)
        if use_proxy and self.proxy:
            opts["proxy"] = self.proxy
        else:
            # Explicitly disable proxy for high-speed direct CDN connection
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

    def extract_mid(self, url_or_mid: str) -> Optional[str]:
        """Extract numeric mid from space URL or raw mid."""
        raw = str(url_or_mid).strip()
        if raw.isdigit():
            return raw
        m = re.search(r"space\.bilibili\.com/(\d+)", raw)
        return m.group(1) if m else None

    def fetch_recent_videos(self, space_url_or_mid: str, limit: int = 3) -> List[dict]:
        """
        Fetch list of recent video entries from UP space.
        Prioritizes Bilibili's official dynamic feed API (HTTP 200, zero 412 block)
        with direct connection first, and falls back to proxy/yt-dlp if needed.
        """
        mid = self.extract_mid(space_url_or_mid)
        results = []

        # 1. Primary Method: Query Bilibili Official Dynamic Feed API (direct first, fallback to proxy)
        if mid:
            api_url = f"https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space?host_mid={mid}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Referer": f"https://space.bilibili.com/{mid}",
                "Origin": "https://space.bilibili.com",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "empty",
                "Sec-Fetch-Mode": "cors",
                "Sec-Fetch-Site": "same-site",
            }
            proxy_attempts = [None]
            if self.proxy:
                proxy_attempts.append({"http": self.proxy, "https": self.proxy})

            for p in proxy_attempts:
                mode_label = "direct connection" if p is None else f"proxy ({self.proxy})"
                try:
                    logger.info(f"Querying Bilibili dynamic feed for UP [mid: {mid}] via {mode_label}...")
                    resp = requests.get(api_url, headers=headers, cookies=self.cookies, proxies=p, timeout=10)
                    if resp.status_code == 200:
                        data_json = resp.json()
                        if data_json.get("code") == 0:
                            items = data_json.get("data", {}).get("items", []) or []
                            for it in items:
                                mod = it.get("modules") or {}
                                dyn = mod.get("module_dynamic") or {}
                                maj = dyn.get("major") or {}
                                if maj and isinstance(maj, dict) and maj.get("archive"):
                                    arc = maj["archive"]
                                    bvid = arc.get("bvid")
                                    title = arc.get("title")
                                    cover = arc.get("cover")
                                    desc = arc.get("desc")
                                    author = mod.get("module_author") or {}
                                    results.append({
                                        "video_id": bvid,
                                        "url": f"https://www.bilibili.com/video/{bvid}",
                                        "title": title,
                                        "cover": cover,
                                        "description": desc,
                                        "uploader": author.get("name"),
                                        "uploader_face": author.get("face"),
                                    })
                                    if len(results) >= limit:
                                        break
                            if results:
                                logger.info(f"Successfully retrieved {len(results)} recent video(s) via dynamic feed ({mode_label}).")
                                return results
                        else:
                            logger.warning(f"Bilibili dynamic feed returned code {data_json.get('code')}: {data_json.get('message')}")
                    else:
                        logger.warning(f"Bilibili dynamic feed returned HTTP {resp.status_code} via {mode_label}")
                except Exception as e:
                    logger.warning(f"Dynamic feed request via {mode_label} failed: {e}")

        # 2. Fallback Method: yt-dlp space video playlist extractor
        space_url = self.normalize_space_url(space_url_or_mid)
        logger.info(f"Scanning Bilibili UP space via yt-dlp: {space_url} (limit={limit})")

        opts = self._get_base_opts(use_proxy=bool(self.proxy))
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
            logger.error(f"Failed to scan space {space_url} via yt-dlp: {e}")
            return []

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
        opts = self._get_base_opts(use_proxy=False)
        opts["skip_download"] = True

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(video_url, download=False)
        except Exception as e:
            if self.proxy:
                logger.warning(f"Direct metadata fetch failed for [{video_id}] ({e}), retrying via proxy...")
                try:
                    opts_p = self._get_base_opts(use_proxy=True)
                    opts_p["skip_download"] = True
                    with yt_dlp.YoutubeDL(opts_p) as ydl_p:
                        return ydl_p.extract_info(video_url, download=False)
                except Exception as ep:
                    logger.error(f"Failed to fetch metadata for [{video_id}] via proxy: {ep}")
            return None

    def download_audio_for_video(self, video_id: str, output_dir: Path) -> Optional[dict]:
        """
        Download high quality audio (.m4a) and optional subtitles for a video.
        Uses direct Gbps CDN connection first for max speed (~1-2 seconds),
        and automatically falls back to CHINA_PROXY if direct download fails/blocks.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        video_url = f"https://www.bilibili.com/video/{video_id}"
        out_template = str(output_dir / f"{video_id}.%(ext)s")
        target_audio_file = output_dir / f"{video_id}.m4a"

        # Attempt order: 1) Direct high-speed download, 2) Fallback to proxy
        attempts = [False]
        if self.proxy:
            attempts.append(True)

        info = None
        last_err = None

        for use_proxy in attempts:
            mode_label = f"proxy ({self.proxy})" if use_proxy else "direct connection"
            logger.info(f"Attempting audio download for [{video_id}] via {mode_label}...")

            opts = self._get_base_opts(use_proxy=use_proxy)
            opts.update({
                "format": "ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio/best",
                "outtmpl": out_template,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ai-zh", "zh-Hans", "zh-CN", "zh"],
                "subtitlesformat": "vtt",
                "socket_timeout": 30,
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

            # Turbocharge download over Tailscale/proxy using aria2c (8 parallel connections)
            if shutil.which("aria2c"):
                opts["external_downloader"] = {"default": "aria2c"}
                opts["external_downloader_args"] = {
                    "aria2c": [
                        "-x", "8",
                        "-s", "8",
                        "-j", "8",
                        "-k", "1M",
                        "--file-allocation=none",
                        "--summary-interval=5",
                    ]
                }
            else:
                opts["concurrent_fragment_downloads"] = 5
                opts["buffersize"] = 1024 * 1024

            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(video_url, download=True)
                if target_audio_file.exists():
                    logger.info(f"Successfully downloaded audio for [{video_id}] via {mode_label}!")
                    break
                else:
                    candidates = list(output_dir.glob(f"{video_id}.*"))
                    audio_cand = [c for c in candidates if c.suffix.lower() in (".m4a", ".mp3", ".opus", ".aac")]
                    if audio_cand:
                        target_audio_file = audio_cand[0]
                        logger.info(f"Successfully downloaded audio for [{video_id}] via {mode_label}!")
                        break
            except Exception as e:
                last_err = e
                logger.warning(f"Audio download via {mode_label} failed ({e}).")
                # Remove partial files before next attempt
                for cand in output_dir.glob(f"{video_id}*.part"):
                    try:
                        cand.unlink()
                    except Exception:
                        pass

        if not target_audio_file.exists() or not info:
            logger.error(f"Failed to download audio for [{video_id}] after all attempts: {last_err}")
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
