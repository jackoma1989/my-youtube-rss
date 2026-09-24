import argparse
import json
import logging
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from podcast_sync.bilibili_fetcher import BilibiliFetcher, parse_bilibili_cookies
from podcast_sync.config import Config, sanitize_channel_id
from podcast_sync.feed import (
    PodcastChannel,
    PodcastEpisode,
    PodcastTranscript,
    generate_podcast_rss,
    validate_podcast_rss,
)
from podcast_sync.notifier import send_bilibili_episode_notification
from podcast_sync.storage import StorageManager

# Configure logging
LOG_DIR = PROJECT_ROOT / "output"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "bilibili_sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("bilibili_sync")


def setup_network_proxy():
    """Detect if proxy is explicitly configured."""
    if os.environ.get("CHINA_PROXY"):
        logger.info(f"Using CHINA_PROXY: {os.environ.get('CHINA_PROXY')}")


def sanitize_filename(name: str, max_length: int = 80) -> str:
    """Sanitize string for Windows filesystem filename."""
    cleaned = re.sub(r'[\/:*?"<>|\r\n\t]', "", name).strip()
    return cleaned[:max_length].rstrip(". ")


def get_icloud_dir(channel: dict) -> Optional[Path]:
    """Determine destination folder in iCloud (Drive X:)."""
    if not channel.get("icloud_backup", True):
        return None

    custom_dir = channel.get("icloud_dir")
    if custom_dir:
        p = Path(custom_dir)
        if p.drive.upper() == "X:" or p.exists():
            return p

    channel_name = channel.get("name", "Bilibili播客")
    x_drive = Path("X:/")
    if x_drive.exists():
        target = x_drive / channel_name
        return target

    return None


def prune_icloud_folder(icloud_dir: Path, retained_episodes: List[PodcastEpisode], max_episodes: int) -> int:
    """Prune iCloud backup folder to keep strictly at most max_episodes files."""
    if not icloud_dir.exists():
        return 0

    retained_titles = {sanitize_filename(ep.title).lower() for ep in retained_episodes}
    audio_files = [f for f in icloud_dir.glob("*.*") if f.suffix.lower() in (".m4a", ".mp3")]
    audio_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

    pruned = 0
    for f in audio_files:
        stem = f.stem.lower()
        matched = any(rt in stem or stem in rt for rt in retained_titles)
        if not matched or len(audio_files) - pruned > max_episodes:
            try:
                f.unlink()
                pruned += 1
                logger.info(f"Pruned older audio from iCloud: {f.name}")
            except Exception as e:
                logger.warning(f"Failed to delete {f.name} from iCloud: {e}")

    return pruned


