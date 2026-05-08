"""
Tests for vCon converter functionality.

These integration tests require the `vcon` library (>=0.9.1). The tests
in test_vcon_extensions.py cover spec-shape contracts without that dep.
"""

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from siprec_srs.config import LawfulBasisConfig
from siprec_srs.vcon_converter import VConConverter
from siprec_srs.rtp_handler import RTPHandler, RTPConfig


# ISO-8601 with explicit timezone (Z or +HH:MM / -HH:MM offset).
ISO8601_WITH_TZ = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})$"
)


def _session_data(**overrides):
    base = {
        'session_id': 'test_session_123',
        'call_id': 'call_456@example.com',
        'recording_session_id': 'rec_789',
        'remote_uri': 'sip:alice@example.com',
        'local_uri': 'sip:srs@example.com',
        'participants': [
            {'id': 'caller_1', 'name': 'Alice Smith', 'tel': '+1234567890', 'role': 'caller'},
            {'id': 'callee_1', 'name': 'Bob Jones', 'tel': '+1987654321', 'role': 'callee'},
        ],
        'start_time': '2026-05-08T12:00:00+00:00',
        'end_time': '2026-05-08T12:05:00+00:00',
        'media_streams': [{'type': 'audio', 'codec': 'PCMU', 'index': 0}],
    }
    base.update(overrides)
    return base


def _empty_rtp_handler():
    return RTPHandler(RTPConfig())


def _rtp_handler_with_audio(audio_files):
    """Return an RTPHandler-shaped mock yielding the given {stream_id: path}."""
    handler = MagicMock(spec=RTPHandler)
    handler.get_audio_files.return_value = audio_files
    return handler


def _make_wav(path: Path, payload_bytes: int = 0):
    """Write a minimal valid WAV file at `path`."""
    # 16-bit mono 8000Hz, payload bytes of zeros.
    header = (
        b'RIFF' + (36 + payload_bytes).to_bytes(4, 'little')
        + b'WAVEfmt ' + (16).to_bytes(4, 'little')
        + (1).to_bytes(2, 'little')   # PCM
        + (1).to_bytes(2, 'little')   # 1 channel
        + (8000).to_bytes(4, 'little')
        + (16000).to_bytes(4, 'little')
        + (2).to_bytes(2, 'little')
        + (16).to_bytes(2, 'little')
        + b'data' + payload_bytes.to_bytes(4, 'little')
    )
    path.write_bytes(header + b'\x00' * payload_bytes)


# ---------------------------------------------------------------------------
# Compliance contract tests (no audio)
# ---------------------------------------------------------------------------

