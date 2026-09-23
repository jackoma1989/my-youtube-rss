import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from podcast_sync.douyin_db import DouyinDatabase
from podcast_sync.douyin_fetcher import DouyinFetcher
from podcast_sync.feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, validate_podcast_rss
from run_douyin_sync import sanitize_filename, prune_icloud_folder, get_icloud_dir


class TestDouyinSync(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)
        self.db_path = self.temp_path / "test_douyin.db"
        self.db = DouyinDatabase(self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_database_seen_and_mark(self):
        ch_id = "test_channel"
        self.assertFalse(self.db.is_seen(ch_id, "vid_101"))
        self.assertEqual(len(self.db.get_seen_ids(ch_id)), 0)

        self.db.mark_seen(
            channel_id=ch_id,
            aweme_id="vid_101",
            title="Test Episode 101",
            duration=300,
            pub_date="2026-09-23T10:00:00+00:00",
            r2_url="https://podcast.example.com/audio/test_channel/vid_101.mp3",
        )

        self.assertTrue(self.db.is_seen(ch_id, "vid_101"))
        self.assertFalse(self.db.is_seen(ch_id, "vid_102"))
        self.assertEqual(self.db.count_seen(ch_id), 1)

    def test_database_sync_from_manifest(self):
        ch_id = "test_channel"
        episodes = [
            PodcastEpisode(
                video_id="vid_1",
                title="Episode 1",
                description="Desc 1",
                pub_date=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
                duration_seconds=120,
                audio_filename="audio/test_channel/vid_1.mp3",
                audio_url="https://podcast.example.com/audio/test_channel/vid_1.mp3",
                file_size_bytes=1000,
            ),
            PodcastEpisode(
                video_id="vid_2",
                title="Episode 2",
                description="Desc 2",
                pub_date=datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc),
                duration_seconds=180,
                audio_filename="audio/test_channel/vid_2.mp3",
                audio_url="https://podcast.example.com/audio/test_channel/vid_2.mp3",
                file_size_bytes=2000,
            ),
        ]
        added = self.db.sync_from_manifest(ch_id, episodes)
        self.assertEqual(added, 2)
        self.assertTrue(self.db.is_seen(ch_id, "vid_1"))
        self.assertTrue(self.db.is_seen(ch_id, "vid_2"))
        self.assertEqual(self.db.count_seen(ch_id), 2)

        # Re-running sync adds 0 duplicate
        added_again = self.db.sync_from_manifest(ch_id, episodes)
        self.assertEqual(added_again, 0)

    def test_sanitize_filename(self):
        dirty = '大企业无息拖账，小企业负重前行/新版"三角债"有多残酷？:*?<>|\r\n'
        cleaned = sanitize_filename(dirty)
        self.assertNotIn("/", cleaned)
        self.assertNotIn('"', cleaned)
        self.assertNotIn(":", cleaned)
        self.assertNotIn("*", cleaned)
        self.assertNotIn("?", cleaned)
        self.assertNotIn("<", cleaned)
        self.assertNotIn(">", cleaned)
        self.assertNotIn("|", cleaned)
        self.assertTrue(cleaned.startswith("大企业无息拖账"))

    def test_prune_icloud_folder(self):
        icloud_dir = self.temp_path / "icloud_test"
        icloud_dir.mkdir(parents=True)

        # Create dummy mp3 files
        ep_retained = [
            PodcastEpisode(
                video_id=f"vid_{i}",
                title=f"Episode {i}",
                description="",
                pub_date=datetime(2026, 9, 20, 10, 0, tzinfo=timezone.utc),
                duration_seconds=100,
                audio_filename=f"audio/test/vid_{i}.mp3",
                audio_url=f"https://example.com/audio/test/vid_{i}.mp3",
                file_size_bytes=100,
            )
            for i in range(1, 4)
        ]

        # File belonging to retained
        f1 = icloud_dir / "路口大爷 - 20260920 - Episode 1.mp3"
        f1.write_bytes(b"dummy1")
        f2 = icloud_dir / "路口大爷 - 20260920 - Episode 2.mp3"
        f2.write_bytes(b"dummy2")

        # Expired file
        f_old = icloud_dir / "路口大爷 - 20250101 - Old Expired.mp3"
        f_old.write_bytes(b"dummy_old")

        deleted = prune_icloud_folder(icloud_dir, ep_retained, max_episodes=2)
        self.assertEqual(deleted, 1)
        self.assertFalse(f_old.exists())
        self.assertTrue(f1.exists())
        self.assertTrue(f2.exists())

    def test_resolve_sec_uid_local(self):
        fetcher = DouyinFetcher(cookie="dummy")
        url1 = "https://www.douyin.com/user/MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c"
        self.assertEqual(fetcher.resolve_sec_uid(url1), "MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c")

        url2 = "https://www.douyin.com/user/MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c?from_tab_name=main"
        self.assertEqual(fetcher.resolve_sec_uid(url2), "MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c")

    def test_feed_generation_and_validation_mp3(self):
        ch = PodcastChannel(
            title="路口大爷",
            link="https://www.douyin.com/user/MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c",
            description="路口大爷播客",
            author="路口大爷",
            image_url="https://podcast.example.com/covers/caijinglukou.jpg",
            category="Business",
            episodes=[
                PodcastEpisode(
                    video_id="7688373194587261739",
                    title="新版三角债有多残酷？",
                    description="大企业无息拖账",
                    pub_date=datetime(2026, 9, 22, 22, 54, tzinfo=timezone.utc),
                    duration_seconds=470,
                    audio_filename="audio/caijinglukou/7688373194587261739.mp3",
                    audio_url="https://podcast.example.com/audio/caijinglukou/7688373194587261739.mp3",
                    file_size_bytes=11297246,
                    thumbnail_url="https://example.com/cover.jpg",
                    webpage_url="https://www.douyin.com/video/7688373194587261739",
                )
            ],
        )

        rss_xml = generate_podcast_rss(ch)
        self.assertIn("<enclosure", rss_xml)
        self.assertIn('type="audio/mpeg"', rss_xml)
        self.assertIn("7688373194587261739.mp3", rss_xml)

        validation = validate_podcast_rss(rss_xml, "caijinglukou", expected_count=1)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["episodes_count"], 1)


if __name__ == "__main__":
    unittest.main()
