import html
import json
import logging
import urllib.parse
import urllib.request
from typing import Optional

from .feed import PodcastEpisode, format_duration

logger = logging.getLogger(__name__)


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: Optional[str] = "HTML",
    disable_web_page_preview: bool = True,
    timeout: int = 10,
) -> Optional[dict]:
    """
    Send text message to Telegram Bot API using standard urllib.
    Falls back to plain text if HTML/Markdown parsing fails.
    """
    if not bot_token or not chat_id:
        logger.debug("Telegram bot_token or chat_id not provided. Skipping notification.")
        return None

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload_data = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload_data["parse_mode"] = parse_mode

    data = json.dumps(payload_data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            logger.info("Telegram notification delivered successfully.")
            return res
    except Exception as e:
        logger.warning(f"Telegram notification attempt failed ({e}). Retrying as plain text...")
        try:
            # Fallback to plain text without parse_mode
            plain_data = {
                "chat_id": chat_id,
                "text": text,
                "disable_web_page_preview": disable_web_page_preview,
            }
            req_plain = urllib.request.Request(
                url,
                data=json.dumps(plain_data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req_plain, timeout=timeout) as resp_plain:
                res_plain = json.loads(resp_plain.read().decode("utf-8"))
                logger.info("Telegram notification delivered successfully via plain text fallback.")
                return res_plain
        except Exception as e2:
            logger.error(f"Telegram notification delivery failed completely: {e2}")
            return None


def send_new_episode_notification(
    bot_token: str,
    chat_id: str,
    channel_title: str,
    episode: PodcastEpisode,
    feed_url: str = "",
) -> Optional[dict]:
    """
    Build formatted notification card for a newly synced episode and send via Telegram.
    """
    if not bot_token or not chat_id:
        return None

    safe_channel = html.escape(channel_title or "YouTube 频道")
    safe_title = html.escape(episode.title or "新单集")
    duration_str = format_duration(episode.duration_seconds)
    pub_str = (
        episode.pub_date.strftime("%Y-%m-%d %H:%M:%S UTC")
        if episode.pub_date
        else "刚刚"
    )

    lines = [
        "🎙️ <b>【YouTube 播客有新单集更新】</b>",
        "",
        f"📺 <b>频道</b>: {safe_channel}",
        f"🎬 <b>单集</b>: {safe_title}",
        f"⏱️ <b>时长</b>: {duration_str}",
        f"📅 <b>发布</b>: {pub_str}",
        "",
    ]
    if feed_url:
        lines.append(f"📻 <b>播客源</b>: <code>{feed_url}</code>")
    if episode.webpage_url:
        lines.append(f"▶️ <b>原片地址</b>: {episode.webpage_url}")

    message_html = "\n".join(lines)
    return send_telegram_message(
        bot_token=bot_token,
        chat_id=chat_id,
        text=message_html,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )
