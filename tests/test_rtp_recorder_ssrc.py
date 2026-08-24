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


def test_duplicate_seq_is_dropped_and_counted():
    """The 2026-08-20 barge: one SSRC, every packet delivered twice.

    ssrc_counts alone reported a single source and stayed quiet while stream_2's
    WAV came out at exactly double length (237.6s for a 118.7s call).
    """
    rec = _recorder()
    written = []
    rec._wave = types.SimpleNamespace(writeframes=written.append)
    for seq in range(50):
        rec.handle_packet(_pcmu_packet(0x64C5E7A5, seq))
        rec.handle_packet(_pcmu_packet(0x64C5E7A5, seq))  # same packet again
    assert rec.packet_count == 50           # not 100
    assert len(written) == 50               # the WAV is real time, not 2x
    assert rec.duplicate_count == 50
    assert rec.stats()["duplicate_counts"] == {"0x64c5e7a5": 50}
    assert rec.stats()["mixed_ssrc"] is False   # one source, still a problem


def test_reordering_is_not_duplication():
    """Out-of-order delivery must not be mistaken for a duplicate."""
    rec = _recorder()
    for seq in (0, 1, 3, 2, 4):
        rec.handle_packet(_pcmu_packet(0xAAAAAAAA, seq))
    assert rec.packet_count == 5
    assert rec.duplicate_count == 0


def test_seq_window_forgets_old_numbers():
    """A 16-bit wrap must not collide with a number seen thousands ago."""
    rec = _recorder()
    rec.handle_packet(_pcmu_packet(0xAAAAAAAA, 7))
    for seq in range(100, 100 + rr._SEQ_WINDOW + 5):
        rec.handle_packet(_pcmu_packet(0xAAAAAAAA, seq))
    rec.handle_packet(_pcmu_packet(0xAAAAAAAA, 7))   # wrapped round, not a dup
    assert rec.duplicate_count == 0


def test_duplicates_are_tracked_per_source():
    """Two SSRCs sharing a port each get their own sequence space."""
    rec = _recorder()
    rec.handle_packet(_pcmu_packet(0xAAAAAAAA, 5))
    rec.handle_packet(_pcmu_packet(0xBBBBBBBB, 5))   # same seq, other source
    assert rec.duplicate_count == 0
    rec.handle_packet(_pcmu_packet(0xBBBBBBBB, 5))   # now a real duplicate
    assert rec.stats()["duplicate_counts"] == {"0xbbbbbbbb": 1}
