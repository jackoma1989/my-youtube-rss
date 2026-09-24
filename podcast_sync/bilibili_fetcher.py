import logging
import os
import re
import shutil
import subprocess
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
    Bilibili API defaults to returning PCDN (mcdn.bilivideo.cn / mountaintoys.cn / szbdyd.com) in 'baseUrl',
    which severely throttles download speeds to ~100 KB/s.
    This monkey-patch inspects 'backupUrl' and promotes high-speed official
    UPOS / Cloud CDN mirrors (cn-*.bilivideo.com, upos-sz-*.bilivideo.com, *.akamaized.net)
    as primary baseUrl and retains all mirrors in 'backup_urls' for automatic retry.
    """
    try:
        from yt_dlp.extractor.bilibili import BiliBiliIE
        if getattr(BiliBiliIE, "_cdn_patched", False):
            return

        orig_extract_formats = BiliBiliIE.extract_formats

        def filter_fast_mirrors(base, backups):
            all_u = ([base] if base else []) + list(backups or [])
            fast = []
            for u in all_u:
                if not u:
                    continue
                # Exclude PCDN / slow peer nodes
                if any(p in u for p in ("mcdn", "mountaintoys", "szbdyd", "p2p")):
                    continue
                if any(k in u for k in ("bilivideo.com", "upos", "akamaized")):
                    if u not in fast:
                        fast.append(u)
            return fast

        def fast_extract_formats(self, play_info):
            dash = play_info.get("dash") or {}
            for stream_type in ("audio", "video"):
                for s in dash.get(stream_type) or []:
                    base = s.get("baseUrl") or s.get("base_url") or ""
                    backups = s.get("backupUrl") or s.get("backup_url") or []
                    fast = filter_fast_mirrors(base, backups)
                    if fast:
                        s["baseUrl"] = fast[0]
                        if "base_url" in s:
                            s["base_url"] = fast[0]
                        s["backup_urls"] = fast

            for d in play_info.get("durl") or []:
                base = d.get("url") or ""
                backups = d.get("backup_url") or []
                fast = filter_fast_mirrors(base, backups)
                if fast:
                    d["url"] = fast[0]
                    d["backup_urls"] = fast

            formats = orig_extract_formats(self, play_info)

            # Propagate backup_urls into extracted format dictionaries
            for f in formats:
                f_url = f.get("url") or ""
                for stream_type in ("audio", "video"):
                    for s in dash.get(stream_type) or []:
                        if s.get("id") == f.get("format_id") or (f_url and s.get("baseUrl") == f_url):
                            if "backup_urls" in s:
                                f["backup_urls"] = s["backup_urls"]
                for d in play_info.get("durl") or []:
                    if f_url and (d.get("url") == f_url or str(d.get("order")) == str(f.get("format_id"))):
                        if "backup_urls" in d:
                            f["backup_urls"] = d["backup_urls"]

            return formats

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

    def get_creator_info(self, mid: str) -> Optional[dict]:
        """Fetch creator name and avatar face from Bilibili user card API."""
        if not mid or not str(mid).isdigit():
            return None
        api_url = f"https://api.bilibili.com/x/web-interface/card?mid={mid}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }
        attempts = [None]
        if self.proxy:
            attempts.append({"http": self.proxy, "https": self.proxy})

        for p in attempts:
            try:
                resp = requests.get(api_url, headers=headers, cookies=self.cookies, proxies=p, timeout=8)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("code") == 0:
                        card = data.get("data", {}).get("card", {})
                        name = card.get("name")
                        face = card.get("face")
                        sign = card.get("sign")
                        if name:
                            return {
                                "mid": mid,
                                "name": name,
                                "face": face,
                                "sign": sign,
                            }
            except Exception as e:
                logger.debug(f"Failed to fetch creator info for mid {mid}: {e}")
        return None

    def download_image(self, image_url: str, output_path: Path) -> bool:
        """Download cover or avatar image (tries direct first, fallback to proxy)."""
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if image_url.startswith("//"):
            image_url = f"https:{image_url}"

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }

        # 1. Try direct download first
        try:
            resp = requests.get(image_url, headers=headers, timeout=15)
            if resp.status_code == 200 and len(resp.content) > 500:
                output_path.write_bytes(resp.content)
                logger.info(f"Downloaded Bilibili image directly to {output_path}")
                return True
        except Exception:
            pass

        # 2. Fallback to proxy
        if self.proxy:
            try:
                proxies = {"http": self.proxy, "https": self.proxy}
                resp = requests.get(image_url, headers=headers, proxies=proxies, timeout=20)
                if resp.status_code == 200 and len(resp.content) > 500:
                    output_path.write_bytes(resp.content)
                    logger.info(f"Downloaded Bilibili image via proxy to {output_path}")
                    return True
            except Exception as e:
                logger.warning(f"Failed to download image from {image_url} via proxy: {e}")

        return False

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
                    current_offset = ""
                    feed_results = []
                    seen_bvids = set()
                    while len(feed_results) < limit:
                        page_url = api_url if not current_offset else f"{api_url}&offset={current_offset}"
                        resp = requests.get(page_url, headers=headers, cookies=self.cookies, proxies=p, timeout=10)
                        if resp.status_code != 200:
                            logger.warning(f"Bilibili dynamic feed returned HTTP {resp.status_code} via {mode_label}")
                            break
                        data_json = resp.json()
                        if data_json.get("code") != 0:
                            logger.warning(f"Bilibili dynamic feed returned code {data_json.get('code')}: {data_json.get('message')}")
                            break
                        data_data = data_json.get("data") or {}
                        items = data_data.get("items") or []
                        if not items:
                            break
                        for it in items:
                            mod = it.get("modules") or {}
                            dyn = mod.get("module_dynamic") or {}
                            maj = dyn.get("major") or {}
                            if maj and isinstance(maj, dict) and maj.get("archive"):
                                arc = maj["archive"]
                                bvid = arc.get("bvid")
                                if not bvid or bvid in seen_bvids:
                                    continue
                                seen_bvids.add(bvid)
                                title = arc.get("title")
                                cover = arc.get("cover")
                                desc = arc.get("desc")
                                author = mod.get("module_author") or {}
                                feed_results.append({
                                    "video_id": bvid,
                                    "url": f"https://www.bilibili.com/video/{bvid}",
                                    "title": title,
                                    "cover": cover,
                                    "description": desc,
                                    "uploader": author.get("name"),
                                    "uploader_face": author.get("face"),
                                })
                                if len(feed_results) >= limit:
                                    break
                        has_more = data_data.get("has_more", False)
                        new_offset = data_data.get("offset", "")
                        if not has_more or not new_offset or new_offset == current_offset:
                            break
                        current_offset = new_offset

                    if feed_results:
                        logger.info(f"Successfully retrieved {len(feed_results)} recent video(s) via dynamic feed ({mode_label}).")
                        return feed_results
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
        opts["writesubtitles"] = True
        opts["writeautomaticsub"] = True
        opts["subtitleslangs"] = ["ai-zh", "zh-Hans", "zh-CN", "zh"]

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                return ydl.extract_info(video_url, download=False)
        except Exception as e:
            if self.proxy:
                logger.warning(f"Direct metadata fetch failed for [{video_id}] ({e}), retrying via proxy...")
                try:
                    opts_p = self._get_base_opts(use_proxy=True)
                    opts_p["skip_download"] = True
                    opts_p["writesubtitles"] = True
                    opts_p["writeautomaticsub"] = True
                    opts_p["subtitleslangs"] = ["ai-zh", "zh-Hans", "zh-CN", "zh"]
                    with yt_dlp.YoutubeDL(opts_p) as ydl_p:
                        return ydl_p.extract_info(video_url, download=False)
                except Exception as ep:
                    logger.error(f"Failed to fetch metadata for [{video_id}] via proxy: {ep}")
            return None

    def download_audio_for_video(self, video_id: str, output_dir: Path) -> Optional[dict]:
        """
        Download high quality audio (.m4a) and optional subtitles for a video.
        Uses intelligent Split-Proxy:
        1. Metadata & playurl extracted (via proxy if direct returns 412),
        2. Audio stream downloaded DIRECTLY from official Bilibili UPOS CDN (10-50 MB/s, ~1s),
        3. Gracefully falls back to proxy yt-dlp download if direct CDN streaming fails.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        video_url = f"https://www.bilibili.com/video/{video_id}"
        out_template = str(output_dir / f"{video_id}.%(ext)s")
        target_audio_file = output_dir / f"{video_id}.m4a"

        # 1. Fetch metadata and format list (direct first, fallback to proxy)
        info = self.fetch_video_metadata(video_id)
        if not info:
            logger.error(f"Failed to fetch metadata for [{video_id}]")
            return None

        # 2. Extract formats
        all_formats = info.get("formats", [])
        audio_formats = [f for f in all_formats if f.get("vcodec") == "none" and f.get("url")]
        audio_formats.sort(key=lambda x: (x.get("tbr") or x.get("abr") or 0), reverse=True)

        download_success = False

        cdn_headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Referer": "https://www.bilibili.com/",
        }

        def try_download_urls(candidate_urls: List[str], dest_file: Path, desc: str) -> bool:
            for idx, cdn_url in enumerate(candidate_urls):
                if not cdn_url:
                    continue
                mirror_host = cdn_url.split("/")[2] if "/" in cdn_url else "cdn"
                logger.info(f"Attempting direct high-speed CDN {desc} from [{mirror_host}] (mirror {idx+1}/{len(candidate_urls)})...")
                try:
                    t0 = time.time()
                    with requests.get(cdn_url, headers=cdn_headers, timeout=(10, 30), stream=True) as r:
                        if r.status_code in (200, 206):
                            with open(dest_file, "wb") as f_out:
                                for chunk in r.iter_content(chunk_size=65536):
                                    if chunk:
                                        f_out.write(chunk)
                            dt = time.time() - t0
                            mb = dest_file.stat().st_size / (1024 * 1024)
                            if mb > 0.05:  # At least 50 KB
                                speed_mb = mb / max(dt, 0.001)
                                logger.info(f"Successfully downloaded {desc} via direct CDN [{mirror_host}] in {dt:.2f}s ({mb:.2f} MB at {speed_mb:.2f} MB/s)!")
                                return True
                            else:
                                dest_file.unlink(missing_ok=True)
                        else:
                            logger.warning(f"Direct CDN [{mirror_host}] returned HTTP {r.status_code}")
                except Exception as e:
                    logger.warning(f"Direct CDN [{mirror_host}] attempt failed: {e}")
                    dest_file.unlink(missing_ok=True)
            return False

        # 3. FAST PATH A: Standalone DASH audio stream (multi-mirror retry)
        if audio_formats:
            best_audio = audio_formats[0]
            candidate_urls = []
            if best_audio.get("url"):
                candidate_urls.append(best_audio["url"])
            if best_audio.get("backup_urls"):
                candidate_urls.extend(best_audio["backup_urls"])
            candidate_urls = list(dict.fromkeys(candidate_urls))

            if try_download_urls(candidate_urls, target_audio_file, f"audio for [{video_id}]"):
                download_success = True

        # 3. FAST PATH B: Muxed video (durl / MP4), direct download lowest 360P video & extract audio in 0.1s
        if not download_success:
            muxed_formats = [f for f in all_formats if f.get("url")]
            # Sort by filesize ascending, or tbr ascending, to pick lowest resolution (360P)
            muxed_formats.sort(key=lambda x: (x.get("filesize") or 999999999, x.get("tbr") or 999999999))
            if muxed_formats:
                lowest_muxed = muxed_formats[0]
                temp_video_file = output_dir / f"{video_id}.temp.mp4"
                muxed_urls = []
                if lowest_muxed.get("url"):
                    muxed_urls.append(lowest_muxed["url"])
                if lowest_muxed.get("backup_urls"):
                    muxed_urls.extend(lowest_muxed["backup_urls"])
                muxed_urls = list(dict.fromkeys(muxed_urls))

                fmt_label = f"{lowest_muxed.get('format_id') or '360P'} muxed video"
                if try_download_urls(muxed_urls, temp_video_file, f"{fmt_label} for [{video_id}]"):
                    # Extract audio with ffmpeg stream copy
                    try:
                        logger.info(f"Extracting pure audio track from {temp_video_file.name} via ffmpeg stream copy...")
                        res = subprocess.run(
                            ["ffmpeg", "-y", "-i", str(temp_video_file), "-vn", "-c:a", "copy", str(target_audio_file)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE,
                        )
                        if res.returncode != 0 or not target_audio_file.exists() or target_audio_file.stat().st_size < 1024:
                            # Fallback to AAC encoding if copy failed
                            logger.info(f"Stream copy failed ({res.stderr.decode(errors='ignore')[:100]}), re-encoding to AAC...")
                            subprocess.run(
                                ["ffmpeg", "-y", "-i", str(temp_video_file), "-vn", "-c:a", "aac", "-b:a", "128k", str(target_audio_file)],
                                check=True,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                        if target_audio_file.exists() and target_audio_file.stat().st_size > 1024:
                            mb = target_audio_file.stat().st_size / (1024 * 1024)
                            logger.info(f"Successfully extracted {mb:.2f} MB pure audio from muxed video in <1s!")
                            download_success = True
                    except Exception as e:
                        logger.error(f"FFmpeg extraction failed for [{video_id}]: {e}")
                    finally:
                        temp_video_file.unlink(missing_ok=True)

        # 4. SLOW PATH FALLBACK: If direct CDN download failed, use yt-dlp through proxy
        # Strictly download lowest resolution video to avoid 100+ MB proxy downloads!
        if not download_success or not target_audio_file.exists():
            mode_label = f"proxy ({self.proxy})" if self.proxy else "direct connection"
            logger.info(f"Falling back to audio download for [{video_id}] via {mode_label}...")
            opts = self._get_base_opts(use_proxy=bool(self.proxy))
            opts.update({
                "format": "ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio/worstvideo[ext=mp4]+bestaudio/worst[ext=mp4]/worst",
                "outtmpl": out_template,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["ai-zh", "zh-Hans", "zh-CN", "zh"],
                "subtitlesformat": "vtt",
                "socket_timeout": 30,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
            })
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([video_url])
                if target_audio_file.exists():
                    download_success = True
                else:
                    candidates = list(output_dir.glob(f"{video_id}.*"))
                    audio_cand = [c for c in candidates if c.suffix.lower() in (".m4a", ".mp3", ".opus", ".aac")]
                    if audio_cand:
                        target_audio_file = audio_cand[0]
                        download_success = True
            except Exception as ep:
                logger.error(f"Fallback download failed for [{video_id}]: {ep}")

        # 5. Extract subtitles from metadata if available
        sub_dict = info.get("subtitles") or info.get("automatic_captions") or {}
        for lang in ("ai-zh", "zh-Hans", "zh-CN", "zh"):
            if lang in sub_dict:
                for sub_item in sub_dict[lang]:
                    sub_target = output_dir / f"{video_id}.{lang}.vtt"
                    if "data" in sub_item and sub_item["data"]:
                        try:
                            data_str = sub_item["data"]
                            if not data_str.startswith("WEBVTT"):
                                vtt_lines = ["WEBVTT\n"]
                                for line in data_str.splitlines():
                                    if " --> " in line:
                                        line = re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", line)
                                    vtt_lines.append(line)
                                data_str = "\n".join(vtt_lines)
                            sub_target.write_text(data_str, encoding="utf-8")
                            break
                        except Exception:
                            pass
                    elif sub_item.get("url"):
                        try:
                            sr = requests.get(sub_item["url"], timeout=10)
                            if sr.status_code == 200:
                                sub_target.write_text(sr.text, encoding="utf-8")
                                break
                        except Exception:
                            pass

        if not target_audio_file.exists():
            logger.error(f"Failed to download audio for [{video_id}] after all attempts")
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
