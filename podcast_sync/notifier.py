import base64
import html
import json
import logging
import os
import secrets
import urllib.parse
import urllib.request
import uuid
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
        logger.debug("Telegram bot_token or chat_id not provided. Skipping Telegram notification.")
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


def send_wechat_message(
    bot_token: str,
    user_id: str,
    text: str,
    title: Optional[str] = None,
    context_token: Optional[str] = None,
    base_url: str = "https://ilinkai.weixin.qq.com",
    timeout: int = 15,
) -> Optional[dict]:
    """
    Send text message to WeChat user via Tencent iLink Bot API.
    Zero third-party dependencies (pure standard urllib).
    """
    if not bot_token or not user_id or not text or not str(text).strip():
        logger.debug("WeChat bot_token, user_id or text missing. Skipping WeChat notification.")
        return None

    full_text = f"【{title}】\n\n{text}" if title else text
    target_base = (base_url or "https://ilinkai.weixin.qq.com").rstrip("/")
    url = f"{target_base}/ilink/bot/sendmessage"
    uin = base64.b64encode(str(int.from_bytes(secrets.token_bytes(4), "big")).encode("utf-8")).decode("ascii")

    msg_payload = {
        "from_user_id": "",
        "to_user_id": user_id,
        "client_id": str(uuid.uuid4()),
        "message_type": 2,
        "message_state": 2,
        "item_list": [
            {
                "type": 1,
                "text_item": {
                    "text": full_text
                }
            }
        ]
    }
    if context_token:
        msg_payload["context_token"] = context_token

    payload = {
        "msg": msg_payload,
        "base_info": {
            "channel_version": "2.2.0"
        }
    }
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {bot_token}",
        "Content-Length": str(len(body)),
        "X-WECHAT-UIN": uin,
        "iLink-App-Id": "bot",
        "iLink-App-ClientVersion": "131584",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw_res = resp.read().decode("utf-8")
            res = json.loads(raw_res)
            msg_id = res.get("message_id")
            ret_code = res.get("ret", 0)
            if msg_id or ret_code == 0:
                logger.info(f"WeChat notification delivered successfully (msg_id: {msg_id}).")
                return res
            else:
                logger.error(f"WeChat gateway returned error response: {res}")
                return None
    except Exception as e:
        logger.error(f"Failed to send WeChat notification: {e}")
        return None


def get_feishu_tenant_access_token(
    app_id: str,
    app_secret: str,
    timeout: int = 10,
) -> Optional[str]:
    """
    Obtain internal tenant_access_token for Feishu Bot API.
    Zero third-party dependencies (pure standard urllib).
    """
    if not app_id or not app_secret:
        return None

    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("code") == 0:
                token = data.get("tenant_access_token")
                return token
            else:
                logger.error(f"Failed to get Feishu tenant_access_token: {data.get('msg')} (code: {data.get('code')})")
                return None
    except Exception as e:
        logger.error(f"Error fetching Feishu tenant_access_token: {e}")
        return None


