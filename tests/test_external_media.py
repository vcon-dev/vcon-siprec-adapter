"""Tests for external-media mode (URL + sha512 content_hash)."""

import base64
import hashlib
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from siprec_srs.config import FilesystemMediaConfig, MediaConfig
from siprec_srs.media_publisher import PublishingError
from siprec_srs.rtp_handler import RTPHandler
from siprec_srs.vcon_converter import VConConverter


CONTENT_HASH_RE = re.compile(r"^sha512-[A-Za-z0-9_-]+$")


def _make_wav(path: Path, payload_bytes: int = 64):
    header = (
        b'RIFF' + (36 + payload_bytes).to_bytes(4, 'little')
        + b'WAVEfmt ' + (16).to_bytes(4, 'little')
        + (1).to_bytes(2, 'little')
        + (1).to_bytes(2, 'little')
        + (8000).to_bytes(4, 'little')
        + (16000).to_bytes(4, 'little')
        + (2).to_bytes(2, 'little')
        + (16).to_bytes(2, 'little')
        + b'data' + payload_bytes.to_bytes(4, 'little')
    )
    path.write_bytes(header + b'\x00' * payload_bytes)


def _session():
    return {
        'session_id': 's', 'call_id': 'c@e', 'recording_session_id': 'r',
        'remote_uri': 'sip:a@e', 'local_uri': 'sip:b@e',
        'participants': [{'name': 'A', 'tel': '+1'}],
        'start_time': '2026-05-08T12:00:00+00:00',
        'media_streams': [],
    }


def _handler(audio_files):
    h = MagicMock(spec=RTPHandler)
    h.get_audio_files.return_value = audio_files
    return h


class TestSha512ContentHash:
    def test_format_matches_spec(self):
        converter = VConConverter()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.wav"
            p.write_bytes(b"hello world")
            ch = converter._sha512_content_hash(str(p))
            assert CONTENT_HASH_RE.match(ch)

    def test_value_matches_stdlib_digest(self):
        converter = VConConverter()
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "a.wav"
            data = b"the quick brown fox"
            p.write_bytes(data)

            ch = converter._sha512_content_hash(str(p))
            expected_b64 = base64.urlsafe_b64encode(
                hashlib.sha512(data).digest()
            ).decode("ascii").rstrip("=")
            assert ch == f"sha512-{expected_b64}"


class TestExternalMediaDialog:
    def setup_method(self):
        self.media_cfg = MediaConfig(
            mode="external",
            base_url="https://media.example.com/recordings",
        )
        self.converter = VConConverter(media_config=self.media_cfg)

    def test_dialog_carries_url_and_content_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav)
            handler = _handler({"stream_0": str(wav)})

            vcon = self.converter.convert_session_to_vcon(_session(), handler)
            recordings = [d for d in vcon.dialog if d.get("type") == "recording"]
            assert len(recordings) == 1
            rec = recordings[0]

            assert rec["url"] == "https://media.example.com/recordings/stream_0.wav"
            assert CONTENT_HASH_RE.match(rec["content_hash"])
            # External mode must NOT also embed the body.
            assert "body" not in rec or not rec.get("body")
            # Encoding is irrelevant when there's no body.
            assert "encoding" not in rec or rec["encoding"] is None

    def test_external_none_without_base_url_fails_at_startup(self):
        bad_cfg = MediaConfig(mode="external", base_url=None)

        with pytest.raises(PublishingError):
            VConConverter(media_config=bad_cfg)

    def test_filesystem_mode_publishes_before_adding_dialog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "stream_0.wav"
            _make_wav(wav)
            media_cfg = MediaConfig(
                mode="external",
                publisher="filesystem",
                filesystem=FilesystemMediaConfig(
                    path=str(root / "published")
                ),
            )
            converter = VConConverter(media_config=media_cfg)

            vcon = converter.convert_session_to_vcon(
                _session(), _handler({"stream_0": str(wav)})
            )

            recording = next(
                d for d in vcon.dialog if d.get("type") == "recording"
            )
            published = root / "published" / "r" / "stream_0.wav"
            assert published.read_bytes() == wav.read_bytes()
            assert recording["url"] == published.resolve().as_uri()
            assert CONTENT_HASH_RE.match(recording["content_hash"])

    def test_external_mode_aborts_when_any_stream_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "stream_0.wav"
            _make_wav(wav)
            converter = VConConverter(media_config=self.media_cfg)

            vcon = converter.convert_session_to_vcon(
                _session(),
                _handler({
                    "stream_0": str(wav),
                    "stream_1": str(root / "missing.wav"),
                }),
            )

            assert vcon is None

    def test_inline_mode_default_still_embeds_body(self):
        converter = VConConverter()  # default MediaConfig: inline
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav)
            handler = _handler({"stream_0": str(wav)})

            vcon = converter.convert_session_to_vcon(_session(), handler)
            rec = next(d for d in vcon.dialog if d.get("type") == "recording")
            assert rec["encoding"] == "base64url"
            assert rec.get("body")
            assert "url" not in rec or rec["url"] is None
