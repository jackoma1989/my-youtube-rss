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

    def load_episodes_manifest(self) -> List[PodcastEpisode]:
        """Load known episodes manifest from Cloudflare R2 or local cache."""
        if self.config.dry_run:
            local_json = self.config.output_dir / "episodes.json"
            if local_json.exists():
                try:
                    with open(local_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return [PodcastEpisode.from_dict(item) for item in data]
                except Exception as e:
                    logger.warning(f"Failed to read local manifest: {e}")
            return []

        try:
            logger.info("Loading episodes.json from Cloudflare R2...")
            response = self.s3_client.get_object(
                Bucket=self.config.r2_bucket_name,
                Key="episodes.json",
            )
            content = response["Body"].read().decode("utf-8")
            data = json.loads(content)
            episodes = [PodcastEpisode.from_dict(item) for item in data]
            logger.info(f"Loaded {len(episodes)} existing episodes from R2")
            return episodes
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                logger.info("No existing episodes.json found in R2. Starting fresh.")
                return []
            raise

    def save_episodes_manifest(self, episodes: List[PodcastEpisode]) -> None:
        """Save updated episodes manifest to R2 or local cache."""
        data = [ep.to_dict() for ep in episodes]
        json_str = json.dumps(data, ensure_ascii=False, indent=2)

        # Always save locally
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config.output_dir / "episodes.json", "w", encoding="utf-8") as f:
            f.write(json_str)

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Saved {len(episodes)} episodes to local episodes.json")
            return

        logger.info(f"Saving episodes.json ({len(episodes)} items) to Cloudflare R2...")
        self.s3_client.put_object(
            Bucket=self.config.r2_bucket_name,
            Key="episodes.json",
            Body=json_str.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )

    def upload_audio(self, local_path: Path, video_id: str) -> str:
        """
        Upload audio file to R2 under audio/{video_id}.m4a.
        Returns the public URL of the audio file.
        """
        filename = f"audio/{video_id}.m4a"
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

    def upload_feed(self, rss_content: str) -> str:
        """
        Upload feed.xml to R2 root.
        Returns the public feed URL.
        """
        feed_url = f"{self.config.r2_public_url}/feed.xml"

        # Always save locally as well
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        with open(self.config.output_dir / "feed.xml", "w", encoding="utf-8") as f:
            f.write(rss_content)

        if self.config.dry_run:
            logger.info(f"[DRY RUN] Simulating upload of feed.xml -> {feed_url}")
            return feed_url

        logger.info("Uploading feed.xml to Cloudflare R2...")
        self.s3_client.put_object(
            Bucket=self.config.r2_bucket_name,
            Key="feed.xml",
            Body=rss_content.encode("utf-8"),
            ContentType="application/rss+xml; charset=utf-8",
            CacheControl="public, max-age=300",  # 5 min cache for RSS client refresh
        )
        logger.info(f"Feed URL: {feed_url}")
        return feed_url

    def delete_audio(self, video_id: str) -> None:
        """Delete old audio file from R2."""
        filename = f"audio/{video_id}.m4a"
        if self.config.dry_run:
            logger.info(f"[DRY RUN] Simulating delete of {filename} from R2")
            return

        try:
            logger.info(f"Deleting expired audio from R2: {filename}")
            self.s3_client.delete_object(
                Bucket=self.config.r2_bucket_name,
                Key=filename,
            )
        except Exception as e:
            logger.warning(f"Failed to delete {filename} from R2: {e}")
