import os
import boto3
from dotenv import load_dotenv

load_dotenv()

account_id = os.environ.get("R2_ACCOUNT_ID")
access_key = os.environ.get("R2_ACCESS_KEY_ID")
secret_key = os.environ.get("R2_SECRET_ACCESS_KEY")
bucket_name = os.environ.get("R2_BUCKET_NAME")

if not all([account_id, access_key, secret_key, bucket_name]):
    print("Missing R2 credentials in environment.")
    exit(1)

s3 = boto3.client(
    "s3",
    endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
    aws_access_key_id=access_key,
    aws_secret_access_key=secret_key,
    region_name="auto",
)

try:
    response = s3.list_multipart_uploads(Bucket=bucket_name)
    uploads = response.get("Uploads", [])
    print(f"Found {len(uploads)} incomplete multipart upload(s):")
    for u in uploads:
        key = u["Key"]
        upload_id = u["UploadId"]
        initiated = u.get("Initiated")
        print(f" - Key: {key}, Initiated: {initiated}, UploadId: {upload_id}")
        
        # Abort the upload
        s3.abort_multipart_upload(Bucket=bucket_name, Key=key, UploadId=upload_id)
        print(f"   Successfully aborted incomplete upload for {key}!")
        
    if not uploads:
        print("No incomplete multipart uploads found in bucket.")
except Exception as e:
    print(f"Error querying/aborting multipart uploads: {e}")
