import argparse
import asyncio
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

from podcast_sync.config import Config
from podcast_sync.douyin_db import DouyinDatabase
from podcast_sync.douyin_fetcher import DouyinFetcher
from podcast_sync.feed import (
    PodcastChannel,
    PodcastEpisode,
    generate_podcast_rss,
    validate_podcast_rss,
)
from podcast_sync.notifier import send_douyin_episode_notification
from podcast_sync.storage import StorageManager

# Configure logging
LOG_DIR = PROJECT_ROOT / "output"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / "douyin_sync.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
logger = logging.getLogger("douyin_sync")


def setup_network_proxy():
    """Detect if local proxy (e.g. Clash/V2Ray on 127.0.0.1:7890) is available on local machine."""
    # If CHINA_PROXY is set (e.g. GitHub Actions selective routing), keep global network direct!
    if os.environ.get("CHINA_PROXY"):
        return
    if os.environ.get("HTTP_PROXY") or os.environ.get("HTTPS_PROXY"):
        return
    import socket
    s = socket.socket()
    s.settimeout(0.5)
    try:
        if s.connect_ex(("127.0.0.1", 7890)) == 0:
            os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
            os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
            logger.info("Auto-detected and enabled local proxy (http://127.0.0.1:7890) for high-speed Cloudflare R2 uploads.")
    except Exception:
        pass
    finally:
        s.close()




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

    channel_name = channel.get("name", "抖音播客")
    x_drive = Path("X:/")
    if x_drive.exists():
        target = x_drive / channel_name
        return target

    return None


