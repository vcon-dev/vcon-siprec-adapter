"""Tests for durable external audio publishers."""

import base64
import hashlib
from pathlib import Path

import pytest

from siprec_srs.media_publisher import (
    FilesystemPublisher,
    NonePublisher,
    PublishingError,
    S3Publisher,
)


def expected_hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha512(data).digest())
    return f"sha512-{digest.decode('ascii').rstrip('=')}"


def test_none_publisher_returns_operator_url_and_hash(tmp_path):
    source = tmp_path / "stream.wav"
    source.write_bytes(b"audio")

    published = NonePublisher(
        base_url="https://media.example.com/recordings"
    ).publish(source, "stream.wav")

    assert published.url == "https://media.example.com/recordings/stream.wav"
    assert published.content_hash == expected_hash(b"audio")
    assert published.object_key == "stream.wav"


def test_filesystem_publisher_copies_bytes_and_derives_file_url(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"recorded audio")
    destination = tmp_path / "published"

    published = FilesystemPublisher(destination).publish(
        source, "recording-1/stream-0.wav"
    )

    stored = destination / "recording-1" / "stream-0.wav"
    assert stored.read_bytes() == b"recorded audio"
    assert published.url == stored.resolve().as_uri()
    assert published.content_hash == expected_hash(b"recorded audio")


def test_filesystem_publisher_uses_base_url_override(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")

    published = FilesystemPublisher(
        tmp_path / "published",
        base_url="https://cdn.example.com/audio/",
    ).publish(source, "recording-1/stream-0.wav")

    assert (
        published.url
        == "https://cdn.example.com/audio/recording-1/stream-0.wav"
    )


@pytest.mark.parametrize("object_key", ["../outside.wav", "/outside.wav"])
def test_filesystem_publisher_rejects_keys_outside_destination(
    tmp_path, object_key
):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")

    with pytest.raises(PublishingError):
        FilesystemPublisher(tmp_path / "published").publish(source, object_key)


class FakeS3Client:
    def __init__(self, failures=None):
        self.failures = list(failures or [])
        self.uploads = []

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.uploads.append({
            "body": Path(filename).read_bytes(),
            "bucket": bucket,
            "key": key,
            "extra_args": ExtraArgs,
        })
        if self.failures:
            raise self.failures.pop(0)


def test_s3_publisher_uploads_audio_and_derives_regional_url(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    client = FakeS3Client()
    publisher = S3Publisher(
        bucket="recordings",
        region="us-west-2",
        prefix="siprec",
        client=client,
    )

    published = publisher.publish(source, "recording-1/stream-0.wav")

    assert client.uploads == [{
        "body": b"audio",
        "bucket": "recordings",
        "key": "siprec/recording-1/stream-0.wav",
        "extra_args": {"ContentType": "audio/wav"},
    }]
    assert published.url == (
        "https://recordings.s3.us-west-2.amazonaws.com/"
        "siprec/recording-1/stream-0.wav"
    )
    assert published.content_hash == expected_hash(b"audio")


def test_s3_publisher_retries_transient_failure(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    client = FakeS3Client([TimeoutError("timeout")])
    sleeps = []
    publisher = S3Publisher(
        bucket="recordings",
        retry_attempts=3,
        backoff_factor=0.5,
        client=client,
        sleep=sleeps.append,
    )

    publisher.publish(source, "stream.wav")

    assert len(client.uploads) == 2
    assert sleeps == [0.5]


def test_s3_publisher_does_not_retry_permanent_failure(tmp_path):
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    client = FakeS3Client([ValueError("invalid request")])
    publisher = S3Publisher(
        bucket="recordings",
        retry_attempts=3,
        client=client,
    )

    with pytest.raises(PublishingError):
        publisher.publish(source, "stream.wav")

    assert len(client.uploads) == 1
