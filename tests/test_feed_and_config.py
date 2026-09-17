import unittest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import json

from podcast_sync.config import ChannelConfig, Config, sanitize_channel_id
from podcast_sync.feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, format_duration, validate_podcast_rss


class TestPodcastSync(unittest.TestCase):
    def test_duration_formatter(self):
        self.assertEqual(format_duration(45), "00:45")
        self.assertEqual(format_duration(125), "02:05")
        self.assertEqual(format_duration(3665), "01:01:05")

    def test_rss_generation(self):
        ep1 = PodcastEpisode(
            video_id="dQw4w9WgXcQ",
            title="Test Episode 1",
            description="A classic music video.",
            pub_date=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=212,
            audio_filename="audio/test/dQw4w9WgXcQ.m4a",
            audio_url="https://podcast.example.com/audio/test/dQw4w9WgXcQ.m4a",
            file_size_bytes=3456789,
            thumbnail_url="https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            webpage_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

        channel = PodcastChannel(
            title="My Tech Show",
            link="https://www.youtube.com/@mytechshow",
            description="Tech talk.",
            author="Tech Host",
            image_url="https://podcast.example.com/cover.jpg",
            language="zh-cn",
            category="Technology",
            episodes=[ep1],
        )

        xml_output = generate_podcast_rss(channel)

        root = ET.fromstring(xml_output)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib.get("version"), "2.0")

        channel_elem = root.find("channel")
        self.assertIsNotNone(channel_elem)
        self.assertEqual(channel_elem.find("title").text, "My Tech Show")

        itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        author_elem = channel_elem.find(f"{{{itunes_ns}}}author")
        self.assertEqual(author_elem.text, "Tech Host")

        item = channel_elem.find("item")
        self.assertIsNotNone(item)
        enclosure = item.find("enclosure")
        self.assertEqual(enclosure.attrib["url"], ep1.audio_url)
        self.assertEqual(enclosure.attrib["type"], "audio/x-m4a")

    def test_channel_sanitization(self):
        self.assertEqual(sanitize_channel_id("Wang-ZhiAn_123"), "wang-zhian_123")
        self.assertEqual(sanitize_channel_id("@channel!#$"), "channel")

    def test_multi_channel_config_loading(self):
        data = [
            {"id": "channel1", "url": "https://www.youtube.com/@ch1", "max_episodes": 10},
            {"id": "channel2", "url": "https://www.youtube.com/@ch2", "category": "News"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp)
            tmp_path = Path(tmp.name)

        try:
            cfg = Config.from_env(env_path=Path("nonexistent.env"), channels_file=tmp_path)
            cfg.dry_run = True
            cfg.validate()
            self.assertEqual(len(cfg.channels), 2)
            self.assertEqual(cfg.channels[0].id, "channel1")
            self.assertEqual(cfg.channels[0].max_episodes, 10)
            self.assertEqual(cfg.channels[1].category, "News")
            self.assertEqual(cfg.channels[1].max_episodes, 15)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    def test_channel_manifest_isolation(self):
        from podcast_sync.storage import StorageManager
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = Config(dry_run=True, output_dir=tmp_path)
            storage = StorageManager(cfg)

            channel_dir = tmp_path / "chaijing2023"
            channel_dir.mkdir(parents=True, exist_ok=True)

            # Manifest containing one contaminated root episode and one valid channel episode
            test_episodes = [
                {
                    "video_id": "2exrmt3_Z-M",
                    "title": "Legacy Root Video",
                    "description": "Legacy",
                    "pub_date": "2026-09-16T12:00:00+00:00",
                    "duration_seconds": 120,
                    "audio_filename": "audio/2exrmt3_Z-M.m4a",
                    "audio_url": "https://podcast.example.com/audio/2exrmt3_Z-M.m4a",
                    "file_size_bytes": 1000,
                    "thumbnail_url": "",
                    "webpage_url": "",
                },
                {
                    "video_id": "valid123",
                    "title": "Chai Jing Video",
                    "description": "Chai Jing",
                    "pub_date": "2026-09-16T13:00:00+00:00",
                    "duration_seconds": 300,
                    "audio_filename": "audio/chaijing2023/valid123.m4a",
                    "audio_url": "https://podcast.example.com/audio/chaijing2023/valid123.m4a",
                    "file_size_bytes": 2000,
                    "thumbnail_url": "",
                    "webpage_url": "",
                },
            ]

            with open(channel_dir / "episodes.json", "w", encoding="utf-8") as f:
                json.dump(test_episodes, f)

            loaded = storage.load_episodes_manifest("chaijing2023")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].video_id, "valid123")
            self.assertEqual(loaded[0].audio_filename, "audio/chaijing2023/valid123.m4a")

    def test_ignored_videos_management(self):
        from podcast_sync.storage import StorageManager
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = Config(dry_run=True, output_dir=tmp_path)
            storage = StorageManager(cfg)

            # Initially empty
            ignored = storage.load_ignored_videos("wangzhian")
            self.assertEqual(len(ignored), 0)

            # Mark a members-only video
            storage.mark_video_ignored(
                channel_id="wangzhian",
                video_id="member_video_1",
                reason="members_only",
                title="Member Only Episode",
                error="This video is available to this channel's members",
            )

            # Verify persisted and reloaded
            reloaded = storage.load_ignored_videos("wangzhian")
            self.assertIn("member_video_1", reloaded)
            self.assertEqual(reloaded["member_video_1"]["reason"], "members_only")
            self.assertEqual(reloaded["member_video_1"]["title"], "Member Only Episode")

    def test_validate_podcast_rss(self):
        ep = PodcastEpisode(
            video_id="abc1234",
            title="Episode 1",
            description="Good show",
            pub_date=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=120,
            audio_filename="audio/test_ch/abc1234.m4a",
            audio_url="https://podcast.example.com/audio/test_ch/abc1234.m4a",
            file_size_bytes=1024,
            thumbnail_url=None,
            webpage_url="https://www.youtube.com/watch?v=abc1234",
        )
        ch = PodcastChannel(
            title="Test Show",
            link="https://www.youtube.com/@test_ch",
            description="Test Description",
            author="Test Host",
            image_url="https://podcast.example.com/cover.jpg",
            episodes=[ep],
        )

        valid_xml = generate_podcast_rss(ch)

        # 1. Valid feed passes
        res = validate_podcast_rss(valid_xml, channel_id="test_ch", expected_count=1)
        self.assertTrue(res["valid"])
        self.assertEqual(res["episodes_count"], 1)

        # 2. Cross-channel enclosure URL raises error
        with self.assertRaises(ValueError) as ctx:
            validate_podcast_rss(valid_xml, channel_id="wrong_ch", expected_count=1)
        self.assertIn("Enclosure URL validation failed", str(ctx.exception))

        # 3. Episode count mismatch raises error
        with self.assertRaises(ValueError) as ctx:
            validate_podcast_rss(valid_xml, channel_id="test_ch", expected_count=2)
        self.assertIn("Episode count mismatch", str(ctx.exception))

        # 4. Malformed XML raises error
        with self.assertRaises(ValueError):
            validate_podcast_rss("<rss><unclosed>", channel_id="test_ch")

    def test_env_secret_precedence(self):
        import os
        # channels.json has 2 channels
        data = [
            {"id": "channel1", "url": "https://www.youtube.com/@ch1", "name": "Channel 1"},
            {"id": "channel2", "url": "https://www.youtube.com/@ch2", "name": "Channel 2"},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(data, tmp)
            tmp_path = Path(tmp.name)

        orig_env = dict(os.environ)
        try:
            # User only has YOUTUBE_CHANNEL_URL for ch1 (ch2 deleted from secret)
            os.environ["YOUTUBE_CHANNEL_URL"] = "https://www.youtube.com/@ch1"
            if "YOUTUBE_CHANNEL_URL_2" in os.environ:
                del os.environ["YOUTUBE_CHANNEL_URL_2"]

            cfg = Config.from_env(channels_file=tmp_path)
            # Only ch1 should be active
            self.assertEqual(len(cfg.channels), 1)
            self.assertEqual(cfg.channels[0].id, "channel1")
            self.assertEqual(cfg.channels[0].name, "Channel 1")
        finally:
            os.environ.clear()
            os.environ.update(orig_env)
            if tmp_path.exists():
                tmp_path.unlink()

    def test_unsubscribed_feed_removal_preserves_audio(self):
        from podcast_sync.storage import StorageManager
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = Config(dry_run=True, output_dir=tmp_path)
            storage = StorageManager(cfg)

            ch_dir = tmp_path / "dashibingfa"
            ch_dir.mkdir(parents=True, exist_ok=True)
            manifest_file = ch_dir / "episodes.json"
            manifest_file.write_text("[]", encoding="utf-8")

            # Feed XML
            feed_file = tmp_path / "dashibingfa.xml"
            feed_file.write_text("<rss></rss>", encoding="utf-8")

            # Audio file
            audio_dir = tmp_path / "audio" / "dashibingfa"
            audio_dir.mkdir(parents=True, exist_ok=True)
            audio_file = audio_dir / "episode1.m4a"
            audio_file.write_text("fake audio content", encoding="utf-8")

            # 1. list_persisted_channels detects dashibingfa
            persisted = storage.list_persisted_channels()
            self.assertIn("dashibingfa", persisted)

            # 2. remove_channel_feed removes dashibingfa.xml
            storage.remove_channel_feed("dashibingfa")
            self.assertFalse(feed_file.exists())

            # 3. Audio file and manifest are strictly PRESERVED!
            self.assertTrue(audio_file.exists())
            self.assertTrue(manifest_file.exists())

    def test_telegram_notification_formatting_and_fallback(self):
        from unittest.mock import patch, MagicMock
        import io
        from podcast_sync.notifier import send_new_episode_notification, send_telegram_message

        ep = PodcastEpisode(
            video_id="test1234",
            title="<Special> Episode & Title",
            description="Sample",
            pub_date=datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc),
            duration_seconds=3665,
            audio_filename="audio/wangzhian/test1234.m4a",
            audio_url="https://podcast.example.com/audio/wangzhian/test1234.m4a",
            file_size_bytes=123456,
            thumbnail_url="",
            webpage_url="https://www.youtube.com/watch?v=test1234",
        )

        # 1. Test empty credentials returns None safely
        self.assertIsNone(send_new_episode_notification("", "", "王局", ep))

        # 2. Test successful delivery
        fake_response = MagicMock()
        fake_response.read.return_value = json.dumps({"ok": True, "result": {"message_id": 999}}).encode("utf-8")
        fake_response.__enter__.return_value = fake_response

        with patch("urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
            res = send_new_episode_notification(
                bot_token="fake_token",
                chat_id="fake_chat",
                channel_title="王局拍案",
                episode=ep,
                feed_url="https://podcast.hemajia.fun/wangzhian.xml",
            )
            self.assertIsNotNone(res)
            self.assertTrue(res.get("ok"))
            self.assertEqual(mock_urlopen.call_count, 1)

            # Inspect request payload
            req = mock_urlopen.call_args[0][0]
            req_data = json.loads(req.data.decode("utf-8"))
            self.assertEqual(req_data["chat_id"], "fake_chat")
            self.assertEqual(req_data["parse_mode"], "HTML")
            self.assertIn("&lt;Special&gt; Episode &amp; Title", req_data["text"])
            self.assertIn("01:01:05", req_data["text"])
            self.assertIn("https://podcast.hemajia.fun/wangzhian.xml", req_data["text"])

        # 3. Test fallback to plain text when HTML fails
        with patch("urllib.request.urlopen", side_effect=[Exception("400 Bad Request"), fake_response]) as mock_retry:
            res = send_telegram_message("fake_token", "fake_chat", "test text")
            self.assertIsNotNone(res)
            self.assertEqual(mock_retry.call_count, 2)
            # Second call should not have parse_mode
            fallback_req = mock_retry.call_args_list[1][0][0]
            fallback_data = json.loads(fallback_req.data.decode("utf-8"))
            self.assertNotIn("parse_mode", fallback_data)

    def test_youtube_format_prioritizes_native_m4a(self):
        import inspect
        from podcast_sync.youtube import YouTubeFetcher
        src = inspect.getsource(YouTubeFetcher.download_audio_for_video)
        self.assertIn("ba[ext=m4a]/ba[acodec^=mp4a]/bestaudio/best", src)

    def test_transcript_data_model_and_rss(self):
        from podcast_sync.feed import PodcastTranscript

        t_zh = PodcastTranscript(
            url="https://podcast.hemajia.fun/transcripts/wangzhian/v123.zh-Hans.vtt",
            type="text/vtt",
            language="zh-CN",
            rel="captions",
        )
        t_en = PodcastTranscript(
            url="https://podcast.hemajia.fun/transcripts/wangzhian/v123.en.vtt",
            type="text/vtt",
            language="en",
            rel="captions",
        )

        ep = PodcastEpisode(
            video_id="v123",
            title="Episode with Transcripts",
            description="Testing transcripts",
            pub_date=datetime(2026, 9, 17, 10, 0, 0, tzinfo=timezone.utc),
            duration_seconds=120,
            audio_filename="audio/wangzhian/v123.m4a",
            audio_url="https://podcast.hemajia.fun/audio/wangzhian/v123.m4a",
            file_size_bytes=100000,
            transcripts=[t_zh, t_en],
        )

        # 1. Test serialization and deserialization
        d = ep.to_dict()
        self.assertIn("transcripts", d)
        self.assertEqual(len(d["transcripts"]), 2)
        self.assertEqual(d["transcripts"][0]["language"], "zh-CN")

        restored_ep = PodcastEpisode.from_dict(d)
        self.assertEqual(len(restored_ep.transcripts), 2)
        self.assertEqual(restored_ep.transcripts[0].url, t_zh.url)

        # 2. Test backward compatibility when transcripts field is missing
        del d["transcripts"]
        legacy_ep = PodcastEpisode.from_dict(d)
        self.assertEqual(legacy_ep.transcripts, [])

        # 3. Test RSS XML generation contains <podcast:transcript>
        ch = PodcastChannel(
            title="Test Show",
            link="https://www.youtube.com/@wangzhian",
            description="Description",
            author="Host",
            image_url="https://podcast.hemajia.fun/cover.jpg",
            episodes=[ep],
        )
        xml_output = generate_podcast_rss(ch)
        self.assertIn("xmlns:podcast=\"https://podcastindex.org/namespace/1.0\"", xml_output)
        self.assertIn("<podcast:transcript", xml_output)
        self.assertIn("url=\"https://podcast.hemajia.fun/transcripts/wangzhian/v123.zh-Hans.vtt\"", xml_output)
        self.assertIn("type=\"text/vtt\"", xml_output)
        self.assertIn("language=\"zh-CN\"", xml_output)

        # 4. Test RSS validation succeeds
        val_res = validate_podcast_rss(xml_output, channel_id="wangzhian", expected_count=1)
        self.assertTrue(val_res["valid"])

    def test_transcript_storage_operations(self):
        from podcast_sync.storage import StorageManager
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = Config(dry_run=True, output_dir=tmp_path, r2_public_url="https://podcast.hemajia.fun")
            storage = StorageManager(cfg)

            dummy_vtt = tmp_path / "v123.zh-Hans.vtt"
            dummy_vtt.write_text("WEBVTT\n00:00:00.000 --> 00:00:05.000\nHello", encoding="utf-8")

            url = storage.upload_transcript(dummy_vtt, "wangzhian", "v123", "zh-Hans")
            self.assertEqual(url, "https://podcast.hemajia.fun/transcripts/wangzhian/v123.zh-Hans.vtt")

    def test_youtube_subtitle_normalization(self):
        from podcast_sync.youtube import YouTubeFetcher
        cfg = Config(dry_run=True)
        yt = YouTubeFetcher(cfg)

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            (tmp_path / "vid1.zh-Hans.vtt").write_text("WEBVTT", encoding="utf-8")
            (tmp_path / "vid1.en.vtt").write_text("WEBVTT", encoding="utf-8")
            (tmp_path / "vid1.zh-Hant.vtt").write_text("WEBVTT", encoding="utf-8")

            subs = yt._find_and_normalize_subtitles("vid1", tmp_path)
            langs = [s["language"] for s in subs]
            self.assertIn("zh-CN", langs)
            self.assertIn("en", langs)
            self.assertIn("zh-TW", langs)
            # zh-CN should be sorted first
            self.assertEqual(subs[0]["language"], "zh-CN")

    def test_subtitle_backfill_in_sync_single_channel(self):
        from unittest.mock import MagicMock
        from podcast_sync.main import sync_single_channel
        from podcast_sync.storage import StorageManager
        from podcast_sync.youtube import YouTubeFetcher

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            cfg = Config(dry_run=True, output_dir=tmp_path, r2_public_url="https://podcast.hemajia.fun")
            channel_cfg = ChannelConfig(id="testch", url="https://www.youtube.com/@testch")

            # Create an existing episode with NO transcripts
            existing_ep = PodcastEpisode(
                video_id="existing_1",
                title="Existing Episode",
                description="Desc",
                pub_date=datetime(2026, 9, 17, 8, 0, 0, tzinfo=timezone.utc),
                duration_seconds=180,
                audio_filename="audio/testch/existing_1.m4a",
                audio_url="https://podcast.hemajia.fun/audio/testch/existing_1.m4a",
                file_size_bytes=5000,
                transcripts=[],
            )

            # Mock storage
            storage = StorageManager(cfg)
            storage.load_episodes_manifest = MagicMock(return_value=[existing_ep])
            storage.load_ignored_videos = MagicMock(return_value={})
            storage.cleanup_orphan_and_expired_audio = MagicMock()
            storage.cleanup_orphan_and_expired_transcripts = MagicMock()
            storage.save_episodes_manifest = MagicMock()
            storage.upload_channel_feed = MagicMock(return_value="https://podcast.hemajia.fun/testch.xml")

            # Mock youtube
            yt = YouTubeFetcher(cfg)
            yt.get_channel_info_and_entries = MagicMock(return_value=(
                {"title": "Test Show", "link": "https://www.youtube.com/@testch", "description": "Desc", "author": "Host", "image_url": ""},
                [{"id": "existing_1", "title": "Existing Episode"}]
            ))

            # When backfill is called, return a dummy subtitle
            dummy_vtt = tmp_path / "existing_1.zh-Hans.vtt"
            dummy_vtt.write_text("WEBVTT\n00:00:01.000 --> 00:00:04.000\nHello", encoding="utf-8")
            yt.fetch_subtitles_for_video = MagicMock(return_value=[{
                "language": "zh-CN",
                "lang_suffix": "zh-Hans",
                "local_vtt_path": dummy_vtt,
            }])

            res = sync_single_channel(channel_cfg, cfg, storage, yt)
            self.assertEqual(res["total"], 1)
            # Verify subtitles were backfilled onto the existing episode!
            self.assertEqual(len(existing_ep.transcripts), 1)
            self.assertEqual(existing_ep.transcripts[0].language, "zh-CN")
            self.assertIn("existing_1.zh-Hans.vtt", existing_ep.transcripts[0].url)
            # Verify manifest was saved
            storage.save_episodes_manifest.assert_called_once()


if __name__ == "__main__":
    unittest.main()
