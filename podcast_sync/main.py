import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import ChannelConfig, Config
from .feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, validate_podcast_rss
from .storage import StorageManager
from .youtube import YouTubeFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast_sync")


def sync_single_channel(
    channel_cfg: ChannelConfig,
    config: Config,
    storage: StorageManager,
    yt: YouTubeFetcher,
    is_primary: bool = False,
) -> dict:
    """Sync a single channel and return status dict."""
    channel_id = channel_cfg.id
    logger.info(f"--- Processing Channel: [{channel_id}] ({channel_cfg.url}) ---")

    # 1. Load known episodes and ignored list for this channel
    known_episodes = storage.load_episodes_manifest(channel_id)
    known_by_id = {ep.video_id: ep for ep in known_episodes}
    ignored_videos = storage.load_ignored_videos(channel_id)

    # 2. Query YouTube channel for latest videos
    channel_info, entries = yt.get_channel_info_and_entries(channel_cfg)
    if not entries:
        logger.warning(f"No video entries found for channel [{channel_id}].")
        return {"id": channel_id, "title": channel_info["title"], "feed_url": "", "new": 0, "total": len(known_episodes)}

    # 3. Process each video entry
    updated_episodes = []
    new_count = 0

    for entry in entries:
        if len(updated_episodes) >= channel_cfg.max_episodes:
            logger.info(f"[{channel_id}] Target quota of {channel_cfg.max_episodes} valid episodes reached. Stopping scan.")
            break

        video_id = entry["id"]
        if video_id in known_by_id:
            updated_episodes.append(known_by_id[video_id])
            logger.info(f"[{channel_id}] Existing episode: [{video_id}] {entry.get('title', '')}")
            continue

        if video_id in ignored_videos:
            logger.info(
                f"[{channel_id}] Skipping ignored video: [{video_id}] {entry.get('title', '')} "
                f"(Reason: {ignored_videos[video_id].get('reason', 'ignored')})"
            )
            continue

        # New video to download and convert
        logger.info(f"[{channel_id}] Processing new video: [{video_id}] {entry.get('title', '')}")
        try:
            download_meta = yt.download_audio_for_video(video_id, config.output_dir / channel_id)
            local_path = download_meta["local_audio_path"]

            # Upload audio to Cloudflare R2 under audio/{channel_id}/{video_id}.m4a
            public_audio_url = storage.upload_audio(local_path, channel_id, video_id)

            episode = PodcastEpisode(
                video_id=video_id,
                title=download_meta["title"],
                description=download_meta["description"],
                pub_date=download_meta["pub_date"],
                duration_seconds=download_meta["duration_seconds"],
                audio_filename=f"audio/{channel_id}/{video_id}.m4a",
                audio_url=public_audio_url,
                file_size_bytes=download_meta["file_size_bytes"],
                thumbnail_url=download_meta["thumbnail_url"],
                webpage_url=download_meta["webpage_url"],
            )
            updated_episodes.append(episode)
            new_count += 1

            # Delete local audio file
            if local_path.exists():
                try:
                    local_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete temporary file {local_path}: {e}")

        except Exception as e:
            err_str = str(e)
            err_lower = err_str.lower()
            is_members_only = any(
                kw in err_lower
                for kw in [
                    "members-only",
                    "join this channel",
                    "members of",
                    "subscriber_only",
                    "private video",
                    "sign in if you've been granted access",
                    "payment required",
                    "premium",
                ]
            )
            if is_members_only:
                logger.warning(f"[{channel_id}] Video [{video_id}] is restricted / members-only. Marking as ignored.")
                storage.mark_video_ignored(
                    channel_id=channel_id,
                    video_id=video_id,
                    reason="members_only",
                    title=entry.get("title", ""),
                    error=err_str,
                )
            else:
                logger.error(f"[{channel_id}] Failed to process video {video_id}: {e}")

    # Retain existing episodes not in current fetch (up to max_episodes)
    current_ids = {ep.video_id for ep in updated_episodes}
    for ep in known_episodes:
        if ep.video_id not in current_ids:
            updated_episodes.append(ep)

    # 4. Sort and apply retention policy
    updated_episodes.sort(key=lambda ep: ep.pub_date, reverse=True)
    retained_episodes = updated_episodes[: channel_cfg.max_episodes]
    expired_episodes = updated_episodes[channel_cfg.max_episodes :]

    # 5. Clean up expired episodes from R2
    if expired_episodes:
        logger.info(f"[{channel_id}] Pruning {len(expired_episodes)} expired episodes from R2...")
        for exp_ep in expired_episodes:
            storage.delete_audio(channel_id, exp_ep.video_id)

    # Active bucket reconciliation: ensure R2 audio folder strictly contains ONLY retained episodes
    storage.cleanup_orphan_and_expired_audio(channel_id, retained_episodes)

    # 6. Save manifest
    storage.save_episodes_manifest(channel_id, retained_episodes)

    # 7. Generate Apple Podcasts RSS Feed
    podcast_channel = PodcastChannel(
        title=channel_info["title"],
        link=channel_info["link"],
        description=channel_info["description"],
        author=channel_info["author"],
        image_url=channel_info["image_url"],
        language=channel_cfg.language,
        category=channel_cfg.category,
        episodes=retained_episodes,
    )

    rss_xml = generate_podcast_rss(podcast_channel)

    # 8. Validate RSS Feed XML accuracy before uploading
    try:
        val_res = validate_podcast_rss(
            rss_xml=rss_xml,
            channel_id=channel_id,
            expected_count=len(retained_episodes),
            r2_public_url=config.r2_public_url,
        )
        if new_count > 0:
            logger.info(
                f"[{channel_id}] ✓ XML accuracy verified after adding {new_count} new episodes "
                f"(Total: {val_res['episodes_count']} episodes, valid enclosures and syntax)."
            )
        else:
            logger.info(
                f"[{channel_id}] ✓ XML accuracy verified (Total: {val_res['episodes_count']} episodes, valid enclosures and syntax)."
            )
    except Exception as e:
        logger.error(f"[{channel_id}] XML accuracy validation FAILED: {e}")
        raise

    # 9. Upload feed.xml
    feed_url = storage.upload_channel_feed(channel_id, rss_xml, is_primary=is_primary)

    logger.info(f"[{channel_id}] Sync finished. Feed URL: {feed_url}")
    return {
        "id": channel_id,
        "title": channel_info["title"],
        "feed_url": feed_url,
        "new": new_count,
        "total": len(retained_episodes),
    }


