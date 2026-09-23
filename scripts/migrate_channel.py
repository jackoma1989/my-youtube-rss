import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import boto3
from dotenv import load_dotenv

load_dotenv()

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{os.environ.get('R2_ACCOUNT_ID')}.r2.cloudflarestorage.com",
    aws_access_key_id=os.environ.get("R2_ACCESS_KEY_ID"),
    aws_secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY"),
    region_name="auto",
)
bucket = os.environ.get("R2_BUCKET_NAME")


def _copy_single_audio(k: str, old_prefix: str, new_id: str):
    filename = k.replace(old_prefix, "")
    new_k = f"audio/{new_id}/{filename}"
    # 使用服务端直接拷贝 copy_object，不走客户端高阶分片逻辑
    s3.copy_object(
        Bucket=bucket,
        CopySource=f"{bucket}/{k}",
        Key=new_k,
    )
    return k, new_k


def migrate(old_id: str, new_id: str):
    print(f"Migrating [{old_id}] -> [{new_id}] in R2 bucket '{bucket}'...", flush=True)

    # 1. Migrate audio files (多线程并发 + 服务端直拷)
    old_audio_prefix = f"audio/{old_id}/"
    res = s3.list_objects_v2(Bucket=bucket, Prefix=old_audio_prefix)
    audio_keys = [o["Key"] for o in res.get("Contents", [])]
    total_audio = len(audio_keys)
    print(f"Found {total_audio} audio files to migrate.", flush=True)

    if total_audio > 0:
        max_workers = min(16, total_audio)
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_key = {
                executor.submit(_copy_single_audio, k, old_audio_prefix, new_id): k
                for k in audio_keys
            }
            completed = 0
            for future in as_completed(future_to_key):
                completed += 1
                orig_k, target_k = future.result()
                print(f"  [{completed}/{total_audio}] Copied {orig_k} -> {target_k}", flush=True)

    # 2. Migrate cover
    old_cover = f"covers/{old_id}.jpg"
    new_cover = f"covers/{new_id}.jpg"
    try:
        s3.head_object(Bucket=bucket, Key=old_cover)
        print(f"Copying {old_cover} -> {new_cover}...", flush=True)
        s3.copy_object(
            Bucket=bucket,
            CopySource=f"{bucket}/{old_cover}",
            Key=new_cover,
        )
    except Exception as e:
        print(f"No cover found at {old_cover}: {e}", flush=True)

    # 3. Migrate manifest
    old_manifest_key = f"channels/{old_id}/episodes.json"
    new_manifest_key = f"channels/{new_id}/episodes.json"
    try:
        obj = s3.get_object(Bucket=bucket, Key=old_manifest_key)
        raw_manifest = obj["Body"].read().decode("utf-8")
        manifest_data = json.loads(raw_manifest)

        for ep in manifest_data:
            if "audio_filename" in ep and ep["audio_filename"].startswith(old_audio_prefix):
                ep["audio_filename"] = ep["audio_filename"].replace(old_audio_prefix, f"audio/{new_id}/")
            if "audio_url" in ep and f"audio/{old_id}/" in ep["audio_url"]:
                ep["audio_url"] = ep["audio_url"].replace(f"audio/{old_id}/", f"audio/{new_id}/")
            if "channel_id" in ep:
                ep["channel_id"] = new_id

        updated_manifest_bytes = json.dumps(manifest_data, ensure_ascii=False, indent=2).encode("utf-8")
        s3.put_object(
            Bucket=bucket,
            Key=new_manifest_key,
            Body=updated_manifest_bytes,
            ContentType="application/json; charset=utf-8",
        )
        print(f"Successfully migrated manifest: {new_manifest_key} ({len(manifest_data)} episodes)", flush=True)
    except Exception as e:
        print(f"Error migrating manifest: {e}", flush=True)

    # 4. Migrate RSS feeds
    old_feed_keys = [f"{old_id}.xml", f"{old_id}/feed.xml"]
    for old_feed in old_feed_keys:
        try:
            obj = s3.get_object(Bucket=bucket, Key=old_feed)
            xml_text = obj["Body"].read().decode("utf-8")
            updated_xml = xml_text.replace(f"/{old_id}.", f"/{new_id}.").replace(f"/{old_id}/", f"/{new_id}/")
            new_feed = old_feed.replace(old_id, new_id)
            s3.put_object(
                Bucket=bucket,
                Key=new_feed,
                Body=updated_xml.encode("utf-8"),
                ContentType="application/rss+xml; charset=utf-8",
                CacheControl="public, max-age=300",
            )
            print(f"Successfully migrated feed {old_feed} -> {new_feed}", flush=True)
        except Exception as e:
            print(f"Error migrating feed {old_feed}: {e}", flush=True)

    # 5. Clean up old objects (批量删除 delete_objects，一次网络请求搞定)
    print("\nCleaning up old objects in batch...", flush=True)
    keys_to_delete = audio_keys + [old_cover, old_manifest_key] + old_feed_keys
    delete_payload = [{"Key": k} for k in keys_to_delete]

    if delete_payload:
        try:
            s3.delete_objects(
                Bucket=bucket,
                Delete={"Objects": delete_payload, "Quiet": True},
            )
            print(f"  Batch deleted {len(delete_payload)} old keys.", flush=True)
        except Exception as e:
            print(f"  Failed batch deleting objects: {e}", flush=True)

    print(f"\nMigration of [{old_id}] -> [{new_id}] complete!", flush=True)


if __name__ == "__main__":
    migrate("douyin_2", "geekerwan")