def send_feishu_message(
    app_id: Optional[str] = None,
    app_secret: Optional[str] = None,
    receive_id: Optional[str] = None,
    receive_id_type: Optional[str] = None,
    text: str = "",
    title: Optional[str] = None,
    webhook_url: Optional[str] = None,
    timeout: int = 15,
) -> Optional[dict]:
    """
    Send notification to Feishu via OpenAPI (Hermes Bot App) or Custom Bot Webhook.
    Zero third-party dependencies.
    """
    # 1. Custom Bot Webhook mode
    if webhook_url:
        full_text = f"【{title}】\n\n{text}" if title else text
        payload = {
            "msg_type": "text",
            "content": {"text": full_text},
        }
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                if res.get("StatusCode") == 0 or res.get("code") == 0:
                    logger.info("Feishu notification delivered successfully via Webhook.")
                    return res
                else:
                    logger.error(f"Feishu webhook returned error: {res}")
                    return None
        except Exception as e:
            logger.error(f"Failed to send Feishu webhook notification: {e}")
            return None

    # 2. Feishu OpenAPI App mode (Hermes Bot)
    if not app_id or not app_secret or not receive_id:
        logger.debug("Feishu app_id, app_secret or receive_id missing. Skipping Feishu notification.")
        return None

    token = get_feishu_tenant_access_token(app_id, app_secret, timeout=timeout)
    if not token:
        return None

    # Auto detect receive_id_type if not specified
    if not receive_id_type:
        if receive_id.startswith("oc_"):
            receive_id_type = "chat_id"
        elif receive_id.startswith("ou_"):
            receive_id_type = "open_id"
        elif receive_id.startswith("on_"):
            receive_id_type = "union_id"
        else:
            receive_id_type = "chat_id"

    url = f"https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={receive_id_type}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }

    # Attempt 1: Interactive Card message for best readability
    card_title = title or "播客有新单集更新"
    card_content = {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": card_title},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"tag": "lark_md", "content": text},
            }
        ],
    }

    card_payload = {
        "receive_id": receive_id,
        "msg_type": "interactive",
        "content": json.dumps(card_content, ensure_ascii=False),
    }

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(card_payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            if res.get("code") == 0:
                logger.info("Feishu notification delivered successfully via interactive card.")
                return res
            else:
                logger.warning(f"Feishu interactive card failed ({res.get('msg')}), falling back to text message...")
    except Exception as e:
        logger.warning(f"Feishu interactive card attempt failed ({e}). Retrying with plain text message...")

    # Attempt 2: Text message fallback
    try:
        plain_text = f"【{title}】\n\n{text}" if title else text
        text_payload = {
            "receive_id": receive_id,
            "msg_type": "text",
            "content": json.dumps({"text": plain_text}, ensure_ascii=False),
        }
        req_text = urllib.request.Request(
            url,
            data=json.dumps(text_payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req_text, timeout=timeout) as resp_text:
            res_text = json.loads(resp_text.read().decode("utf-8"))
            if res_text.get("code") == 0:
                logger.info("Feishu notification delivered successfully via text fallback.")
                return res_text
            else:
                logger.error(f"Feishu text message failed completely: {res_text}")
                return None
    except Exception as e2:
        logger.error(f"Failed to deliver Feishu text message: {e2}")
        return None


def _format_episode_plain(
    platform_name: str,
    channel_label: str,
    channel_title: str,
    episode: PodcastEpisode,
    feed_url: str = "",
) -> str:
    """Format clean plain text for WeChat or fallback notifications."""
    duration_str = format_duration(episode.duration_seconds)
    pub_str = (
        episode.pub_date.strftime("%Y-%m-%d %H:%M:%S")
        if episode.pub_date
        else "刚刚"
    )

    lines = [
        f"{channel_label}: {channel_title or '未知'}",
        f"🎬 单集: {episode.title or '新单集'}",
        f"⏱️ 时长: {duration_str}",
        f"📅 发布: {pub_str}",
    ]
    if feed_url:
        lines.append(f"📻 播客源: {feed_url}")
    if episode.webpage_url:
        lines.append(f"🔗 原视频: {episode.webpage_url}")

    return "\n".join(lines)


def _format_episode_feishu_markdown(
    platform_name: str,
    channel_label: str,
    channel_title: str,
    episode: PodcastEpisode,
    feed_url: str = "",
) -> str:
    """Format structured Markdown for Feishu rich interactive card."""
    duration_str = format_duration(episode.duration_seconds)
    pub_str = (
        episode.pub_date.strftime("%Y-%m-%d %H:%M:%S")
        if episode.pub_date
        else "刚刚"
    )

    lines = [
        f"**{channel_label}**: {channel_title or '未知'}",
        f"**🎬 单集**: {episode.title or '新单集'}",
        f"**⏱️ 时长**: {duration_str}",
        f"**📅 发布**: {pub_str}",
    ]
    if feed_url:
        lines.append(f"**📻 播客源**: `{feed_url}`")
    if episode.webpage_url:
        lines.append(f"**🔗 原视频**: [点击查看原视频]({episode.webpage_url})")

    return "\n\n".join(lines)


def send_dual_episode_notification(
    platform: str,
    creator_name: str,
    episode: PodcastEpisode,
    feed_url: str = "",
    # Telegram config
    telegram_bot_token: Optional[str] = None,
    telegram_chat_id: Optional[str] = None,
    # WeChat config
    wechat_bot_token: Optional[str] = None,
    wechat_user_id: Optional[str] = None,
    wechat_context_token: Optional[str] = None,
    wechat_base_url: str = "https://ilinkai.weixin.qq.com",
    # Feishu config
    feishu_app_id: Optional[str] = None,
    feishu_app_secret: Optional[str] = None,
    feishu_receive_id: Optional[str] = None,
    feishu_receive_id_type: Optional[str] = None,
    feishu_webhook_url: Optional[str] = None,
) -> dict:
    """
    Multi-channel notification dispatcher (Telegram + WeChat + Feishu).
    Sends notifications in parallel with complete fault isolation.
    Failure on one platform does not impact the others.
    """
    results = {"telegram": None, "wechat": None, "feishu": None}

    # Normalize platform meta
    platform_lower = (platform or "youtube").lower()
    if platform_lower in ("bilibili", "b站"):
        title_banner = "Bilibili 播客有新单集更新"
        channel_label = "👤 UP主"
        default_name = "Bilibili UP主"
        orig_label = "B站原视频"
        icon = "📺"
    elif platform_lower in ("douyin", "抖音"):
        title_banner = "抖音播客有新单集更新"
        channel_label = "👤 博主"
        default_name = "抖音创作者"
        orig_label = "抖音原视频"
        icon = "🎵"
    else:
        title_banner = "YouTube 播客有新单集更新"
        channel_label = "📺 频道"
        default_name = "YouTube 频道"
        orig_label = "原片地址"
        icon = "🎙️"

    name = creator_name or default_name

    # 1. Telegram Notification (HTML formatted)
    if telegram_bot_token and telegram_chat_id:
        try:
            safe_name = html.escape(name)
            safe_title = html.escape(episode.title or "新单集")
            duration_str = format_duration(episode.duration_seconds)
            pub_str = (
                episode.pub_date.strftime("%Y-%m-%d %H:%M:%S UTC")
                if episode.pub_date and platform_lower == "youtube"
                else (episode.pub_date.strftime("%Y-%m-%d %H:%M:%S") if episode.pub_date else "刚刚")
            )

            tg_lines = [
                f"{icon} <b>【{title_banner}】</b>",
                "",
                f"{channel_label}: {safe_name}",
                f"🎬 <b>单集</b>: {safe_title}",
                f"⏱️ <b>时长</b>: {duration_str}",
                f"📅 <b>发布</b>: {pub_str}",
            ]
            tg_lines.append("")
            if feed_url:
                tg_lines.append(f"📻 <b>播客源</b>: <code>{feed_url}</code>")
            if episode.webpage_url:
                if platform_lower in ("bilibili", "douyin"):
                    tg_lines.append(f"🔗 <b>{orig_label}</b>: <a href=\"{episode.webpage_url}\">点击查看</a>")
                else:
                    tg_lines.append(f"▶️ <b>{orig_label}</b>: {episode.webpage_url}")

            tg_html = "\n".join(tg_lines)
            results["telegram"] = send_telegram_message(
                bot_token=telegram_bot_token,
                chat_id=telegram_chat_id,
                text=tg_html,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception as te:
            logger.error(f"Telegram notification execution error: {te}")

    # 2. WeChat Notification (Clean plain text via Tencent iLink Bot)
    if wechat_bot_token and wechat_user_id:
        try:
            wechat_body = _format_episode_plain(
                platform_name=title_banner,
                channel_label=channel_label,
                channel_title=name,
                episode=episode,
                feed_url=feed_url,
            )
            results["wechat"] = send_wechat_message(
                bot_token=wechat_bot_token,
                user_id=wechat_user_id,
                text=wechat_body,
                title=f"{icon} {title_banner}",
                context_token=wechat_context_token,
                base_url=wechat_base_url,
            )
        except Exception as we:
            logger.error(f"WeChat notification execution error: {we}")

    # 3. Feishu Notification (Lark Interactive Card or Webhook)
    if (feishu_app_id and feishu_app_secret and feishu_receive_id) or feishu_webhook_url:
        try:
            feishu_body = _format_episode_feishu_markdown(
                platform_name=title_banner,
                channel_label=channel_label,
                channel_title=name,
                episode=episode,
                feed_url=feed_url,
            )
            results["feishu"] = send_feishu_message(
                app_id=feishu_app_id,
                app_secret=feishu_app_secret,
                receive_id=feishu_receive_id,
                receive_id_type=feishu_receive_id_type,
                text=feishu_body,
                title=f"{icon} {title_banner}",
                webhook_url=feishu_webhook_url,
            )
        except Exception as fe:
            logger.error(f"Feishu notification execution error: {fe}")

    return results


def send_new_episode_notification(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    channel_title: str = "",
    episode: Optional[PodcastEpisode] = None,
    feed_url: str = "",
    config: Optional[object] = None,
    **kwargs,
) -> Optional[dict]:
    """
    Build formatted notification card for a newly synced episode and send via Telegram, WeChat, and Feishu.
    Maintains full backward compatibility with existing callers.
    """
    if episode is None:
        return None

    if bot_token is not None:
        tg_token = bot_token
    else:
        tg_token = getattr(config, "telegram_bot_token", None) or os.environ.get("TELEGRAM_BOT_TOKEN")

    if chat_id is not None:
        tg_chat = chat_id
    else:
        tg_chat = getattr(config, "telegram_chat_id", None) or os.environ.get("TELEGRAM_CHAT_ID")

    wc_token = kwargs.get("wechat_bot_token") or getattr(config, "wechat_bot_token", None) or os.environ.get("WECHAT_BOT_TOKEN") or os.environ.get("ILINK_BOT_TOKEN")
    wc_user = kwargs.get("wechat_user_id") or getattr(config, "wechat_user_id", None) or os.environ.get("WECHAT_USER_ID")
    wc_ctx = kwargs.get("wechat_context_token") or getattr(config, "wechat_context_token", None) or os.environ.get("WECHAT_CONTEXT_TOKEN")
    wc_base = kwargs.get("wechat_base_url") or getattr(config, "wechat_base_url", "https://ilinkai.weixin.qq.com") or os.environ.get("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")

    fs_app_id = kwargs.get("feishu_app_id") or getattr(config, "feishu_app_id", None) or os.environ.get("FEISHU_APP_ID")
    fs_app_secret = kwargs.get("feishu_app_secret") or getattr(config, "feishu_app_secret", None) or os.environ.get("FEISHU_APP_SECRET")
    fs_recv_id = kwargs.get("feishu_receive_id") or getattr(config, "feishu_receive_id", None) or os.environ.get("FEISHU_RECEIVE_ID") or os.environ.get("FEISHU_HOME_CHANNEL")
    fs_recv_type = kwargs.get("feishu_receive_id_type") or getattr(config, "feishu_receive_id_type", None) or os.environ.get("FEISHU_RECEIVE_ID_TYPE")
    fs_webhook = kwargs.get("feishu_webhook_url") or getattr(config, "feishu_webhook_url", None) or os.environ.get("FEISHU_WEBHOOK_URL")

    # If neither Telegram, WeChat, nor Feishu is configured, return None immediately
    has_tg = bool(tg_token and tg_chat)
    has_wc = bool(wc_token and wc_user)
    has_fs = bool(fs_webhook or (fs_app_id and fs_app_secret and fs_recv_id))
    if not (has_tg or has_wc or has_fs):
        return None

    res = send_dual_episode_notification(
        platform="youtube",
        creator_name=channel_title,
        episode=episode,
        feed_url=feed_url,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
        wechat_bot_token=wc_token,
        wechat_user_id=wc_user,
        wechat_context_token=wc_ctx,
        wechat_base_url=wc_base,
        feishu_app_id=fs_app_id,
        feishu_app_secret=fs_app_secret,
        feishu_receive_id=fs_recv_id,
        feishu_receive_id_type=fs_recv_type,
        feishu_webhook_url=fs_webhook,
    )
    return res.get("telegram") or res.get("wechat") or res.get("feishu")


def send_douyin_episode_notification(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    creator_name: str = "",
    episode: Optional[PodcastEpisode] = None,
    feed_url: str = "",
    config: Optional[object] = None,
    **kwargs,
) -> Optional[dict]:
    """
    Build formatted notification card for a newly synced Douyin episode and send via Telegram, WeChat, and Feishu.
    """
    if episode is None:
        return None

    if bot_token is not None:
        tg_token = bot_token
    else:
        tg_token = getattr(config, "telegram_bot_token", None) or os.environ.get("TELEGRAM_BOT_TOKEN")

    if chat_id is not None:
        tg_chat = chat_id
    else:
        tg_chat = getattr(config, "telegram_chat_id", None) or os.environ.get("TELEGRAM_CHAT_ID")

    wc_token = kwargs.get("wechat_bot_token") or getattr(config, "wechat_bot_token", None) or os.environ.get("WECHAT_BOT_TOKEN") or os.environ.get("ILINK_BOT_TOKEN")
    wc_user = kwargs.get("wechat_user_id") or getattr(config, "wechat_user_id", None) or os.environ.get("WECHAT_USER_ID")
    wc_ctx = kwargs.get("wechat_context_token") or getattr(config, "wechat_context_token", None) or os.environ.get("WECHAT_CONTEXT_TOKEN")
    wc_base = kwargs.get("wechat_base_url") or getattr(config, "wechat_base_url", "https://ilinkai.weixin.qq.com") or os.environ.get("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")

    fs_app_id = kwargs.get("feishu_app_id") or getattr(config, "feishu_app_id", None) or os.environ.get("FEISHU_APP_ID")
    fs_app_secret = kwargs.get("feishu_app_secret") or getattr(config, "feishu_app_secret", None) or os.environ.get("FEISHU_APP_SECRET")
    fs_recv_id = kwargs.get("feishu_receive_id") or getattr(config, "feishu_receive_id", None) or os.environ.get("FEISHU_RECEIVE_ID") or os.environ.get("FEISHU_HOME_CHANNEL")
    fs_recv_type = kwargs.get("feishu_receive_id_type") or getattr(config, "feishu_receive_id_type", None) or os.environ.get("FEISHU_RECEIVE_ID_TYPE")
    fs_webhook = kwargs.get("feishu_webhook_url") or getattr(config, "feishu_webhook_url", None) or os.environ.get("FEISHU_WEBHOOK_URL")

    # If neither Telegram, WeChat, nor Feishu is configured, return None immediately
    has_tg = bool(tg_token and tg_chat)
    has_wc = bool(wc_token and wc_user)
    has_fs = bool(fs_webhook or (fs_app_id and fs_app_secret and fs_recv_id))
    if not (has_tg or has_wc or has_fs):
        return None

    res = send_dual_episode_notification(
        platform="douyin",
        creator_name=creator_name,
        episode=episode,
        feed_url=feed_url,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
        wechat_bot_token=wc_token,
        wechat_user_id=wc_user,
        wechat_context_token=wc_ctx,
        wechat_base_url=wc_base,
        feishu_app_id=fs_app_id,
        feishu_app_secret=fs_app_secret,
        feishu_receive_id=fs_recv_id,
        feishu_receive_id_type=fs_recv_type,
        feishu_webhook_url=fs_webhook,
    )
    return res.get("telegram") or res.get("wechat") or res.get("feishu")


def send_bilibili_episode_notification(
    bot_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    creator_name: str = "",
    episode: Optional[PodcastEpisode] = None,
    feed_url: str = "",
    config: Optional[object] = None,
    **kwargs,
) -> Optional[dict]:
    """
    Build formatted notification card for a newly synced Bilibili episode and send via Telegram, WeChat, and Feishu.
    """
    if episode is None:
        return None

    if bot_token is not None:
        tg_token = bot_token
    else:
        tg_token = getattr(config, "telegram_bot_token", None) or os.environ.get("TELEGRAM_BOT_TOKEN")

    if chat_id is not None:
        tg_chat = chat_id
    else:
        tg_chat = getattr(config, "telegram_chat_id", None) or os.environ.get("TELEGRAM_CHAT_ID")

    wc_token = kwargs.get("wechat_bot_token") or getattr(config, "wechat_bot_token", None) or os.environ.get("WECHAT_BOT_TOKEN") or os.environ.get("ILINK_BOT_TOKEN")
    wc_user = kwargs.get("wechat_user_id") or getattr(config, "wechat_user_id", None) or os.environ.get("WECHAT_USER_ID")
    wc_ctx = kwargs.get("wechat_context_token") or getattr(config, "wechat_context_token", None) or os.environ.get("WECHAT_CONTEXT_TOKEN")
    wc_base = kwargs.get("wechat_base_url") or getattr(config, "wechat_base_url", "https://ilinkai.weixin.qq.com") or os.environ.get("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")

    fs_app_id = kwargs.get("feishu_app_id") or getattr(config, "feishu_app_id", None) or os.environ.get("FEISHU_APP_ID")
    fs_app_secret = kwargs.get("feishu_app_secret") or getattr(config, "feishu_app_secret", None) or os.environ.get("FEISHU_APP_SECRET")
    fs_recv_id = kwargs.get("feishu_receive_id") or getattr(config, "feishu_receive_id", None) or os.environ.get("FEISHU_RECEIVE_ID") or os.environ.get("FEISHU_HOME_CHANNEL")
    fs_recv_type = kwargs.get("feishu_receive_id_type") or getattr(config, "feishu_receive_id_type", None) or os.environ.get("FEISHU_RECEIVE_ID_TYPE")
    fs_webhook = kwargs.get("feishu_webhook_url") or getattr(config, "feishu_webhook_url", None) or os.environ.get("FEISHU_WEBHOOK_URL")

    # If neither Telegram, WeChat, nor Feishu is configured, return None immediately
    has_tg = bool(tg_token and tg_chat)
    has_wc = bool(wc_token and wc_user)
    has_fs = bool(fs_webhook or (fs_app_id and fs_app_secret and fs_recv_id))
    if not (has_tg or has_wc or has_fs):
        return None

    res = send_dual_episode_notification(
        platform="bilibili",
        creator_name=creator_name,
        episode=episode,
        feed_url=feed_url,
        telegram_bot_token=tg_token,
        telegram_chat_id=tg_chat,
        wechat_bot_token=wc_token,
        wechat_user_id=wc_user,
        wechat_context_token=wc_ctx,
        wechat_base_url=wc_base,
        feishu_app_id=fs_app_id,
        feishu_app_secret=fs_app_secret,
        feishu_receive_id=fs_recv_id,
        feishu_receive_id_type=fs_recv_type,
        feishu_webhook_url=fs_webhook,
    )
    return res.get("telegram") or res.get("wechat") or res.get("feishu")
