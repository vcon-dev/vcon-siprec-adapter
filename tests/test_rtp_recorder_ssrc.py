"""Two RTP sources on one port must be detected, not silently merged.

The 2026-08-18 four-party barge captured 5738 packets on stream_2 against ~2860
on its siblings: a second SSRC arriving at the same advertised port, written
into the same WAV, interleaved, audibly chopped. The recorder still writes both
(dropping audio would be worse), but it now says so.
"""

import struct
import types

import pytest

import siprec_srs.rtp_recorder as rr


class _NullWave:
    def writeframes(self, frames):
        pass


@pytest.fixture(autouse=True)
def _stub_audioop(monkeypatch):
    """audioop is a pip shim on 3.13+; the SSRC path does not depend on it."""
    monkeypatch.setattr(rr, "audioop", types.SimpleNamespace(
        ulaw2lin=lambda payload, width: payload * 2,
        alaw2lin=lambda payload, width: payload * 2,
    ))


def _pcmu_packet(ssrc, seq):
    """One 20ms G.711u packet: RTP v2, PT 0, given SSRC."""
    header = struct.pack(">BBHII", 0x80, 0, seq, seq * 160, ssrc)
    return header + b"\xff" * 160


def _recorder(stream_id="stream_2", port=10004):
    rec = rr.RTPRecorder(stream_id, "/dev/null")
    rec._wave = _NullWave()
    rec.local_port = port
    return rec


def test_single_source_is_not_flagged():
    rec = _recorder("stream_0", 10000)
    for seq in range(4):
        rec.handle_packet(_pcmu_packet(0xDEADBEEF, seq))
    assert rec.stats()["mixed_ssrc"] is False
    assert rec.ssrc_summary() == "0xdeadbeef=4"
    assert rec.packet_count == 4


def test_second_ssrc_on_one_port_is_counted_and_warned(caplog):
    rec = _recorder()
    for seq in range(3):
        rec.handle_packet(_pcmu_packet(0xAABBCCDD, seq))
    for seq in range(5):
        rec.handle_packet(_pcmu_packet(0x11223344, seq))

    stats = rec.stats()
    assert stats["mixed_ssrc"] is True
    assert stats["ssrc_counts"] == {"0xaabbccdd": 3, "0x11223344": 5}
    # Both sources are still captured; nothing is dropped.
    assert rec.packet_count == 8
    assert "second SSRC 0x11223344 on udp/10004" in caplog.text


def test_warning_is_once_per_new_ssrc_not_per_packet(caplog):
    rec = _recorder()
    for seq in range(10):
        rec.handle_packet(_pcmu_packet(0xAAAAAAAA, seq))
        rec.handle_packet(_pcmu_packet(0xBBBBBBBB, seq))
    assert caplog.text.count("second SSRC") == 1
    assert rec.stats()["ssrc_counts"] == {"0xaaaaaaaa": 10, "0xbbbbbbbb": 10}


def test_short_and_non_rtp_packets_do_not_register_an_ssrc():
    rec = _recorder()
    rec.handle_packet(b"\x80\x00\x00")          # under 12 bytes
    rec.handle_packet(b"\x00" * 172)            # RTP version 0
    assert rec.ssrc_counts == {}
    assert rec.ssrc_summary() == "none"
