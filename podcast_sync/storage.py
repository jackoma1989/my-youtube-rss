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
        expected_prefix = f"audio/{channel_id}/"

        def _filter_valid(eps: List[PodcastEpisode]) -> List[PodcastEpisode]:
            valid = []
            for ep in eps:
                if ep.audio_filename.startswith(expected_prefix):
                    valid.append(ep)
                else:
                    logger.warning(
                        f"[{channel_id}] Dropping legacy/cross-channel episode [{ep.video_id}] with audio_filename='{ep.audio_filename}'"
                    )
            return valid

        if self.config.dry_run:
            local_json = self.config.output_dir / channel_id / "episodes.json"
            if local_json.exists():
                try:
                    with open(local_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        return _filter_valid([PodcastEpisode.from_dict(item) for item in data])
                except Exception as e:
                    logger.warning(f"Failed to read local manifest for {channel_id}: {e}")
            return []

        key = f"channels/{channel_id}/episodes.json"
        try:
            logger.info(f"Checking for {key} in Cloudflare R2...")
            response = self.s3_client.get_object(
                Bucket=self.config.r2_bucket_name,
                Key=key,
            )
            content = response["Body"].read().decode("utf-8")
            data = json.loads(content)
            episodes = [PodcastEpisode.from_dict(item) for item in data]
            filtered = _filter_valid(episodes)
            logger.info(f"Loaded {len(filtered)} valid existing episodes for [{channel_id}] from {key}")
            return filtered
        except ClientError as e:
            if e.response["Error"]["Code"] not in ("NoSuchKey", "404"):
                raise

        logger.info(f"No existing manifest found for channel [{channel_id}]. Starting fresh.")
        return []

    def load_ignored_videos(self, channel_id: str) -> dict:
        """Load ignored/members-only videos dictionary {video_id: {reason, title, ...}}."""
        if self.config.dry_run:
            local_json = self.config.output_dir / channel_id / "ignored.json"
            if local_json.exists():
                try:
                    with open(local_json, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return data
                except Exception as e:
                    logger.warning(f"Failed to read local ignored list for {channel_id}: {e}")
            return {}

        key = f"channels/{channel_id}/ignored.json"
        try:
            response = self.s3_client.get_object(
                Bucket=self.config.r2_bucket_name,
                Key=key,
            )
            content = response["Body"].read().decode("utf-8")
            data = json.loads(content)
            if isinstance(data, dict):
                logger.info(f"Loaded {len(data)} ignored videos for [{channel_id}] from {key}")
                return data
            return {}
        except ClientError as e:
            if e.response["Error"]["Code"] in ("NoSuchKey", "404"):
                return {}
            logger.warning(f"Error loading {key} from R2: {e}")
            return {}

    def save_ignored_videos(self, channel_id: str, ignored: dict) -> None:
        """Save ignored/members-only videos dict to R2 and local disk."""
        json_str = json.dumps(ignored, ensure_ascii=False, indent=2)

        channel_output_dir = self.config.output_dir / channel_id
        channel_output_dir.mkdir(parents=True, exist_ok=True)
        with open(channel_output_dir / "ignored.json", "w", encoding="utf-8") as f:
            f.write(json_str)

        if self.config.dry_run:
            return

        key = f"channels/{channel_id}/ignored.json"
        self.s3_client.put_object(
            Bucket=self.config.r2_bucket_name,
            Key=key,
            Body=json_str.encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        logger.info(f"Saved {len(ignored)} ignored videos to R2 at {key}")

    def mark_video_ignored(
        self, channel_id: str, video_id: str, reason: str = "members_only", title: str = "", error: str = ""
    ) -> None:
        """Mark a video as ignored in the manifest."""
        from datetime import datetime, timezone

        ignored = self.load_ignored_videos(channel_id)
        ignored[video_id] = {
            "title": title,
            "reason": reason,
            "error": error[:200] if error else "",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self.save_ignored_videos(channel_id, ignored)
        logger.info(f"[{channel_id}] Marked video [{video_id}] as ignored ({reason}).")

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

    def cleanup_orphan_and_expired_audio(self, channel_id: str, retained_episodes: List[PodcastEpisode]) -> int:
        """
        List all audio files in audio/{channel_id}/ on R2,
        and delete any file that is NOT in retained_episodes.
        Guarantees R2 directory strictly contains at most len(retained_episodes) files!
        """
        if self.config.dry_run or not self.s3_client:
            return 0

        retained_ids = {ep.video_id for ep in retained_episodes}
        prefix = f"audio/{channel_id}/"
        deleted_count = 0

        try:
            paginator = self.s3_client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self.config.r2_bucket_name, Prefix=prefix):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    filename = key.split("/")[-1]
                    if not filename.endswith(".m4a"):
                        continue
                    video_id = filename[:-4]
                    if video_id not in retained_ids:
                        logger.info(f"[{channel_id}] Pruning extra/orphan audio from R2: {key}")
                        self.s3_client.delete_object(Bucket=self.config.r2_bucket_name, Key=key)
                        deleted_count += 1
        except Exception as e:
            logger.warning(f"[{channel_id}] Error reconciling audio folder: {e}")

        if deleted_count > 0:
            logger.info(f"[{channel_id}] Reconciled R2: purged {deleted_count} extra/orphan files. Remaining: {len(retained_episodes)}.")
        return deleted_count

    def cleanup_legacy_root_audio(self) -> int:
        """Purge any legacy stray files directly under audio/ outside of channel subdirectories."""
        if self.config.dry_run or not self.s3_client:
            return 0
        deleted_count = 0
        try:
            response = self.s3_client.list_objects_v2(Bucket=self.config.r2_bucket_name, Prefix="audio/", Delimiter="/")
            for obj in response.get("Contents", []):
                key = obj["Key"]
                if key != "audio/" and not key.endswith("/"):
                    logger.info(f"Purging legacy root audio file from R2: {key}")
                    self.s3_client.delete_object(Bucket=self.config.r2_bucket_name, Key=key)
                    deleted_count += 1
        except Exception as e:
            logger.warning(f"Error cleaning legacy root audio: {e}")
        return deleted_count


