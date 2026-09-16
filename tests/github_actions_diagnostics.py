"""
GitHub Actions Environment & Connectivity Diagnostic Tool
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import boto3
import yt_dlp


def banner(title: str):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


def check_tool_versions():
    banner("1. Tool & Runtime Versions")
    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"yt-dlp: {yt_dlp.version.__version__}")

    for tool in ["ffmpeg", "node", "deno"]:
        path = shutil.which(tool)
        if path:
            try:
                res = subprocess.run([tool, "--version"], capture_output=True, text=True, timeout=5)
                first_line = (res.stdout or res.stderr).splitlines()[0]
                print(f"{tool}: {first_line.strip()} (found at {path})")
            except Exception as e:
                print(f"{tool}: Found at {path} (version check failed: {e})")
        else:
            print(f"{tool}: NOT FOUND in PATH!")


def check_secrets():
    banner("2. Environment & Secrets Check")
    secrets_to_check = [
        "R2_ACCOUNT_ID",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET_NAME",
        "R2_PUBLIC_URL",
        "YOUTUBE_CHANNEL_URL",
        "YOUTUBE_CHANNEL_URL_2",
        "YOUTUBE_CHANNEL_URL_3",
        "YOUTUBE_COOKIES",
    ]

    for sec in secrets_to_check:
        val = os.environ.get(sec, "")
        if val:
            if sec == "YOUTUBE_COOKIES":
                lines = len(val.strip().splitlines())
                has_header = "Netscape" in val
                print(f"✓ {sec}: PRESENT ({len(val)} bytes, {lines} lines, Netscape format={has_header})")
            else:
                masked = val[:4] + "***" + val[-4:] if len(val) > 8 else "***"
                print(f"✓ {sec}: PRESENT ({masked})")
        else:
            print(f"✗ {sec}: MISSING or EMPTY!")


def check_r2_connectivity():
    banner("3. Cloudflare R2 Connectivity Test")
    account_id = os.environ.get("R2_ACCOUNT_ID", "").strip()
    access_key = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
    secret_key = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
    bucket_name = os.environ.get("R2_BUCKET_NAME", "").strip()

    if not all([account_id, access_key, secret_key, bucket_name]):
        print("Skipping R2 check due to missing credentials.")
        return

    try:
        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name="auto",
        )
        s3.head_bucket(Bucket=bucket_name)
        print(f"✓ Successfully connected to Cloudflare R2 bucket: '{bucket_name}'!")
    except Exception as e:
        print(f"✗ Failed to connect to R2: {e}")


def check_youtube_download():
    banner("4. YouTube Extraction & Download Diagnostic")
    cookies_content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    cookie_file = None
    if cookies_content:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        tmp.write(cookies_content)
        tmp.close()
        cookie_file = tmp.name
        print(f"Created temporary cookie file: {cookie_file} ({os.path.getsize(cookie_file)} bytes)")

    test_video_url = "https://www.youtube.com/watch?v=HuGAiGfKqW0"
    print(f"Testing video extraction: {test_video_url}")

    ydl_opts = {
        "format": "bestaudio/best",
        "quiet": False,
        "no_warnings": False,
        "js_runtimes": {"deno": {}, "node": {}},
    }
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(test_video_url, download=False)
            print(f"✓ Extraction SUCCESS!")
            print(f"  Title: {info.get('title')}")
            print(f"  Uploader: {info.get('uploader')}")
            print(f"  Duration: {info.get('duration')}s")
            print(f"  Formats available: {len(info.get('formats', []))}")
    except Exception as e:
        print(f"✗ Extraction FAILED: {e}")
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.unlink(cookie_file)
            except Exception:
                pass


if __name__ == "__main__":
    banner("GITHUB ACTIONS COMPREHENSIVE DIAGNOSTIC")
    check_tool_versions()
    check_secrets()
    check_r2_connectivity()
    check_youtube_download()
    banner("DIAGNOSTIC FINISHED")
