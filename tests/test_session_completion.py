"""Application behavior when external audio publishing fails."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from main import SIPRECSRSApp
from siprec_srs.config import Config, MediaConfig
from siprec_srs.media_publisher import PublishingError
from siprec_srs.vcon_converter import VConConverter


class FailingPublisher:
    def publish(self, local_path, object_key):
        raise PublishingError("storage unavailable")


def make_wav(path: Path):
    payload_bytes = 64
    header = (
        b"RIFF" + (36 + payload_bytes).to_bytes(4, "little")
        + b"WAVEfmt " + (16).to_bytes(4, "little")
        + (1).to_bytes(2, "little")
        + (1).to_bytes(2, "little")
        + (8000).to_bytes(4, "little")
        + (16000).to_bytes(4, "little")
        + (2).to_bytes(2, "little")
        + (16).to_bytes(2, "little")
        + b"data" + payload_bytes.to_bytes(4, "little")
    )
    path.write_bytes(header + b"\x00" * payload_bytes)


@pytest.mark.asyncio
async def test_publish_failure_preserves_temp_audio(tmp_path):
    wav = tmp_path / "stream.wav"
    make_wav(wav)
    session = MagicMock()
    session.session_id = "session-1"
    session.call_id = "call-1"
    session.recording_session_id = "recording-1"
    session.participants = [{"name": "Caller", "tel": "+1"}]
    session.vendor_extension = {}
    session.stream_labels = {}
    session.rs_keys = {}
    session.start_time = "2026-07-30T12:00:00+00:00"
    session.end_time = "2026-07-30T12:01:00+00:00"
    session.media_streams = []
    session.remote_uri = "sip:caller@example.com"
    session.local_uri = "sip:agent@example.com"
    session.get_audio_files.return_value = {"stream": str(wav)}

    app = object.__new__(SIPRECSRSApp)
    app.config = Config()
    app.config.media = MediaConfig(
        mode="external",
        publisher="filesystem",
    )
    app.config.webhooks.enabled = False
    app.vcon_converter = VConConverter(
        media_config=app.config.media,
        audio_publisher=FailingPublisher(),
    )
    app.storage_handler = MagicMock()
    app.signer = None

    await app._on_session_complete(session)

    app.storage_handler.save_vcon.assert_not_called()
    assert wav.exists()
