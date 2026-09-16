import xml.etree.ElementTree as ET
from xml.dom import minidom
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import format_datetime
from typing import List, Optional


@dataclass
class PodcastEpisode:
    video_id: str
    title: str
    description: str
    pub_date: datetime
    duration_seconds: int
    audio_filename: str
    audio_url: str
    file_size_bytes: int
    thumbnail_url: Optional[str] = None
    webpage_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "description": self.description,
            "pub_date": self.pub_date.isoformat(),
            "duration_seconds": self.duration_seconds,
            "audio_filename": self.audio_filename,
            "audio_url": self.audio_url,
            "file_size_bytes": self.file_size_bytes,
            "thumbnail_url": self.thumbnail_url,
            "webpage_url": self.webpage_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PodcastEpisode":
        return cls(
            video_id=d["video_id"],
            title=d["title"],
            description=d.get("description", ""),
            pub_date=datetime.fromisoformat(d["pub_date"]),
            duration_seconds=int(d.get("duration_seconds", 0)),
            audio_filename=d["audio_filename"],
            audio_url=d["audio_url"],
            file_size_bytes=int(d.get("file_size_bytes", 0)),
            thumbnail_url=d.get("thumbnail_url"),
            webpage_url=d.get("webpage_url"),
        )


@dataclass
class PodcastChannel:
    title: str
    link: str
    description: str
    author: str
    image_url: str
    language: str = "zh-cn"
    category: str = "Technology"
    episodes: List[PodcastEpisode] = None

    def __post_init__(self):
        if self.episodes is None:
            self.episodes = []


def format_duration(seconds: int) -> str:
    """Format duration in seconds into HH:MM:SS or MM:SS."""
    if not seconds or seconds < 0:
        return "00:00"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def generate_podcast_rss(channel: PodcastChannel) -> str:
    """Generate an Apple Podcasts-compliant RSS 2.0 XML string."""
    ITUNES_NS = "http://www.itunes.com/dtds/podcast-1.0.dtd"
    CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"

    ET.register_namespace("itunes", ITUNES_NS)
    ET.register_namespace("content", CONTENT_NS)

    rss = ET.Element("rss", {"version": "2.0"})

    channel_elem = ET.SubElement(rss, "channel")

    ET.SubElement(channel_elem, "title").text = channel.title
    ET.SubElement(channel_elem, "link").text = channel.link
    ET.SubElement(channel_elem, "description").text = channel.description or channel.title
    ET.SubElement(channel_elem, "language").text = channel.language
    ET.SubElement(channel_elem, "generator").text = "YouTube-to-Apple-Podcasts-Sync"

    # itunes specific elements
    ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}author").text = channel.author
    ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}summary").text = channel.description or channel.title
    ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}type").text = "episodic"
    ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}explicit").text = "false"

    # Category
    cat_elem = ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}category")
    cat_elem.set("text", channel.category)

    # Owner
    owner_elem = ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}owner")
    ET.SubElement(owner_elem, f"{{{ITUNES_NS}}}name").text = channel.author
    ET.SubElement(owner_elem, f"{{{ITUNES_NS}}}email").text = "podcast-sync@noreply.com"

    # Channel Image
    if channel.image_url:
        ET.SubElement(channel_elem, f"{{{ITUNES_NS}}}image", {"href": channel.image_url})
        image_elem = ET.SubElement(channel_elem, "image")
        ET.SubElement(image_elem, "url").text = channel.image_url
        ET.SubElement(image_elem, "title").text = channel.title
        ET.SubElement(image_elem, "link").text = channel.link

    # Sort episodes by pub_date descending
    sorted_episodes = sorted(channel.episodes, key=lambda ep: ep.pub_date, reverse=True)

    for ep in sorted_episodes:
        item = ET.SubElement(channel_elem, "item")
        ET.SubElement(item, "title").text = ep.title
        ET.SubElement(item, f"{{{ITUNES_NS}}}title").text = ep.title
        ET.SubElement(item, f"{{{ITUNES_NS}}}author").text = channel.author

        # Description
        desc_text = ep.description if ep.description else ep.title
        ET.SubElement(item, "description").text = desc_text

        # Link to original youtube video
        if ep.webpage_url:
            ET.SubElement(item, "link").text = ep.webpage_url

        # Guid
        guid = ET.SubElement(item, "guid", {"isPermaLink": "false"})
        guid.text = f"yt-video-{ep.video_id}"

        # PubDate in RFC 2822
        pub_date_tz = ep.pub_date if ep.pub_date.tzinfo else ep.pub_date.replace(tzinfo=timezone.utc)
        ET.SubElement(item, "pubDate").text = format_datetime(pub_date_tz)

        # Enclosure (Audio stream link)
        ET.SubElement(
            item,
            "enclosure",
            {
                "url": ep.audio_url,
                "length": str(ep.file_size_bytes),
                "type": "audio/x-m4a",
            },
        )

        # Duration
        if ep.duration_seconds > 0:
            ET.SubElement(item, f"{{{ITUNES_NS}}}duration").text = format_duration(ep.duration_seconds)

        ET.SubElement(item, f"{{{ITUNES_NS}}}explicit").text = "false"

        # Item cover image
        if ep.thumbnail_url:
            ET.SubElement(item, f"{{{ITUNES_NS}}}image", {"href": ep.thumbnail_url})

    # Return pretty formatted XML with standard header
    rough_string = ET.tostring(rss, encoding="utf-8")
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ", encoding="utf-8").decode("utf-8")


