#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notification Connectivity Tester for GitHub Actions & Local Environments.
Tests Feishu, WeChat, and Telegram notification delivery with detailed logging.
"""

import datetime
import os
import sys
from pathlib import Path

# Ensure project root is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from podcast_sync.notifier import (
    send_feishu_message,
    send_wechat_message,
    send_telegram_message,
)


def main():
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8")
            sys.stderr.reconfigure(encoding="utf-8")
        except Exception:
            pass

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    runner_os = os.environ.get("RUNNER_OS", "Local")
    github_repo = os.environ.get("GITHUB_REPOSITORY", "Local Dev")
    github_run_id = os.environ.get("GITHUB_RUN_ID", "N/A")

    print("=" * 60)
    print(" Notification Connectivity Test")
    print(f" Time:       {now_str}")
    print(f" Runner OS:  {runner_os}")
    print(f" Repository: {github_repo}")
    print(f" Run ID:     {github_run_id}")
    print("=" * 60)

    results = {}

    # 1. Test Feishu Notification
    print("\n[1/3] Testing Feishu (Lark) Notification...")
    fs_app_id = os.environ.get("FEISHU_APP_ID", "").strip() or None
    fs_app_secret = os.environ.get("FEISHU_APP_SECRET", "").strip() or None
    fs_receive_id = os.environ.get("FEISHU_RECEIVE_ID", "").strip() or None
    fs_webhook = os.environ.get("FEISHU_WEBHOOK_URL", "").strip() or None

    if fs_webhook or (fs_app_id and fs_app_secret and fs_receive_id):
        test_body = (
            f"**来源**: GitHub Actions 云端测试\n\n"
            f"- **时间**: {now_str}\n"
            f"- **仓库**: {github_repo}\n"
            f"- **运行 ID**: `{github_run_id}`\n"
            f"- **接收端**: `{fs_receive_id or 'Webhook'}`\n\n"
            f"🎉 恭喜！您在 GitHub 后台配置的飞书 Secrets 已验证成功，通信正常！"
        )
        try:
            res_fs = send_feishu_message(
                app_id=fs_app_id,
                app_secret=fs_app_secret,
                receive_id=fs_receive_id,
                text=test_body,
                title="🔔 GitHub Actions 飞书通知连通性测试",
                webhook_url=fs_webhook,
            )
            if res_fs and (res_fs.get("code") == 0 or res_fs.get("StatusCode") == 0):
                print("✓ Feishu notification delivered successfully!")
                results["feishu"] = "SUCCESS"
            else:
                print(f"✗ Feishu delivery failed: {res_fs}")
                results["feishu"] = f"FAILED: {res_fs}"
        except Exception as e:
            print(f"✗ Feishu exception: {e}")
            results["feishu"] = f"ERROR: {e}"
    else:
        print("- Feishu credentials not fully configured (FEISHU_APP_ID / SECRET / RECEIVE_ID). Skipped.")
        results["feishu"] = "SKIPPED"

    # 2. Test WeChat Notification
    print("\n[2/3] Testing WeChat (iLink Bot) Notification...")
    wc_token = os.environ.get("WECHAT_BOT_TOKEN", "").strip() or None
    wc_user = os.environ.get("WECHAT_USER_ID", "").strip() or None
    wc_ctx = os.environ.get("WECHAT_CONTEXT_TOKEN", "").strip() or None
    wc_base = os.environ.get("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com").strip() or "https://ilinkai.weixin.qq.com"

    if wc_token and wc_user:
        wc_text = (
            f"【GitHub Actions 微信通知测试】\n\n"
            f"时间: {now_str}\n"
            f"仓库: {github_repo}\n"
            f"运行 ID: {github_run_id}\n\n"
            f"微信通道正常就绪！"
        )
        try:
            res_wc = send_wechat_message(
                bot_token=wc_token,
                user_id=wc_user,
                text=wc_text,
                title="🔔 微信通道测试",
                context_token=wc_ctx,
                base_url=wc_base,
            )
            if res_wc and (res_wc.get("ret", 0) == 0 or res_wc.get("message_id")):
                print("✓ WeChat notification delivered successfully!")
                results["wechat"] = "SUCCESS"
            else:
                print(f"✗ WeChat delivery failed: {res_wc}")
                results["wechat"] = f"FAILED: {res_wc}"
        except Exception as e:
            print(f"✗ WeChat exception: {e}")
            results["wechat"] = f"ERROR: {e}"
    else:
        print("- WeChat credentials not provided (WECHAT_BOT_TOKEN / USER_ID). Skipped.")
        results["wechat"] = "SKIPPED"

    # 3. Test Telegram Notification
    print("\n[3/3] Testing Telegram Notification...")
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip() or None
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip() or None

    if tg_token and tg_chat:
        tg_html = (
            f"🔔 <b>【GitHub Actions Telegram 测试】</b>\n\n"
            f"时间: {now_str}\n"
            f"仓库: {github_repo}\n"
            f"运行 ID: <code>{github_run_id}</code>\n\n"
            f"Telegram 通道正常就绪！"
        )
        try:
            res_tg = send_telegram_message(
                bot_token=tg_token,
                chat_id=tg_chat,
                text=tg_html,
                parse_mode="HTML",
            )
            if res_tg and res_tg.get("ok"):
                print("✓ Telegram notification delivered successfully!")
                results["telegram"] = "SUCCESS"
            else:
                print(f"✗ Telegram delivery failed: {res_tg}")
                results["telegram"] = f"FAILED: {res_tg}"
        except Exception as e:
            print(f"✗ Telegram exception: {e}")
            results["telegram"] = f"ERROR: {e}"
    else:
        print("- Telegram credentials not provided (TELEGRAM_BOT_TOKEN / CHAT_ID). Skipped.")
        results["telegram"] = "SKIPPED"

    print("\n" + "=" * 60)
    print(" Summary of Notification Tests:")
    for channel, status in results.items():
        print(f"  - {channel.upper()}: {status}")
    print("=" * 60)

    # If Feishu was tested and failed, exit code 1
    if results.get("feishu", "").startswith("FAIL") or results.get("feishu", "").startswith("ERR"):
        sys.exit(1)


if __name__ == "__main__":
    main()
