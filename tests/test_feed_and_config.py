import unittest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from pathlib import Path

from podcast_sync.config import Config
from podcast_sync.feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, format_duration


class TestPodcastSync(unittest.TestCase):
    def test_duration_formatter(self):
        self.assertEqual(format_duration(45), "00:45")
        self.assertEqual(format_duration(125), "02:05")
        self.assertEqual(format_duration(3665), "01:01:05")

    def test_rss_generation(self):
        ep1 = PodcastEpisode(
            video_id="dQw4w9WgXcQ",
            title="Test Episode 1: Never Gonna Give You Up",
            description="A classic music video turned podcast episode.",
            pub_date=datetime(2026, 9, 16, 12, 0, 0, tzinfo=timezone.utc),
            duration_seconds=212,
            audio_filename="audio/dQw4w9WgXcQ.m4a",
            audio_url="https://pub-example.r2.dev/audio/dQw4w9WgXcQ.m4a",
            file_size_bytes=3456789,
            thumbnail_url="https://img.youtube.com/vi/dQw4w9WgXcQ/maxresdefault.jpg",
            webpage_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

        channel = PodcastChannel(
            title="My Tech Show",
            link="https://www.youtube.com/@mytechshow",
            description="Conversations about technology and software engineering.",
            author="Tech Host",
            image_url="https://pub-example.r2.dev/cover.jpg",
            language="zh-cn",
            category="Technology",
            episodes=[ep1],
        )

        xml_output = generate_podcast_rss(channel)

        # 1. Must parse as valid XML
        root = ET.fromstring(xml_output)
        self.assertEqual(root.tag, "rss")
        self.assertEqual(root.attrib.get("version"), "2.0")

        # 2. Channel check
        channel_elem = root.find("channel")
        self.assertIsNotNone(channel_elem)
        self.assertEqual(channel_elem.find("title").text, "My Tech Show")
        self.assertEqual(channel_elem.find("link").text, "https://www.youtube.com/@mytechshow")

        # 3. iTunes namespaces check
        itunes_ns = "http://www.itunes.com/dtds/podcast-1.0.dtd"
        author_elem = channel_elem.find(f"{{{itunes_ns}}}author")
        self.assertIsNotNone(author_elem)
        self.assertEqual(author_elem.text, "Tech Host")

        # 4. Item enclosure check
        item = channel_elem.find("item")
        self.assertIsNotNone(item)
        self.assertEqual(item.find("title").text, ep1.title)
        enclosure = item.find("enclosure")
        self.assertIsNotNone(enclosure)
        self.assertEqual(enclosure.attrib["url"], ep1.audio_url)
        self.assertEqual(enclosure.attrib["type"], "audio/x-m4a")
        self.assertEqual(enclosure.attrib["length"], str(ep1.file_size_bytes))

        guid = item.find("guid")
        self.assertEqual(guid.text, f"yt-video-{ep1.video_id}")

    def test_config_defaults(self):
        cfg = Config(channel_url="https://www.youtube.com/@test", dry_run=True)
        self.assertTrue(cfg.dry_run)
        self.assertEqual(cfg.max_episodes, 15)
        self.assertEqual(cfg.podcast_language, "zh-cn")
        # Validate should pass for dry-run even without R2 credentials
        cfg.validate()


if __name__ == "__main__":
    unittest.main()
