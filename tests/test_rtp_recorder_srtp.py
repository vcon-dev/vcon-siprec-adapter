"""RTPRecorder with an SRTPContext: decrypts good packets, drops bad ones."""

import base64
import wave

import pytest

from siprec_srs.rtp_recorder import RTPRecorder
from siprec_srs.srtp import SRTPContext
from tests.test_srtp import INLINE, _hdr, _protect

SUITE = "AES_CM_128_HMAC_SHA1_80"


@pytest.mark.asyncio
async def test_recorder_unprotects_and_counts_failures(tmp_path):
    wav = str(tmp_path / "s.wav")
    rec = RTPRecorder("s", wav, bind_host="127.0.0.1",
                      srtp=SRTPContext(SUITE, INLINE))
    await rec.start()
    sender = SRTPContext(SUITE, INLINE)
    payload = b"\xff" * 160  # PCMU silence
    for seq in range(5):
        rec.handle_packet(_protect(sender, _hdr(seq), payload, 0))
    rec.handle_packet(_hdr(5) + payload)  # plaintext RTP: must fail auth
    rec.stop()

    assert rec.packet_count == 5
    st = rec.stats()
    assert st["srtp"] == SUITE and st["srtp_auth_failures"] == 1
    with wave.open(wav) as w:
        assert w.getnframes() == 5 * 160


def test_plain_rtp_unchanged_without_context(tmp_path):
    rec = RTPRecorder("p", str(tmp_path / "p.wav"))
    assert rec.stats()["srtp"] is None
