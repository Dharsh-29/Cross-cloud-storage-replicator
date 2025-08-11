import os
import json
import time
import logging
import boto3
from google.cloud import storage
import urllib.parse  # <-- Added for decoding S3 object key

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Read environment variables
GCS_BUCKET = os.environ.get("GCS_BUCKET")              # Your GCS bucket name
GCP_SA_KEY = os.environ.get("GCP_SA_KEY")              # Raw JSON string of your GCP service account key (preferred)
GCP_SA_KEY_B64 = os.environ.get("GCP_SA_KEY_B64")      # Base64-encoded JSON string (optional)
RETRY_ATTEMPTS = int(os.environ.get("RETRY_ATTEMPTS", "3"))
RETRY_BACKOFF = int(os.environ.get("RETRY_BACKOFF_SEC", "2"))

s3 = boto3.client("s3")

def init_gcs_client():
    sa_json = None
    if GCP_SA_KEY:
        sa_json = GCP_SA_KEY
    elif GCP_SA_KEY_B64:
        import base64
        sa_json = base64.b64decode(GCP_SA_KEY_B64).decode("utf-8")
    else:
        raise RuntimeError("GCP_SA_KEY or GCP_SA_KEY_B64 environment variable is required")

    tmp_path = "/tmp/gcp_sa_key.json"
    with open(tmp_path, "w") as f:
        f.write(sa_json)
    client = storage.Client.from_service_account_json(tmp_path)
    return client

def s3_get_object_stream(bucket, key):
    resp = s3.get_object(Bucket=bucket, Key=key)
    body = resp["Body"]  # StreamingBody
    size = resp.get("ContentLength")
    return body, size

def upload_stream_to_gcs(bucket_obj, dest_blob_name, s3_stream):
    blob = bucket_obj.blob(dest_blob_name)
    with blob.open("wb") as f:
        chunk_size = 8 * 1024 * 1024  # 8 MB
        while True:
            chunk = s3_stream.read(chunk_size)
            if not chunk:
                break
            f.write(chunk)

def file_exists_and_same_size(bucket_obj, blob_name, s3_size):
    blob = bucket_obj.blob(blob_name)
    if not blob.exists():
        return False
    try:
        return (s3_size is not None) and (blob.size == s3_size)
    except Exception:
        return False

def retry_fn(fn, attempts=3, backoff=2):
    last_exc = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last_exc = e
            logger.warning(f"Attempt {i+1} failed: {e}")
            if i < attempts - 1:
                time.sleep(backoff * (i+1))
    raise last_exc

def lambda_handler(event, context):
    logger.info(f"Received event: {json.dumps(event)}")

    try:
        record = event["Records"][0]
        s3_bucket = record["s3"]["bucket"]["name"]

        # Decode S3 object key to handle spaces and special characters
        raw_key = record["s3"]["object"]["key"]
        s3_key = urllib.parse.unquote_plus(raw_key)

    except Exception as e:
        logger.error(f"Invalid event format: {e}")
        raise

    gcs_client = init_gcs_client()
    gcs_bucket_obj = gcs_client.bucket(GCS_BUCKET)

    s3_stream, s3_size = s3_get_object_stream(s3_bucket, s3_key)

    # Idempotency: skip if same file with same size exists on GCS
    try:
        if file_exists_and_same_size(gcs_bucket_obj, s3_key, s3_size):
            logger.info(f"File already exists in GCS with same size; skipping: {s3_key}")
            try:
                s3_stream.close()
            except:
                pass
            return {"statusCode": 200, "body": "Skipped - file already exists"}
    except Exception as e:
        logger.warning(f"Idempotency check failed, proceeding anyway: {e}")

    # Upload with retries
    def upload():
        stream, _ = s3_get_object_stream(s3_bucket, s3_key)
        upload_stream_to_gcs(gcs_bucket_obj, s3_key, stream)
        try:
            stream.close()
        except:
            pass
        return True

    retry_fn(upload, attempts=RETRY_ATTEMPTS, backoff=RETRY_BACKOFF)

    logger.info(f"Successfully replicated s3://{s3_bucket}/{s3_key} -> gs://{GCS_BUCKET}/{s3_key}")

    return {"statusCode": 200, "body": "Replication successful"}
