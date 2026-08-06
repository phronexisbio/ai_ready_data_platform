"""MinIO landing-zone access shared by every connector."""

import hashlib
import os

import boto3
from botocore.client import Config

def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set — Phase 12 (BUILD_PLAN_COMMERCIAL.md) removed the "
            f"plaintext-credential fallback default; wire it from the minio-credentials Secret."
        )
    return value


MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "http://minio.data-platform.svc.cluster.local:9000")
MINIO_ACCESS_KEY = _required_env("MINIO_ACCESS_KEY")
MINIO_SECRET_KEY = _required_env("MINIO_SECRET_KEY")


def client():
    return boto3.client(
        "s3",
        endpoint_url=MINIO_ENDPOINT,
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def checksum(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def land(key: str, content: bytes, bucket: str = "landing") -> str:
    """Write bytes to a data lake zone. Returns `bucket/key`."""
    client().put_object(Bucket=bucket, Key=key, Body=content)
    return f"{bucket}/{key}"


def parse_location(location: str) -> tuple[str, str]:
    """`"landing/local/ds/file.fasta"` -> `("landing", "local/ds/file.fasta")`."""
    bucket, _, key = location.partition("/")
    return bucket, key


def get(location: str) -> bytes:
    bucket, key = parse_location(location)
    obj = client().get_object(Bucket=bucket, Key=key)
    return obj["Body"].read()
