import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from podcast_sync.config import Config
from podcast_sync.feed import PodcastEpisode
from podcast_sync.storage import StorageManager


class TestHistoryAndRetention(unittest.TestCase):

    def setUp(self):
        self.tmp_dir = Path("output/test_history_retention")
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.cfg = Config(
            r2_account_id="",
            r2_access_key_id="",
            r2_secret_access_key="",
            r2_bucket_name="",
            dry_run=True,
            output_dir=self.tmp_dir,
        )
        self.storage = StorageManager(self.cfg)

    def tearDown(self):
        import shutil
        if self.tmp_dir.exists():
            shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_local_history_load_save(self):
        channel_id = "test_chan"
        ids = {"vid_1", "vid_2", "vid_3"}
        self.storage.save_history_ids(channel_id, ids)

        loaded = self.storage.load_history_ids(channel_id)
        self.assertEqual(loaded, ids)

    @patch("boto3.client")
    def test_r2_history_load_save(self, mock_boto):
        mock_s3 = MagicMock()
        mock_boto.return_value = mock_s3

        cfg = Config(
            r2_account_id="acc",
            r2_access_key_id="key",
            r2_secret_access_key="secret",
            r2_bucket_name="mybucket",
            dry_run=False,
            output_dir=self.tmp_dir,
        )
        storage = StorageManager(cfg)

        # Mock load from R2
        mock_s3.get_object.return_value = {
            "Body": MagicMock(read=lambda: json.dumps(["id1", "id2"]).encode("utf-8"))
        }
        loaded = storage.load_history_ids("my_chan")
        self.assertEqual(loaded, {"id1", "id2"})
        mock_s3.get_object.assert_called_with(
            Bucket="mybucket", Key="channels/my_chan/history.json"
        )

        # Mock save to R2
        storage.save_history_ids("my_chan", {"id1", "id2", "id3"})
        mock_s3.put_object.assert_called_once()
        call_kwargs = mock_s3.put_object.call_args[1]
        self.assertEqual(call_kwargs["Bucket"], "mybucket")
        self.assertEqual(call_kwargs["Key"], "channels/my_chan/history.json")
        saved_list = json.loads(call_kwargs["Body"].decode("utf-8"))
        self.assertEqual(saved_list, ["id1", "id2", "id3"])

    def test_notification_filtering_only_retained(self):
        # Create 3 episodes: ep1 (newest), ep2, ep3 (oldest)
        ep1 = PodcastEpisode(
            video_id="v1",
            title="Video 1",
            description="",
            pub_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            duration_seconds=100,
            audio_filename="audio/c/v1.m4a",
            audio_url="https://podcast.hemajia.fun/v1.m4a",
            file_size_bytes=1000,
        )
        ep2 = PodcastEpisode(
            video_id="v2",
            title="Video 2",
            description="",
            pub_date=datetime(2026, 7, 20, tzinfo=timezone.utc),
            duration_seconds=100,
            audio_filename="audio/c/v2.m4a",
            audio_url="https://podcast.hemajia.fun/v2.m4a",
            file_size_bytes=1000,
        )
        ep3_pinned = PodcastEpisode(
            video_id="v3_pinned",
            title="Old Pinned Video",
            description="",
            pub_date=datetime(2026, 7, 10, tzinfo=timezone.utc),
            duration_seconds=100,
            audio_filename="audio/c/v3_pinned.m4a",
            audio_url="https://podcast.hemajia.fun/v3_pinned.m4a",
            file_size_bytes=1000,
        )

        manifest = [ep1, ep2, ep3_pinned]
        max_episodes = 2
        # Sort and prune
        sorted_manifest = sorted(manifest, key=lambda ep: ep.pub_date, reverse=True)
        retained = sorted_manifest[:max_episodes]
        self.assertEqual([ep.video_id for ep in retained], ["v1", "v2"])

        # Pinned ep3 was added to newly_added_episodes during run
        newly_added_episodes = [ep1, ep3_pinned]

        retained_ids = {ep.video_id for ep in retained}
        valid_new_episodes = [ep for ep in newly_added_episodes if ep.video_id in retained_ids]

        # Crucial check: only ep1 should trigger Telegram notification; ep3_pinned was pruned so must not notify!
        self.assertEqual([ep.video_id for ep in valid_new_episodes], ["v1"])


if __name__ == "__main__":
    unittest.main()