def sync_single_bilibili_channel(
    channel_conf: dict,
    cfg: Config,
    storage: StorageManager,
    fetcher: BilibiliFetcher,
    force: bool = False,
) -> bool:
    channel_id = sanitize_channel_id(channel_conf.get("id", "bilibili_channel"))
    channel_name = channel_conf.get("name") or channel_id
    space_url = channel_conf.get("url") or (f"https://space.bilibili.com/{channel_conf.get('mid')}" if channel_conf.get("mid") else None)
    
    # Strictly respect 1 episode limit by default unless overridden
    max_episodes = int(channel_conf.get("max_episodes") or os.environ.get("BILI_MAX_EPISODES") or os.environ.get("MAX_EPISODES") or 1)
    category = channel_conf.get("category", "Technology")
    language = channel_conf.get("language", "zh-cn")
    description = channel_conf.get("description", f"{channel_name} Bilibili 音频播客")

    logger.info(f"=== Starting sync for Bilibili UP: [{channel_name}] (ID: {channel_id}, max_episodes: {max_episodes}) ===")

    if not space_url:
        logger.error(f"[{channel_name}] Missing 'url' or 'mid' in configuration.")
        return False

    channel_output_dir = cfg.output_dir / channel_id
    channel_output_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load manifest from R2
    manifest: List[PodcastEpisode] = storage.load_episodes_manifest(channel_id)
    existing_video_ids = {ep.video_id for ep in manifest}
    logger.info(f"[{channel_name}] Loaded {len(manifest)} existing episode(s) from R2 manifest.")

    # 2. Scan recent videos from space (fetch latest 2-3 to find latest new upload)
    recent_videos = fetcher.fetch_recent_videos(space_url, limit=max(3, max_episodes * 2))
    if not recent_videos:
        logger.warning(f"[{channel_name}] No videos retrieved from {space_url}")
        return False

    # Check if the latest video is already processed
    latest_video = recent_videos[0]
    latest_vid = latest_video["video_id"]
    if latest_vid in existing_video_ids and not force:
        logger.info(f"[{channel_name}] Latest video [{latest_vid}] is already in manifest and up to date.")
        return True

    # 3. Download the new episode(s) up to max_episodes
    newly_added_episodes: List[PodcastEpisode] = []
    icloud_dir = get_icloud_dir(channel_conf)
    if icloud_dir:
        icloud_dir.mkdir(parents=True, exist_ok=True)

    channel_cover_url = channel_conf.get("image_url")

    for v_entry in recent_videos[:max_episodes]:
        vid = v_entry["video_id"]
        if vid in existing_video_ids and not force:
            logger.info(f"[{channel_name}] Video [{vid}] already synced. Skipping.")
            continue

        logger.info(f"[{channel_name}] Downloading audio for [{vid}]...")
        dl_res = fetcher.download_audio_for_video(vid, channel_output_dir)
        if not dl_res:
            logger.error(f"[{channel_name}] Failed to download audio for [{vid}]. Skipping.")
            continue

        audio_file = dl_res["audio_file"]
        title = dl_res["title"]
        duration = dl_res["duration"]
        pub_date = dl_res["pub_date"]
        ep_description = dl_res["description"] or title
        webpage_url = dl_res["webpage_url"]
        thumb_url = dl_res["thumbnail_url"]

        if not channel_cover_url and thumb_url:
            channel_cover_url = thumb_url

        # 4. Upload audio to R2
        r2_audio_url = storage.upload_audio(audio_file, channel_id, vid)

        # 5. Upload subtitles if available
        transcripts: List[PodcastTranscript] = []
        for sub in dl_res.get("subtitles", []):
            try:
                sub_path = sub["local_path"]
                # Extract lang from filename e.g. BV123.ai-zh.vtt
                parts = sub_path.stem.split(".")
                lang_code = parts[-1] if len(parts) > 1 else "zh-CN"
                sub_url = storage.upload_transcript(sub_path, channel_id, vid, lang_code)
                transcripts.append(PodcastTranscript(url=sub_url, type="text/vtt", language=lang_code))
            except Exception as e:
                logger.warning(f"Failed to upload transcript for {vid}: {e}")

        # 6. Copy to iCloud backup if available
        if icloud_dir and not cfg.dry_run:
            safe_name = sanitize_filename(f"{channel_name} - {title}")
            dest_file = icloud_dir / f"{safe_name}{audio_file.suffix}"
            try:
                shutil.copy2(audio_file, dest_file)
                logger.info(f"Backed up to iCloud: {dest_file.name}")
            except Exception as e:
                logger.warning(f"Failed to copy to iCloud {dest_file}: {e}")

        # 7. Create Episode object
        episode = PodcastEpisode(
            video_id=vid,
            title=title,
            description=ep_description,
            pub_date=pub_date,
            duration_seconds=duration,
            audio_filename=f"audio/{channel_id}/{vid}{audio_file.suffix}",
            audio_url=r2_audio_url,
            file_size_bytes=dl_res["file_size"],
            thumbnail_url=thumb_url,
            webpage_url=webpage_url,
            transcripts=transcripts,
            transcripts_checked=True,
        )
        newly_added_episodes.append(episode)
        manifest = [ep for ep in manifest if ep.video_id != vid]
        manifest.insert(0, episode)

    if not newly_added_episodes and not force:
        logger.info(f"[{channel_name}] No new episodes downloaded.")
        return True

    # 8. Sort manifest descending by date & enforce max_episodes limit (default 1)
    unique_episodes = {ep.video_id: ep for ep in manifest}
    sorted_manifest = sorted(unique_episodes.values(), key=lambda ep: ep.pub_date, reverse=True)

    retained = sorted_manifest[:max_episodes]
    expired = sorted_manifest[max_episodes:]

    if expired:
        logger.info(f"[{channel_name}] Enforcing retention limit ({max_episodes}). Pruning {len(expired)} older episode(s)...")
        for exp in expired:
            logger.info(f"Deleting expired episode: [{exp.video_id}] {exp.title}")
            storage.delete_audio(channel_id, exp.video_id)
            storage.delete_transcripts(channel_id, exp.video_id)
            if not cfg.dry_run:
                for cand in channel_output_dir.glob(f"{exp.video_id}.*"):
                    try:
                        cand.unlink()
                    except Exception:
                        pass

        # Reconcile R2 audio & transcripts directory to ensure zero orphaned files
        storage.cleanup_orphan_and_expired_audio(channel_id, retained)
        storage.cleanup_orphan_and_expired_transcripts(channel_id, retained)

        if icloud_dir and not cfg.dry_run:
            pruned_ic = prune_icloud_folder(icloud_dir, retained, max_episodes)
            if pruned_ic > 0:
                logger.info(f"Pruned {pruned_ic} expired episode(s) from iCloud.")

    # 9. Save updated manifest
    storage.save_episodes_manifest(channel_id, retained)

    # 10. Generate and Upload Podcast RSS XML
    feed_channel = PodcastChannel(
        title=channel_name,
        link=space_url,
        description=description,
        author=channel_name,
        image_url=channel_cover_url,
        language=language,
        category=category,
        episodes=retained,
    )
    rss_xml = generate_podcast_rss(feed_channel)
    validate_podcast_rss(rss_xml, channel_id, expected_count=len(retained))
    feed_url = storage.upload_channel_feed(channel_id, rss_xml)
    logger.info(f"[{channel_name}] RSS Feed successfully published: {feed_url}")

    # 11. Send Telegram Notification
    if not cfg.dry_run and cfg.telegram_bot_token and cfg.telegram_chat_id:
        for ep in newly_added_episodes:
            logger.info(f"Sending Telegram notification for [{ep.title}]...")
            send_bilibili_episode_notification(
                bot_token=cfg.telegram_bot_token,
                chat_id=cfg.telegram_chat_id,
                creator_name=channel_name,
                episode=ep,
                feed_url=feed_url,
            )
    elif cfg.dry_run:
        logger.info(f"[DRY RUN] Would send {len(newly_added_episodes)} Telegram notification(s).")

    logger.info(f"=== Successfully completed sync for [{channel_name}] ===")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync Bilibili UP spaces to Apple Podcasts RSS feeds.")
    parser.add_argument("--config", default="bilibili_channels.json", help="Path to bilibili_channels.json")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without uploading to R2")
    parser.add_argument("--force", action="store_true", help="Force re-download and re-generate feeds")
    args = parser.parse_args()

    config_file = Path(args.config)
    channels_data = []

    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                if isinstance(loaded, list):
                    channels_data = loaded
        except Exception as e:
            logger.warning(f"Failed to read {config_file}: {e}")

    # Support channel override via environment variables (BILIBILI_CHANNEL_URL, etc.)
    env_channels = [
        (k, v.strip()) for k, v in os.environ.items() if k.startswith("BILIBILI_CHANNEL_URL") and v.strip()
    ]
    if env_channels:
        merged = []
        json_by_url = {c.get("url", "").rstrip("/"): c for c in channels_data if c.get("url")}
        for env_k, url_val in env_channels:
            norm = url_val.rstrip("/")
            if norm in json_by_url:
                merged.append(json_by_url[norm])
            else:
                suffix = env_k.replace("BILIBILI_CHANNEL_URL", "").strip("_")
                cid = os.environ.get(f"BILIBILI_CHANNEL_ID_{suffix}") or os.environ.get("BILIBILI_CHANNEL_ID") or f"bili_{suffix.lower() if suffix else 'channel'}"
                cname = os.environ.get(f"BILIBILI_CHANNEL_NAME_{suffix}") or os.environ.get("BILIBILI_CHANNEL_NAME") or cid
                merged.append({
                    "id": cid,
                    "name": cname,
                    "url": url_val,
                    "max_episodes": int(os.environ.get("BILI_MAX_EPISODES") or os.environ.get("MAX_EPISODES") or 1),
                    "category": os.environ.get("PODCAST_CATEGORY", "Technology"),
                    "language": os.environ.get("PODCAST_LANGUAGE", "zh-cn"),
                    "enabled": True,
                })
        # Add remaining enabled channels from config
        seen_urls = {c.get("url", "").rstrip("/") for c in merged}
        for c in channels_data:
            if c.get("enabled", True) and c.get("url", "").rstrip("/") not in seen_urls:
                merged.append(c)
        channels_data = merged

    if not channels_data:
        logger.error(f"No Bilibili channels found in {config_file} or environment variables.")
        return 1

    setup_network_proxy()

    cfg = Config.from_env()
    if args.dry_run:
        cfg.dry_run = True

    storage = StorageManager(cfg)
    storage.abort_incomplete_multipart_uploads()
    fetcher = BilibiliFetcher()

    success_count = 0
    for channel in channels_data:
        if not channel.get("enabled", True):
            logger.info(f"Skipping disabled channel: {channel.get('name')}")
            continue
        try:
            ok = sync_single_bilibili_channel(channel, cfg, storage, fetcher, force=args.force)
            if ok:
                success_count += 1
        except Exception as e:
            logger.error(f"Error syncing channel {channel.get('name')}: {e}", exc_info=True)

    logger.info(f"Finished Bilibili Podcast Sync. Successfully processed {success_count}/{len(channels_data)} channel(s).")
    return 0 if success_count > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
