"""Tests for the pluggable transcription provider interface."""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from siprec_srs.config import LawfulBasisConfig
from siprec_srs.rtp_handler import RTPHandler
from siprec_srs.transcription import (
    NoopTranscriptionProvider,
    TranscriptionProvider,
    WTF_SCHEMA_URL,
    WTF_TRANSCRIPTION_EXTENSION,
    add_transcription_analysis,
)
from siprec_srs.vcon_converter import VConConverter


class StubProvider:
    """Test double matching the TranscriptionProvider Protocol."""

    vendor = "stub-asr"
    product = "stub-1.0"

    def __init__(self, doc):
        self._doc = doc
        self.calls = []

    def transcribe(self, audio_path):
        self.calls.append(audio_path)
        return self._doc


class FailingProvider:
    vendor = "boom"
    product = "v0"

    def transcribe(self, audio_path):
        raise RuntimeError("ASR exploded")


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


# ---------------------------------------------------------------------------
# Pure unit tests for add_transcription_analysis
# ---------------------------------------------------------------------------

class TestAddTranscriptionAnalysis:
    def test_protocol_satisfied_by_stub(self):
        # runtime_checkable Protocol — duck-type confirmation.
        assert isinstance(StubProvider({"transcript": {"text": "hi"}}),
                          TranscriptionProvider)

    def test_noop_provider_skips(self):
        d = {}
        added = add_transcription_analysis(
            d, provider=NoopTranscriptionProvider(),
            dialog_index=0, audio_path="/tmp/x.wav",
        )
        assert added is False
        assert "analysis" not in d
        assert "extensions" not in d

    def test_provider_returning_none_skips(self):
        d = {}
        added = add_transcription_analysis(
            d, provider=StubProvider(None),
            dialog_index=0, audio_path="/tmp/x.wav",
        )
        assert added is False
        assert "analysis" not in d

    def test_failing_provider_swallowed(self):
        d = {}
        added = add_transcription_analysis(
            d, provider=FailingProvider(),
            dialog_index=0, audio_path="/tmp/x.wav",
        )
        assert added is False  # error is logged, not raised

    def test_successful_transcription_shape(self):
        d = {}
        wtf_doc = {"transcript": {"text": "hello world"}, "version": "1.0"}
        provider = StubProvider(wtf_doc)
        added = add_transcription_analysis(
            d, provider=provider,
            dialog_index=2, audio_path="/tmp/x.wav",
        )
        assert added is True
        assert WTF_TRANSCRIPTION_EXTENSION in d["extensions"]
        analysis = d["analysis"][0]
        assert analysis["type"] == "transcript"
        assert analysis["vendor"] == "stub-asr"
        assert analysis["product"] == "stub-1.0"
        assert analysis["schema"] == WTF_SCHEMA_URL
        assert analysis["dialog"] == [2]
        assert analysis["encoding"] == "json"
        # body must be a JSON-encoded string per core spec.
        assert isinstance(analysis["body"], str)
        assert json.loads(analysis["body"]) == wtf_doc
        # Speckit Analysis Object lists `mediatype` as recommended.
        assert analysis["mediatype"] == "application/json"

    def test_audio_path_passed_to_provider(self):
        provider = StubProvider({"transcript": {"text": "x"}})
        add_transcription_analysis(
            {}, provider=provider,
            dialog_index=0, audio_path="/some/file.wav",
        )
        assert provider.calls == ["/some/file.wav"]


# ---------------------------------------------------------------------------
# Integration: converter wires the provider into recording dialogs
# ---------------------------------------------------------------------------

class TestConverterIntegration:
    def test_default_converter_emits_no_transcript(self):
        converter = VConConverter()
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "stream_0.wav"
            _make_wav(wav)
            handler = _handler({"stream_0": str(wav)})
            vcon = converter.convert_session_to_vcon(_session(), handler)
            assert vcon.vcon_dict.get("analysis", []) == []
            assert WTF_TRANSCRIPTION_EXTENSION not in vcon.vcon_dict.get(
                "extensions", []
            )

    def test_transcription_provider_emits_analysis_per_dialog(self):
        provider = StubProvider({"transcript": {"text": "hi"}, "version": "1.0"})
        converter = VConConverter(transcription_provider=provider)
        with tempfile.TemporaryDirectory() as tmp:
            files = {}
            for i in range(2):
                p = Path(tmp) / f"stream_{i}.wav"
                _make_wav(p)
                files[f"stream_{i}"] = str(p)
            handler = _handler(files)

            session = _session()
            session['participants'] = [
                {'name': 'A', 'tel': '+1'}, {'name': 'B', 'tel': '+2'}
            ]
            vcon = converter.convert_session_to_vcon(session, handler)

            analysis = vcon.vcon_dict["analysis"]
            assert len(analysis) == 2
            assert {a["dialog"][0] for a in analysis} == {0, 1}
            assert all(a["vendor"] == "stub-asr" for a in analysis)
            assert WTF_TRANSCRIPTION_EXTENSION in vcon.vcon_dict["extensions"]
            assert provider.calls == [files["stream_0"], files["stream_1"]]