class TestSpecCompliance:
    """Each test asserts one piece of draft-ietf-vcon-vcon-core-02 conformance."""

    def setup_method(self):
        self.converter = VConConverter()

    def test_syntax_version_is_0_4_0(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        assert vcon is not None
        assert vcon.vcon_dict["vcon"] == "0.4.0"

    def test_uuid_present(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        assert vcon.uuid is not None
        assert len(str(vcon.uuid)) > 0

    def test_extensions_declared(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        extensions = vcon.vcon_dict.get("extensions", [])
        assert "sip-signaling" in extensions
        assert "lawful_basis" in extensions

    def test_parties_drop_non_spec_kwargs(self):
        """Party objects must not carry `role`/`uuid` as top-level fields."""
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        for party_dict in vcon.vcon_dict.get("parties", []):
            # Spec-typed fields only at top level.
            assert "role" not in party_dict
            # `uuid` is not a Party field in core; was being passed in.
            assert "uuid" not in party_dict


class TestSessionMetadataAttachment:
    def setup_method(self):
        self.converter = VConConverter()

    def test_present_with_correct_purpose(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        attachments = vcon.vcon_dict.get("attachments", [])
        assert any(a.get("purpose") == "session_metadata" for a in attachments)

    def test_required_fields(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        meta = next(
            a for a in vcon.vcon_dict["attachments"]
            if a.get("purpose") == "session_metadata"
        )
        assert meta["party"] == 0
        assert meta["dialog"] == 0
        assert meta["encoding"] == "json"
        # body must be a string (core spec)
        assert isinstance(meta["body"], str)
        body = json.loads(meta["body"])
        assert body["call_id"] == "call_456@example.com"


class TestSipSignalingExtension:
    def setup_method(self):
        self.converter = VConConverter()

    def test_emits_sip_message_trace_attachment(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        atts = vcon.vcon_dict.get("attachments", [])
        traces = [a for a in atts if a.get("purpose") == "sip-message-trace"]
        assert len(traces) == 1
        body = json.loads(traces[0]["body"])
        assert body["call_id"] == "call_456@example.com"
        assert body["recording_session_id"] == "rec_789"

    def test_no_call_id_skips_extension(self):
        vcon = self.converter.convert_session_to_vcon(
            _session_data(call_id=''), _empty_rtp_handler()
        )
        atts = vcon.vcon_dict.get("attachments", [])
        assert not any(a.get("purpose") == "sip-message-trace" for a in atts)


class TestLawfulBasisAttachment:
    def test_default_config_emits_attachment(self):
        converter = VConConverter()
        vcon = converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        atts = vcon.vcon_dict.get("attachments", [])
        lb = [a for a in atts if a.get("type") == "lawful_basis"]
        assert len(lb) == 1
        # Spec exception: lawful_basis uses `type:` not `purpose:`.
        assert "purpose" not in lb[0]
        body = json.loads(lb[0]["body"])
        assert body["lawful_basis"] == "legitimate_interests"

    def test_disabled_config_omits_attachment(self):
        converter = VConConverter(
            lawful_basis_config=LawfulBasisConfig(enabled=False)
        )
        vcon = converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        atts = vcon.vcon_dict.get("attachments", [])
        assert not any(a.get("type") == "lawful_basis" for a in atts)
        # And the extension must NOT be declared if no attachment was emitted.
        assert "lawful_basis" not in vcon.vcon_dict.get("extensions", [])

    def test_custom_purposes_propagate(self):
        converter = VConConverter(
            lawful_basis_config=LawfulBasisConfig(
                enabled=True,
                lawful_basis="consent",
                purposes=["recording", "analysis", "training"],
            )
        )
        vcon = converter.convert_session_to_vcon(
            _session_data(), _empty_rtp_handler()
        )
        lb = next(
            a for a in vcon.vcon_dict["attachments"]
            if a.get("type") == "lawful_basis"
        )
        body = json.loads(lb["body"])
        assert body["lawful_basis"] == "consent"
        assert {g["purpose"] for g in body["purpose_grants"]} == {
            "recording", "analysis", "training"
        }


# ---------------------------------------------------------------------------
# Recording dialog contract (with audio)
# ---------------------------------------------------------------------------

class TestRecordingDialog:
    def setup_method(self):
        self.converter = VConConverter()

    def test_dialog_uses_base64url_encoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav, payload_bytes=8)
            handler = _rtp_handler_with_audio({"stream_0": str(wav)})

            vcon = self.converter.convert_session_to_vcon(
                _session_data(), handler
            )
            recordings = [d for d in vcon.dialog if d.get("type") == "recording"]
            assert recordings, "expected at least one recording dialog"
            for rec in recordings:
                assert rec["encoding"] == "base64url"

    def test_dialog_has_originator(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav, payload_bytes=8)
            handler = _rtp_handler_with_audio({"stream_0": str(wav)})

            vcon = self.converter.convert_session_to_vcon(
                _session_data(), handler
            )
            for rec in (d for d in vcon.dialog if d.get("type") == "recording"):
                assert "originator" in rec
                assert isinstance(rec["originator"], int)

    def test_dialog_carries_sip_call_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav, payload_bytes=8)
            handler = _rtp_handler_with_audio({"stream_0": str(wav)})

            vcon = self.converter.convert_session_to_vcon(
                _session_data(), handler
            )
            for rec in (d for d in vcon.dialog if d.get("type") == "recording"):
                assert rec["sip_call_id"] == "call_456@example.com"

    def test_per_stream_party_mapping(self):
        """When stream count == participant count, each stream maps 1:1."""
        with tempfile.TemporaryDirectory() as tmp:
            files = {}
            for i in range(2):
                p = Path(tmp) / f"stream_{i}.wav"
                _make_wav(p, payload_bytes=8)
                files[f"stream_{i}"] = str(p)
            handler = _rtp_handler_with_audio(files)

            vcon = self.converter.convert_session_to_vcon(
                _session_data(), handler  # 2 participants
            )
            recordings = [d for d in vcon.dialog if d.get("type") == "recording"]
            assert len(recordings) == 2
            # streams sorted -> stream_0 -> party 0, stream_1 -> party 1
            assert recordings[0]["parties"] == [0]
            assert recordings[1]["parties"] == [1]

    def test_stream_provenance_attachment_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav, payload_bytes=8)
            handler = _rtp_handler_with_audio({"stream_0": str(wav)})

            vcon = self.converter.convert_session_to_vcon(
                _session_data(), handler
            )
            provs = [
                a for a in vcon.vcon_dict["attachments"]
                if a.get("purpose") == "stream_provenance"
            ]
            assert len(provs) == 1
            body = json.loads(provs[0]["body"])
            assert body["stream_id"] == "stream_0"
            assert body["source"] == "rtp_capture"
