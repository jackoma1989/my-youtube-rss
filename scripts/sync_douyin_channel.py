"""
Douyin to Apple Podcasts RSS Sync Script
Syncs Douyin creator's audio to Cloudflare R2 and generates Apple Podcasts compliant RSS.
"""

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from curl_cffi import requests
from dotenv import load_dotenv

# Ensure fervel-nobel root is on sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from podcast_sync.config import Config
from podcast_sync.feed import PodcastChannel, PodcastEpisode, generate_podcast_rss, validate_podcast_rss
from podcast_sync.storage import StorageManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("douyin_sync")

def sync_lukoudaye():
    # Auto-detect local proxy (port 7890) for faster R2 uploads
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.3)
    if s.connect_ex(('127.0.0.1', 7890)) == 0:
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        logger.info("Using local proxy at 127.0.0.1:7890 for R2 upload optimization")
    s.close()

    load_dotenv(BASE_DIR / ".env")
    config = Config.from_env(env_path=BASE_DIR / ".env")
    storage = StorageManager(config)

    channel_id = "caijinglukou"
    channel_name = "路口大爷"
    channel_url = "https://www.douyin.com/user/MS4wLjABAAAAfvBbG3svnuAlE41qFjO64nq5H7NBU7y6b17PeY-Mi7c"
    channel_desc = "路口大爷（抖音号：caijinglukouDY）音频播客。专注于宏观经济政策解读、产业趋势分析与商业企业观察。"
    channel_author = "路口大爷"
    channel_category = "Business"

    # 1. Channel Cover Image
    cover_r2_key = f"covers/{channel_id}.jpg"
    channel_image_url = f"{config.r2_public_url}/{cover_r2_key}"

    # Fetch avatar if not yet uploaded
    logger.info("Checking channel cover image...")
    avatar_source_url = "https://p3-pc.douyinpic.com/aweme/1080x1080/aweme-avatar/tos-cn-i-0813_bf52842dd53845b48c5ec6ac414f4bbf.jpeg?from=327834062"
    try:
        resp = requests.get(avatar_source_url, impersonate="chrome120", timeout=15)
        if resp.status_code == 200:
            storage.s3_client.put_object(
                Bucket=config.r2_bucket_name,
                Key=cover_r2_key,
                Body=resp.content,
                ContentType="image/jpeg",
                CacheControl="public, max-age=604800",
            )
            logger.info(f"Channel cover uploaded to: {channel_image_url}")
    except Exception as e:
        logger.warning(f"Could not upload avatar: {e}")

    # 2. Existing local downloaded files to sync
    known_episodes = [
        {
            "video_id": "7688373194587261739",
            "title": "大企业无息拖账，小企业负重前行，新版三角债有多残酷？",
            "description": "大企业无息拖账，小企业负重前行，新版三角债有多残酷？ #燃起来了大国重器 #真财实学计划 #青年创作者成长计划",
            "pub_date": datetime(2026, 9, 22, 22, 54, 6, tzinfo=timezone.utc),
            "duration_seconds": 470,
            "local_file": Path(r"X:\路口大爷\路口大爷 - 20260922 - 大企业无息拖账，小企业负重前行，新版三角债有多残酷.mp3"),
            "thumbnail_url": "https://p3-pc-sign.douyinpic.com/tos-cn-i-dy/bce49e3d3ae14c0bad8ba953ed72d5f9~tplv-dy-cropcenter:323:430.jpeg?lk3s=138a59ce&x-expires=2105496000&x-signature=AhSmy1ZBknKZXIImfSIgKEefv94%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=true&sh=323_430&sc=cover&biz_tag=pcweb_cover&l=2026092312090741DF8995884B3631A3E0",
            "webpage_url": "https://www.douyin.com/video/7688373194587261739",
        },
        {
            "video_id": "7686474710946810665",
            "title": "调查数百家企业：AI用得好的企业，都做对了3件事",
            "description": "调查数百家企业：AI用得好的企业，都做对了3件事 #2026飞书未来无限大会#豆包工作#飞书#八马茶业#圣农集团",
            "pub_date": datetime(2026, 9, 18, 18, 1, 0, tzinfo=timezone.utc),
            "duration_seconds": 320,
            "local_file": Path(r"X:\路口大爷\路口大爷 - 20260918 - 调查数百家企业：AI用得好的企业，都做对了3件事.mp3"),
            "thumbnail_url": "https://p3-pc-sign.douyinpic.com/tos-cn-i-dy/dea40f33f098451b84843b5eb1ad4210~tplv-dy-cropcenter:323:430.jpeg?lk3s=138a59ce&x-expires=2105496000&x-signature=3ndXgk724%2BWL6inC5sLASdXPz7A%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=true&sh=323_430&sc=cover&biz_tag=pcweb_cover&l=2026092312090741DF8995884B3631A3E0",
            "webpage_url": "https://www.douyin.com/video/7686474710946810665",
        },
        {
            "video_id": "7203181629991308605",
            "title": "我为什么叫“路口大爷”呢？",
            "description": "我为什么叫“路口大爷”呢？#我在抖音说财经",
            "pub_date": datetime(2023, 2, 23, 11, 4, 36, tzinfo=timezone.utc),
            "duration_seconds": 215,
            "local_file": Path(r"X:\路口大爷\路口大爷 - 我为什么叫“路口大爷”呢.mp3"),
            "thumbnail_url": "https://p3-pc-sign.douyinpic.com/tos-cn-p-0015/og1AoN22EgC9zjEIBiOhAgBC1AefE2Qcj4u1jr~tplv-dy-cropcenter:323:430.jpeg?lk3s=138a59ce&x-expires=2105496000&x-signature=dA7AhT%2BcU6Ul0StBhRs%2BolIbDiQ%3D&from=327834062&s=PackSourceEnum_PUBLISH&se=true&sh=323_430&sc=cover&biz_tag=pcweb_cover&l=20260923120816BC91DD3CBA8014B70D62",
            "webpage_url": "https://www.douyin.com/video/7203181629991308605",
        },
    ]

    episodes = []
    for item in known_episodes:
        vid = item["video_id"]
        local_p = item["local_file"]
        if not local_p.exists():
            logger.warning(f"File {local_p} not found, skipping...")
            continue

        file_size = local_p.stat().st_size
        filename = f"audio/{channel_id}/{vid}.mp3"
        audio_public_url = f"{config.r2_public_url}/{filename}"

        logger.info(f"Checking / uploading {local_p.name} ({file_size / 1024 / 1024:.2f} MB)...")
        # Upload audio to R2
        start_t = time.time()
        storage.upload_audio(local_p, channel_id, vid)
        elapsed = time.time() - start_t
        logger.info(f"Upload of {vid} completed in {elapsed:.2f}s")

        ep = PodcastEpisode(
            video_id=vid,
            title=item["title"],
            description=item["description"],
            pub_date=item["pub_date"],
            duration_seconds=item["duration_seconds"],
            audio_filename=filename,
            audio_url=audio_public_url,
            file_size_bytes=file_size,
            thumbnail_url=item["thumbnail_url"],
            webpage_url=item["webpage_url"],
        )
        episodes.append(ep)

    # 3. Save episodes manifest
    storage.save_episodes_manifest(channel_id, episodes)

    # 4. Generate RSS XML
    channel_obj = PodcastChannel(
        title=channel_name,
        link=channel_url,
        description=channel_desc,
        author=channel_author,
        category=channel_category,
        language="zh-cn",
        image_url=channel_image_url,
        episodes=episodes,
    )

    rss_xml = generate_podcast_rss(channel_obj)
    
    # 5. Validate RSS XML
    logger.info("Validating generated RSS XML...")
    validate_podcast_rss(rss_xml, channel_id, expected_count=len(episodes), r2_public_url=config.r2_public_url)
    logger.info("RSS XML validation passed!")

    # 6. Upload feed to R2
    feed_url = storage.upload_channel_feed(channel_id, rss_xml)
    logger.info(f"\n==================================================")
    logger.info(f"SUCCESS! Podcast RSS Feed Published:")
    logger.info(f" -> {feed_url}")
    logger.info(f"==================================================")
    return feed_url

if __name__ == "__main__":
    sync_lukoudaye()
