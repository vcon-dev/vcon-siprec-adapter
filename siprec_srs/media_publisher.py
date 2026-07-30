"""Publish recorded audio for externally referenced vCon dialogs."""

import base64
import hashlib
import mimetypes
import os
import shutil
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Optional, Protocol
from urllib.parse import quote


class PublishingError(RuntimeError):
    """Raised when audio cannot be published durably."""


@dataclass(frozen=True)
class PublishedAudio:
    """Location and integrity metadata for published audio."""

    url: str
    content_hash: str
    object_key: str


class AudioPublisher(Protocol):
    """Storage backend used for external audio."""

    def publish(
        self, local_path: Path, object_key: str
    ) -> PublishedAudio:
        """Persist one recording and return its external reference."""
        ...


def sha512_content_hash(path: Path) -> str:
    """Return the vCon content hash for a file."""
    digest = hashlib.sha512()
    try:
        with path.open("rb") as audio_file:
            for chunk in iter(lambda: audio_file.read(65536), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PublishingError(f"Cannot hash audio file {path}: {exc}") from exc

    encoded = base64.urlsafe_b64encode(digest.digest()).decode("ascii")
    return f"sha512-{encoded.rstrip('=')}"


def _validated_object_key(object_key: str) -> str:
    key = PurePosixPath(object_key)
    if key.is_absolute() or not object_key or ".." in key.parts:
        raise PublishingError(f"Unsafe audio object key: {object_key}")
    return key.as_posix()


def _public_url(base_url: str, object_key: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(object_key, safe='/')}"


class NonePublisher:
    """Describe media published separately by the operator."""

    def __init__(self, base_url: str):
        if not base_url:
            raise PublishingError(
                "media.base_url is required when publisher is none"
            )
        self.base_url = base_url

    def publish(self, local_path: Path, object_key: str) -> PublishedAudio:
        key = _validated_object_key(object_key)
        return PublishedAudio(
            url=_public_url(self.base_url, key),
            content_hash=sha512_content_hash(Path(local_path)),
            object_key=key,
        )


class FilesystemPublisher:
    """Copy media to a durable filesystem destination."""

    def __init__(
        self, destination: Path, base_url: Optional[str] = None
    ):
        if not destination:
            raise PublishingError(
                "media.filesystem.path is required for filesystem publishing"
            )
        self.destination = Path(destination).resolve()
        self.base_url = base_url

    def publish(self, local_path: Path, object_key: str) -> PublishedAudio:
        source = Path(local_path)
        key = _validated_object_key(object_key)
        target = (self.destination / key).resolve()
        if (
            self.destination != target
            and self.destination not in target.parents
        ):
            raise PublishingError(
                f"Audio object key escapes destination: {key}"
            )

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{target.name}.", dir=target.parent
            )
            os.close(fd)
            temporary_path = Path(temporary_name)
            try:
                shutil.copy2(source, temporary_path)
                os.replace(temporary_path, target)
            finally:
                temporary_path.unlink(missing_ok=True)
        except OSError as exc:
            raise PublishingError(
                f"Cannot publish {source} to {target}: {exc}"
            ) from exc

        url = (
            _public_url(self.base_url, key)
            if self.base_url
            else target.as_uri()
        )
        return PublishedAudio(
            url=url,
            content_hash=sha512_content_hash(target),
            object_key=key,
        )