def prune_icloud_folder(icloud_dir: Path, retained_episodes: List[PodcastEpisode], max_episodes: int) -> int:
    """Prune iCloud backup folder to keep strictly at most max_episodes files."""
    if not icloud_dir.exists():
        return 0

    retained_titles = {sanitize_filename(ep.title) for ep in retained_episodes}
    retained_ids = {ep.video_id for ep in retained_episodes}

    deleted_count = 0
    mp3_files = sorted(
        icloud_dir.glob("*.mp3"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )

    for f in mp3_files:
        # Check if file belongs to any retained episode
        is_retained = False
        for tid in retained_ids:
            if tid in f.stem:
                is_retained = True
                break
        if not is_retained:
            for t_title in retained_titles:
                if t_title and t_title in f.stem:
                    is_retained = True
                    break

        if not is_retained:
            try:
                f.unlink()
                logger.info(f"Pruned expired iCloud audio: {f.name}")
                deleted_count += 1
            except Exception as e:
                logger.warning(f"Failed to delete iCloud file {f}: {e}")

    # Extra safety check: if files count still exceeds max_episodes, delete oldest
    remaining_files = sorted(
        icloud_dir.glob("*.mp3"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if len(remaining_files) > max_episodes:
        for extra in remaining_files[max_episodes:]:
            try:
                extra.unlink()
                logger.info(f"Pruned extra iCloud audio by count: {extra.name}")
                deleted_count += 1
            except Exception as e:
                logger.warning(f"Failed to delete extra iCloud file {extra}: {e}")

    return deleted_count


async def sync_channel(
    channel: dict,
    cfg: Config,
    storage: StorageManager,
    db: DouyinDatabase,
    fetcher: DouyinFetcher,
    force: bool = False,
) -> bool:
    channel_id = channel.get("id")
    channel_name = channel.get("name", channel_id)
    sec_uid = channel.get("sec_uid", "").strip()
    url = channel.get("url", "").strip()
    max_episodes = int(channel.get("max_episodes", 15))
    category = channel.get("category", "Business")
    language = channel.get("language", "zh-cn")
    description = channel.get("description", f"{channel_name} 抖音音频播客")

    logger.info(f"=== Starting sync for [{channel_name}] ({channel_id}) ===")

    # 1. Resolve sec_uid if needed
    if not sec_uid or sec_uid.startswith("http"):
        resolved = fetcher.resolve_sec_uid(url or sec_uid)
        if resolved and not resolved.startswith("http"):
            sec_uid = resolved
            channel["sec_uid"] = sec_uid
            logger.info(f"Resolved sec_uid: {sec_uid}")
        else:
            logger.error(f"Failed to resolve sec_uid for [{channel_name}] from '{url}'")
            return False

    # 2. Load existing manifest from R2
    manifest: List[PodcastEpisode] = storage.load_episodes_manifest(channel_id)
    logger.info(f"[{channel_id}] Current R2 manifest has {len(manifest)} episode(s).")

    # Seed local database with any known episodes from manifest
    db.sync_from_manifest(channel_id, manifest)
    seen_ids = db.get_seen_ids(channel_id)

    # 3. Fetch latest creator posts from Douyin API
    posts = await fetcher.fetch_creator_posts(sec_user_id=sec_uid, max_posts=max_episodes)
    if not posts:
        logger.warning(f"[{channel_name}] No posts fetched from Douyin API. Skipping.")
        return False

    # Auto-detect creator name from posts if unset (e.g. added via GitHub Secret)
    if not channel.get("name") and posts:
        author_name = posts[0].get("author_name")
        if author_name:
            channel_name = author_name
            channel["name"] = author_name
            description = f"{channel_name} 抖音音频播客"
            logger.info(f"Auto-detected creator name: {channel_name}")

    logger.info(f"[{channel_name}] Fetched {len(posts)} recent post(s) from Douyin.")

    # 4. Handle channel cover image
    channel_output_dir = cfg.output_dir / channel_id
    channel_output_dir.mkdir(parents=True, exist_ok=True)
    cover_file = channel_output_dir / "cover.jpg"
    channel_cover_url = f"{cfg.r2_public_url}/covers/{channel_id}.jpg"

    # If cover doesn't exist locally, try downloading creator avatar
    if not cover_file.exists():
        for p in posts:
            avatar_url = p.get("avatar_url")
            if avatar_url:
                if fetcher.download_image(avatar_url, cover_file):
                    storage.upload_cover(cover_file, channel_id)
                    break

    # 5. Clean & Deduplicate existing manifest
    deduped_manifest = []
    seen_titles = set()
    manifest_changed = False
    for ep in sorted(manifest, key=lambda x: x.pub_date, reverse=True):
        clean_title = sanitize_filename(ep.title).strip().lower()
        if clean_title in seen_titles:
            logger.info(f"Removing duplicate title from manifest: [{ep.video_id}] {ep.title}")
            storage.delete_audio(channel_id, ep.video_id)
            manifest_changed = True
            continue
        seen_titles.add(clean_title)
        deduped_manifest.append(ep)
    if manifest_changed:
        manifest = deduped_manifest
        storage.save_episodes_manifest(channel_id, manifest)

    # 6. Identify new episodes
    manifest_ids = {ep.video_id for ep in manifest}
    new_posts = []
    for p in posts:
        vid = p["video_id"]
        clean_p_title = sanitize_filename(p["title"]).strip().lower()
        # Skip if already in active manifest on R2 (by ID or title)
        if vid in manifest_ids or clean_p_title in seen_titles:
            continue
        # Download if never seen, or forced, or if feed has fewer than max_episodes
        if vid not in seen_ids or force or len(manifest) + len(new_posts) < max_episodes:
            new_posts.append(p)



    if not new_posts:
        logger.info(f"[{channel_name}] Up to date. No new episodes found.")
        # Ensure RSS XML is present and matches current manifest
        if manifest:
            feed_channel = PodcastChannel(
                title=channel_name,
                link=url or f"https://www.douyin.com/user/{sec_uid}",
                description=description,
                author=channel_name,
                image_url=channel_cover_url,
                language=language,
                category=category,
                episodes=manifest,
            )
            rss_xml = generate_podcast_rss(feed_channel)
            validate_podcast_rss(rss_xml, channel_id, expected_count=len(manifest))
            storage.upload_channel_feed(channel_id, rss_xml)
        return True

    logger.info(f"[{channel_name}] Found {len(new_posts)} new episode(s) to download.")

    # 6. Process new episodes (older first, so newest is added last)
    newly_added_episodes: List[PodcastEpisode] = []
    icloud_dir = get_icloud_dir(channel)
    if icloud_dir:
        icloud_dir.mkdir(parents=True, exist_ok=True)

    for post in reversed(new_posts):
        vid = post["video_id"]
        title = post["title"]
        audio_url = post["audio_url"]
        pub_date = post["pub_date"]
        duration = post["duration_seconds"]

        if not audio_url:
            logger.warning(f"[{channel_name}] Skipping [{vid}] '{title}': No audio play_url.")
            continue

        temp_audio_path = channel_output_dir / f"{vid}.mp3"
        file_size = 0

        if cfg.dry_run:
            logger.info(f"[DRY RUN] Simulating download for [{title}] ({vid})")
            file_size = 5000000
        else:
            logger.info(f"Downloading audio for [{title}] ({vid})...")
            if not fetcher.download_audio(audio_url, temp_audio_path):
                logger.error(f"Failed to download audio for [{vid}]. Skipping.")
                continue
            file_size = temp_audio_path.stat().st_size

            # Backup to iCloud if enabled
            if icloud_dir:
                pub_date_str = pub_date.strftime("%Y%m%d")
                clean_title = sanitize_filename(title)
                icloud_file = icloud_dir / f"{channel_name} - {pub_date_str} - {clean_title}.mp3"
                try:
                    shutil.copy2(temp_audio_path, icloud_file)
                    logger.info(f"Backed up to iCloud: {icloud_file.name}")
                except Exception as e:
                    logger.warning(f"Failed to copy to iCloud {icloud_file}: {e}")

        # Upload MP3 to R2
        r2_audio_url = storage.upload_audio(temp_audio_path, channel_id, vid)

        # Create PodcastEpisode object
        episode = PodcastEpisode(
            video_id=vid,
            title=title,
            description=post["description"],
            pub_date=pub_date,
            duration_seconds=duration,
            audio_filename=f"audio/{channel_id}/{vid}.mp3",
            audio_url=r2_audio_url,
            file_size_bytes=file_size,
            thumbnail_url=post["cover_url"],
            webpage_url=post["webpage_url"],
        )

        manifest.append(episode)
        newly_added_episodes.append(episode)

        # Record in local SQLite database
        if not cfg.dry_run:
            db.mark_seen(
                channel_id=channel_id,
                aweme_id=vid,
                title=title,
                duration=duration,
                pub_date=pub_date.isoformat(),
                r2_url=r2_audio_url,
            )

    # 7. Deduplicate by video_id & title, and Sort manifest by pub_date descending
    unique_episodes = {}
    for ep in manifest:
        unique_episodes[ep.video_id] = ep

    sorted_by_date = sorted(unique_episodes.values(), key=lambda ep: ep.pub_date, reverse=True)
    seen_titles = set()
    sorted_manifest = []
    for ep in sorted_by_date:
        clean_title = sanitize_filename(ep.title).strip().lower()
        if clean_title in seen_titles:
            logger.info(f"Removing duplicate title episode from manifest: [{ep.video_id}] {ep.title}")
            storage.delete_audio(channel_id, ep.video_id)
            continue
        seen_titles.add(clean_title)
        sorted_manifest.append(ep)

    # 8. Enforce 15-episode retention limit
    retained = sorted_manifest[:max_episodes]
    expired = sorted_manifest[max_episodes:]

    if expired:
        logger.info(f"[{channel_name}] Enforcing retention limit ({max_episodes}). Pruning {len(expired)} expired episode(s)...")
        for exp in expired:
            logger.info(f"Deleting expired episode: [{exp.video_id}] {exp.title}")
            # Delete from R2
            storage.delete_audio(channel_id, exp.video_id)
            # Delete from local cache
            if not cfg.dry_run:
                loc_f = channel_output_dir / f"{exp.video_id}.mp3"
                if loc_f.exists():
                    try:
                        loc_f.unlink()
                    except Exception as e:
                        logger.debug(f"Failed to delete local cache {loc_f}: {e}")

        # Reconcile R2 audio directory
        storage.cleanup_orphan_and_expired_audio(channel_id, retained)

        # Prune iCloud directory
        if icloud_dir and not cfg.dry_run:
            pruned_ic = prune_icloud_folder(icloud_dir, retained, max_episodes)
            if pruned_ic > 0:
                logger.info(f"Pruned {pruned_ic} expired episode(s) from iCloud.")

    # 9. Save updated manifest to R2 and local disk
    storage.save_episodes_manifest(channel_id, retained)

    # 10. Generate and Upload Apple Podcasts RSS Feed XML
    feed_channel = PodcastChannel(
        title=channel_name,
        link=url or f"https://www.douyin.com/user/{sec_uid}",
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

    # 11. Send Telegram Notification for new episodes
    if not cfg.dry_run and cfg.telegram_bot_token and cfg.telegram_chat_id:
        for ep in newly_added_episodes:
            logger.info(f"Sending Telegram notification for [{ep.title}]...")
            send_douyin_episode_notification(
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


async def main_async(args: argparse.Namespace) -> int:
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

    # Check for channels configured via environment variables (e.g. GitHub Secrets)
    env_channel_items = [
        (env_k, env_v.strip())
        for env_k, env_v in os.environ.items()
        if env_k.startswith("DOUYIN_CHANNEL_URL") and env_v.strip()
    ]

    if env_channel_items:
        json_by_url = {ch.get("url", "").rstrip("/"): ch for ch in channels_data if ch.get("url")}
        json_by_sec = {ch.get("sec_uid", ""): ch for ch in channels_data if ch.get("sec_uid")}
        merged_channels = []
        for env_k, url_val in env_channel_items:
            norm_url = url_val.rstrip("/")
            matched = json_by_url.get(norm_url)
            if not matched:
                for sec, ch in json_by_sec.items():
                    if sec and sec in url_val:
                        matched = ch
                        break
            if matched:
                merged_channels.append(matched)
            else:
                suffix = env_k.replace("DOUYIN_CHANNEL_URL", "").strip("_").lower()
                raw_id = f"douyin_{suffix}" if suffix else "douyin_channel"
                merged_channels.append({
                    "id": raw_id,
                    "url": url_val,
                    "name": None,
                    "max_episodes": int(os.environ.get("MAX_EPISODES", 15)),
                    "category": os.environ.get("PODCAST_CATEGORY", "Business"),
                    "language": os.environ.get("PODCAST_LANGUAGE", "zh-cn"),
                    "enabled": True,
                })
        channels_data = merged_channels

    if not channels_data:
        logger.error(f"No channels found in {config_file} or DOUYIN_CHANNEL_URL environment variables.")
        return 1

    setup_network_proxy()

    # Load system config & storage
    cfg = Config.from_env()
    if args.dry_run:
        cfg.dry_run = True

    storage = StorageManager(cfg)
    db = DouyinDatabase()
    fetcher = DouyinFetcher()

    if not fetcher.cookie:
        logger.error("No Douyin Cookie available. Please configure DOUYIN_COOKIE in .env or settings.json")
        return 1

    target_channel_id = args.channel.strip().lower() if args.channel else None
    channels_modified = False

    for ch in channels_data:
        ch_id = ch.get("id", "").strip().lower()
        if not ch.get("enabled", True):
            logger.info(f"Skipping disabled channel: [{ch.get('name', ch_id)}]")
            continue

        if target_channel_id and ch_id != target_channel_id:
            continue

        sec_uid_before = ch.get("sec_uid")
        success = await sync_channel(
            channel=ch,
            cfg=cfg,
            storage=storage,
            db=db,
            fetcher=fetcher,
            force=args.force,
        )

        if ch.get("sec_uid") != sec_uid_before:
            channels_modified = True

    # Persist any auto-resolved sec_uids back to douyin_channels.json
    if channels_modified and not args.dry_run:
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(channels_data, f, ensure_ascii=False, indent=2)
        logger.info(f"Updated {config_file} with newly resolved sec_uids.")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Douyin Podcast Sync to Cloudflare R2")
    parser.add_argument("--config", default="douyin_channels.json", help="Path to douyin_channels.json")
    parser.add_argument("--channel", default="", help="Sync specific channel ID only")
    parser.add_argument("--dry-run", action="store_true", help="Simulate run without uploading")
    parser.add_argument("--force", action="store_true", help="Force re-download seen episodes")
    args = parser.parse_args()

    exit_code = asyncio.run(main_async(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