def sync():
    parser = argparse.ArgumentParser(description="Sync YouTube channels to independent Apple Podcasts RSS feeds")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode without uploading to R2")
    parser.add_argument("--channel-id", type=str, help="Sync only a specific channel by ID")
    parser.add_argument("--channels-file", type=str, help="Path to custom channels.json")
    args = parser.parse_args()

    channels_path = Path(args.channels_file) if args.channels_file else None
    config = Config.from_env(channels_file=channels_path)

    if args.dry_run:
        config.dry_run = True

    config.validate()

    channels_to_process = config.channels
    if args.channel_id:
        channels_to_process = [ch for ch in config.channels if ch.id == args.channel_id]
        if not channels_to_process:
            logger.error(f"Channel ID '{args.channel_id}' not found in configuration.")
            sys.exit(1)

    logger.info("=== Starting Multi-Channel YouTube Podcast Sync ===")
    logger.info(f"Total configured channels: {len(channels_to_process)}")
    logger.info(f"Dry run mode: {config.dry_run}")

    storage = StorageManager(config)
    yt = YouTubeFetcher(config)

    # Clean up any legacy stray audio files in root audio/
    storage.cleanup_legacy_root_audio()

    results = []
    for idx, channel_cfg in enumerate(channels_to_process):
        is_primary = (idx == 0)
        try:
            res = sync_single_channel(channel_cfg, config, storage, yt, is_primary=is_primary)
            results.append(res)
        except Exception as e:
            logger.error(f"Failed to sync channel [{channel_cfg.id}]: {e}", exc_info=True)

    logger.info("=== All Channels Sync Completed ===")
    print("\n" + "=" * 65)
    print("【播客专属订阅源列表 (Apple Podcasts Feeds)】")
    print("=" * 65)
    for r in results:
        print(f"频道: {r['title']} [{r['id']}]")
        print(f"  - 新增集数: {r['new']} | 总期数: {r['total']}")
        print(f"  - 订阅链接: {r['feed_url']}")
        print("-" * 65)


if __name__ == "__main__":
    sync()
