import json
import logging
from pathlib import Path
from typing import List, Optional

import boto3
from botocore.exceptions import ClientError

from .config import Config
from .feed import PodcastEpisode

logger = logging.getLogger(__name__)


class StorageManager:
    def __init__(self, config: Config):
        self.config = config
        self.s3_client = None
        if not config.dry_run:
            self.s3_client = boto3.client(
                "s3",
                endpoint_url=f"https://{config.r2_account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=config.r2_access_key_id,
                aws_secret_access_key=config.r2_secret_access_key,
                region_name="auto",
            )

    def load_episodes_manifest(self, channel_id: str) -> List[PodcastEpisode]:
        """Load known episodes manifest for a specific channel from R2 or local cache."""
        if self.config.dry_run:
            local_json = self.config.output_dir / channel_id / "episodes.json"
            if not local_json.exists():
                local_json = self.config.output_dir / "episodes.json"
            if local_json.exists():
                try:
                    with open(local_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return [PodcastEpisode.from_dict(item) for item in data]
                except Exception as e:
                    logger.warning(f"Failed to read local manifest for {channel_id}: {e}")
            return []

        # Try channel-specific key first
        keys_to_try = [f"channels/{channel_id}/episodes.json"]
        if channel_id in ("wangzhian", "default"):
            keys_to_try.append("episodes.json")
        for key in keys_to_try:
            try:
                logger.info(f"Checking for {key} in Cloudflare R2...")
                response = self.s3_client.get_object(
                    Bucket=self.config.r2_bucket_name,
                    Key=key,
                )
                content = response["Body"].read().decode("utf-8")
                data = json.loads(content)
                episodes = [PodcastEpisode.from_dict(item) for item in data]
                logger.info(f"Loaded {len(episodes)} existing episodes for [{channel_id}] from {key}")
                return episodes
            except ClientError as e:
                if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                    continue
                raise

        logger.info(f"No existing manifest found for channel [{channel_id}]. Starting fresh.")
        return []

    def save_episodes_manifest(self, channel_id: str, episodes: List[PodcastEpisode]) -> None:
        """Save updated episodes manifest for a channel to R2 and local disk."""
        data = [ep.to_dict() for ep in episodes]
        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        # Save locally
        channel_output_dir = self.config.output_dir / channel_id
        channel_output_dir.mkdir(parents=True, exist_ok=True)
        with open(channel_output_dir / "episodes.json", "w", encoding="utf-8") as f:
            f.write(json_str)

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Saved {len(episodes)} episodes for [{channel_id}] to local disk")
            return

        r2_key = f"channels/{channel_id}/episodes.json"
        logger.info(f"Saving manifest ({len(episodes)} items) to R2 at {r2_key}...")
        self.s3_client.put_object(
            Bucket=self.config.r2_bucket_name,
            Key=r2_key,
            Body=json_str.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )

    def upload_audio(self, local_path: Path, channel_id: str, video_id: str) -> str:
        """Upload audio file to R2 under audio/{channel_id}/{video_id}.m4a."""
        filename = f"audio/{channel_id}/{video_id}.m4a"
        public_url = f"{self.config.r2_public_url}/{filename}"

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Simulating upload of {local_path} -> {public_url}")
            return public_url

        file_size_mb = local_path.stat().st_size / (1024 * 1024)
        logger.info(f"Uploading {local_path.name} ({file_size_mb:.1f} MB) to R2 as {filename}...")
        self.s3_client.upload_file(
            Filename=str(local_path),
            Bucket=self.config.r2_bucket_name,
            Key=filename,
            ExtraArgs={
                "ContentType": "audio/x-m4a",
                "CacheControl": "public, max-age=31536000, immutable",
            },
        )
        logger.info(f"Upload complete: {public_url}")
        return public_url

    def upload_channel_feed(self, channel_id: str, rss_content: str, is_primary: bool = False) -> str:
        """
        Upload channel RSS feed:
        1. {channel_id}.xml (e.g. https://podcast.hemajia.fun/wangzhian.xml)
        2. {channel_id}/feed.xml
        3. If is_primary, also updates root feed.xml for backward compatibility
        """
        primary_feed_url = f"{self.config.r2_public_url}/{channel_id}.xml"

        # Save locally
        channel_dir = self.config.output_dir / channel_id
        channel_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config.output_dir / f"{channel_id}.xml", "w", encoding="utf-8") as f:
            f.write(rss_content)
        with open(channel_dir / "feed.xml", "w", encoding="utf-8") as f:
            f.write(rss_content)

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Simulating upload of feed -> {primary_feed_url}")
            return primary_feed_url

        keys = [f"{channel_id}.xml", f"{channel_id}/feed.xml"]
        if is_primary:
            keys.append("feed.xml")

        for key in keys:
            logger.info(f"Uploading feed to Cloudflare R2 at {key}...")
            self.s3_client.put_object(
                Bucket=self.config.r2_bucket_name,
                Key=key,
                Body=rss_content.encode("utf-8"),
                ContentType="application/rss+xml; charset=utf-8",
                CacheControl="public, max-age=300",
            )

        logger.info(f"Channel [{channel_id}] Feed URL: {primary_feed_url}")
        return primary_feed_url

    def delete_audio(self, channel_id: str, video_id: str) -> None:
        """Delete old audio file from R2."""
        keys = [f"audio/{channel_id}/{video_id}.m4a", f"audio/{video_id}.m4a"]
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Simulating delete of {keys[0]} from R2")
            return

        for key in keys:
            try:
                self.s3_client.delete_object(
                    Bucket=self.config.r2_bucket_name,
                    Key=key,
                )
                logger.info(f"Deleted expired audio from R2: {key}")
            except Exception as e:
                logger.debug(f"Could not delete {key}: {e}")
