import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from .config import Config
from .feed import PodcastChannel, PodcastEpisode, generate_podcast_rss
from .storage import StorageManager
from .youtube import YouTubeFetcher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("podcast_sync")


def sync():
    parser = argparse.ArgumentParser(description="Sync YouTube channel to Apple Podcasts RSS feed")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry-run mode without uploading to R2")
    parser.add_argument("--channel-url", type=str, help="YouTube channel or playlist URL")
    parser.add_argument("--max-episodes", type=int, help="Maximum number of episodes to retain")
    args = parser.parse_args()

    config = Config.from_env()

    if args.dry_run:
        config.dry_run = True
    if args.channel_url:
        config.channel_url = args.channel_url
    if args.max_episodes:
        config.max_episodes = args.max_episodes

    config.validate()

    logger.info("=== Starting YouTube to Podcast Sync ===")
    logger.info(f"Target YouTube URL: {config.channel_url}")
    logger.info(f"Max episodes retention: {config.max_episodes}")
    logger.info(f"Dry run mode: {config.dry_run}")

    storage = StorageManager(config)
    yt = YouTubeFetcher(config)

    # 1. Load existing episodes
    known_episodes = storage.load_episodes_manifest()
    known_by_id = {ep.video_id: ep for ep in known_episodes}

    # 2. Query YouTube channel for latest videos
    channel_info, entries = yt.get_channel_info_and_entries()
    if not entries:
        logger.warning("No video entries found for the specified channel.")
        return

    # 3. Process each video entry (download new ones)
    updated_episodes = []
    new_count = 0

    for entry in entries:
        video_id = entry["id"]
        if video_id in known_by_id:
            # Already synced, reuse existing metadata and R2 URL
            updated_episodes.append(known_by_id[video_id])
            logger.info(f"Existing episode: [{video_id}] {entry.get('title', '')}")
            continue

        # New video to download and convert
        logger.info(f"Processing new video: [{video_id}] {entry.get('title', '')}")
        try:
            download_meta = yt.download_audio_for_video(video_id, config.output_dir)
            local_path = download_meta["local_audio_path"]

            # Upload audio to Cloudflare R2
            public_audio_url = storage.upload_audio(local_path, video_id)

            # Create episode record
            episode = PodcastEpisode(
                video_id=video_id,
                title=download_meta["title"],
                description=download_meta["description"],
                pub_date=download_meta["pub_date"],
                duration_seconds=download_meta["duration_seconds"],
                audio_filename=f"audio/{video_id}.m4a",
                audio_url=public_audio_url,
                file_size_bytes=download_meta["file_size_bytes"],
                thumbnail_url=download_meta["thumbnail_url"],
                webpage_url=download_meta["webpage_url"],
            )
            updated_episodes.append(episode)
            new_count += 1

            # Delete local audio file to save disk space
            if local_path.exists():
                try:
                    local_path.unlink()
                except Exception as e:
                    logger.warning(f"Could not delete temporary file {local_path}: {e}")

        except Exception as e:
            logger.error(f"Failed to process video {video_id}: {e}", exc_info=True)

    # Include any previously known episodes that might not have appeared in current entries
    # up to max_episodes
    current_ids = {ep.video_id for ep in updated_episodes}
    for ep in known_episodes:
        if ep.video_id not in current_ids:
            updated_episodes.append(ep)

    # 4. Sort and apply retention policy
    updated_episodes.sort(key=lambda ep: ep.pub_date, reverse=True)
    retained_episodes = updated_episodes[: config.max_episodes]
    expired_episodes = updated_episodes[config.max_episodes :]

    # 5. Clean up expired episodes from R2
    if expired_episodes:
        logger.info(f"Pruning {len(expired_episodes)} expired episodes from R2...")
        for exp_ep in expired_episodes:
            storage.delete_audio(exp_ep.video_id)

    # 6. Save manifest
    storage.save_episodes_manifest(retained_episodes)

    # 7. Generate Apple Podcasts RSS Feed
    channel = PodcastChannel(
        title=channel_info["title"],
        link=channel_info["link"],
        description=channel_info["description"],
        author=channel_info["author"],
        image_url=channel_info["image_url"],
        language=config.podcast_language,
        category=config.podcast_category,
        episodes=retained_episodes,
    )

    rss_xml = generate_podcast_rss(channel)

    # 8. Upload feed.xml
    feed_url = storage.upload_feed(rss_xml)

    logger.info("=== Sync Completed Successfully ===")
    logger.info(f"New episodes added: {new_count}")
    logger.info(f"Total episodes in feed: {len(retained_episodes)}")
    logger.info(f"Apple Podcasts RSS Feed URL: {feed_url}")


if __name__ == "__main__":
    sync()
