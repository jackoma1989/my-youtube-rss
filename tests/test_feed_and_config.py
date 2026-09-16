import unittest
from datetime import datetime, timezone
import xml.etree.ElementTree as ET
from pathlib import Path
import tempfile
import json

from podcast_sync.config import ChannelConfig, Config, sanitize_channel_id
from podcast_sync.feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, format_duration


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


if __name__ == "__main__":
    unittest.main()
