"""S3/MinIO helper for listing photos.

Presigning is pure local HMAC computation (no network); only `delete_object`
talks to storage. Two clients because SigV4 signs the Host header: the PUBLIC
endpoint signs URLs the browser PUTs to (localhost:9000 in dev), the INTERNAL
endpoint serves backend-side ops (minio:9000 in dev). Both are None in prod,
which collapses to real AWS S3 — that collapse is the entire deployment switch.
"""

import logging
from functools import lru_cache
from uuid import uuid4

import boto3
from botocore.config import Config
from django.conf import settings

logger = logging.getLogger(__name__)

# content_type -> object-key extension; doubles as the upload whitelist. The
# extension always derives from the content type, never the client filename.
ALLOWED_IMAGE_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


@lru_cache(maxsize=4)
def _client(endpoint: str | None):
    return boto3.client(
        "s3",
        endpoint_url=endpoint,  # None -> real AWS
        region_name=settings.STORAGE_REGION,
        aws_access_key_id=settings.STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.STORAGE_SECRET_KEY,
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path" if endpoint else "virtual"},
        ),
    )


def build_key(user_id: int, content_type: str) -> str:
    return f"listings/{user_id}/{uuid4().hex}.{ALLOWED_IMAGE_TYPES[content_type]}"


def public_url_base() -> str:
    if settings.STORAGE_PUBLIC_URL_BASE:
        return settings.STORAGE_PUBLIC_URL_BASE.rstrip("/")
    if settings.STORAGE_ENDPOINT_PUBLIC:  # MinIO/R2 path-style
        return f"{settings.STORAGE_ENDPOINT_PUBLIC.rstrip('/')}/{settings.STORAGE_BUCKET}"
    return f"https://{settings.STORAGE_BUCKET}.s3.{settings.STORAGE_REGION}.amazonaws.com"


def public_url(key: str) -> str:
    return f"{public_url_base()}/{key}"


def presign_put(key: str, content_type: str) -> str:
    # ContentType is signed: the browser PUT must send exactly this Content-Type
    # header or storage rejects it (no swapping an image key to text/html).
    # Size cannot be enforced in a presigned PUT — the presign endpoint caps the
    # declared size and the client enforces it; presigned POST policies with
    # content-length-range are the hardening path if that ever matters.
    return _client(settings.STORAGE_ENDPOINT_PUBLIC).generate_presigned_url(
        "put_object",
        Params={
            "Bucket": settings.STORAGE_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.STORAGE_PRESIGN_EXPIRY,
    )


def key_for_url(url: str) -> str | None:
    """Our bucket's key for `url`, or None for foreign URLs (seed Unsplash rows,
    legacy pasted URLs) — those never trigger storage calls."""
    prefix = public_url_base() + "/"
    if url.startswith(prefix) and len(url) > len(prefix):
        return url[len(prefix) :]
    return None


def delete_object_for_url(url: str) -> None:
    """Best-effort cleanup when a media row is deleted. Foreign URL -> no-op.
    Storage errors are swallowed — the DB row delete must never fail on storage."""
    key = key_for_url(url)
    if key is None:
        return
    try:
        _client(settings.STORAGE_ENDPOINT_INTERNAL).delete_object(
            Bucket=settings.STORAGE_BUCKET, Key=key
        )
    except Exception:
        logger.warning("storage delete failed for key %s", key, exc_info=True)