def validate_podcast_rss(
    rss_xml: str,
    channel_id: str,
    expected_count: Optional[int] = None,
    r2_public_url: Optional[str] = None,
) -> dict:
    """
    Validate Apple Podcasts RSS feed XML for syntax, completeness, and audio enclosure accuracy.
    Raises ValueError if validation fails.
    """
    try:
        root = ET.fromstring(rss_xml.encode("utf-8"))
    except Exception as e:
        raise ValueError(f"[{channel_id}] XML parsing failed: {e}")

    if root.tag != "rss" or root.attrib.get("version") != "2.0":
        raise ValueError(f"[{channel_id}] Invalid root RSS tag or version.")

    channel = root.find("channel")
    if channel is None:
        raise ValueError(f"[{channel_id}] Missing <channel> element in RSS.")

    channel_title = channel.findtext("title", "").strip()
    if not channel_title:
        raise ValueError(f"[{channel_id}] Missing channel <title>.")

    items = channel.findall("item")
    if expected_count is not None and len(items) != expected_count:
        raise ValueError(
            f"[{channel_id}] Episode count mismatch: expected {expected_count}, found {len(items)} in XML."
        )

    guids = set()
    expected_audio_prefix = f"/audio/{channel_id}/"

    for idx, item in enumerate(items, start=1):
        item_title = item.findtext("title", "").strip()
        guid = item.findtext("guid", "").strip()
        if not guid:
            raise ValueError(f"[{channel_id}] Item #{idx} ({item_title}) is missing <guid>.")
        if guid in guids:
            raise ValueError(f"[{channel_id}] Duplicate GUID found: {guid}")
        guids.add(guid)

        enclosure = item.find("enclosure")
        if enclosure is None:
            raise ValueError(f"[{channel_id}] Item #{idx} ({item_title}) is missing <enclosure>.")

        audio_url = enclosure.attrib.get("url", "")
        audio_type = enclosure.attrib.get("type", "")
        audio_len = enclosure.attrib.get("length", "0")

        if not audio_url:
            raise ValueError(f"[{channel_id}] Item #{idx} enclosure missing 'url'.")
        if audio_type != "audio/x-m4a":
            raise ValueError(f"[{channel_id}] Item #{idx} enclosure unexpected MIME type: {audio_type}")
        if not audio_len.isdigit() or int(audio_len) <= 0:
            raise ValueError(f"[{channel_id}] Item #{idx} enclosure invalid file length: {audio_len}")

        # Enforce channel isolation in enclosure URL
        if expected_audio_prefix not in audio_url:
            raise ValueError(
                f"[{channel_id}] Enclosure URL validation failed: '{audio_url}' does not contain '{expected_audio_prefix}'!"
            )

    return {
        "valid": True,
        "channel_id": channel_id,
        "title": channel_title,
        "episodes_count": len(items),
    }