class S3Publisher:
    """Upload media to an S3-compatible object store."""

    _RETRYABLE_CODES = {
        "InternalError",
        "RequestTimeout",
        "ServiceUnavailable",
        "SlowDown",
        "Throttling",
    }
    _RETRYABLE_EXCEPTION_NAMES = {
        "ConnectTimeoutError",
        "ConnectionClosedError",
        "EndpointConnectionError",
        "ReadTimeoutError",
    }

    def __init__(
        self,
        bucket: str,
        region: Optional[str] = None,
        prefix: str = "",
        endpoint_url: Optional[str] = None,
        retry_attempts: int = 3,
        backoff_factor: float = 1.0,
        base_url: Optional[str] = None,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if not bucket:
            raise PublishingError("media.s3.bucket is required")
        if retry_attempts < 1:
            raise PublishingError("S3 retry_attempts must be at least 1")
        if backoff_factor < 0:
            raise PublishingError("S3 backoff_factor cannot be negative")
        self.bucket = bucket
        self.region = region
        self.prefix = prefix.strip("/")
        if self.prefix:
            self.prefix = _validated_object_key(self.prefix)
        self.endpoint_url = endpoint_url
        self.retry_attempts = retry_attempts
        self.backoff_factor = backoff_factor
        self.base_url = base_url
        self.sleep = sleep
        self.client = client or self._create_client()

    def _create_client(self):
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:
            raise PublishingError(
                "boto3 is required for S3 media publishing"
            ) from exc
        return boto3.client(
            "s3",
            region_name=self.region,
            endpoint_url=self.endpoint_url,
            config=Config(retries={"total_max_attempts": 1}),
        )

    def publish(self, local_path: Path, object_key: str) -> PublishedAudio:
        source = Path(local_path)
        key = _validated_object_key(object_key)
        if self.prefix:
            key = f"{self.prefix}/{key}"

        content_type = {
            ".wav": "audio/wav",
            ".mp3": "audio/mpeg",
            ".ogg": "audio/ogg",
        }.get(
            source.suffix.lower(),
            mimetypes.guess_type(source.name)[0]
            or "application/octet-stream",
        )
        for attempt in range(self.retry_attempts):
            try:
                self.client.upload_file(
                    str(source),
                    self.bucket,
                    key,
                    ExtraArgs={"ContentType": content_type},
                )
                break
            except Exception as exc:
                final_attempt = attempt + 1 >= self.retry_attempts
                if final_attempt or not self._is_retryable(exc):
                    raise PublishingError(
                        f"Cannot publish {source} to s3://"
                        f"{self.bucket}/{key}: {exc}"
                    ) from exc
                self.sleep(self.backoff_factor * (2 ** attempt))

        return PublishedAudio(
            url=self._object_url(key),
            content_hash=sha512_content_hash(source),
            object_key=key,
        )

    def _object_url(self, object_key: str) -> str:
        if self.base_url:
            return _public_url(self.base_url, object_key)
        if self.endpoint_url:
            return _public_url(
                f"{self.endpoint_url.rstrip('/')}/{self.bucket}",
                object_key,
            )
        if self.region and self.region != "us-east-1":
            host = f"https://{self.bucket}.s3.{self.region}.amazonaws.com"
        else:
            host = f"https://{self.bucket}.s3.amazonaws.com"
        return _public_url(host, object_key)

    @classmethod
    def _is_retryable(cls, exc: Exception) -> bool:
        if isinstance(exc, (TimeoutError, ConnectionError)):
            return True
        if type(exc).__name__ in cls._RETRYABLE_EXCEPTION_NAMES:
            return True
        response = getattr(exc, "response", None) or {}
        error = response.get("Error", {})
        status = response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return status >= 500 or error.get("Code") in cls._RETRYABLE_CODES


def create_audio_publisher(media_config) -> AudioPublisher:
    """Build the configured external audio publisher."""
    if media_config.publisher == "none":
        return NonePublisher(media_config.base_url)
    if media_config.publisher == "filesystem":
        return FilesystemPublisher(
            Path(media_config.filesystem.path),
            base_url=media_config.base_url,
        )
    if media_config.publisher == "s3":
        return S3Publisher(
            bucket=media_config.s3.bucket,
            region=media_config.s3.region,
            prefix=media_config.s3.prefix,
            endpoint_url=media_config.s3.endpoint_url,
            retry_attempts=media_config.s3.retry_attempts,
            backoff_factor=media_config.s3.backoff_factor,
            base_url=media_config.base_url,
        )
    raise PublishingError(
        f"Unsupported media publisher: {media_config.publisher}"
    )
