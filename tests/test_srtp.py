"""RFC 3711 test vectors (Appendix B) and round-trip checks for srtp.py."""

import base64
import hmac
import struct
from hashlib import sha1

import pytest

from siprec_srs import srtp
from siprec_srs.srtp import SRTPContext, SRTPError, choose_crypto

# RFC 3711 B.3 key derivation vectors
MASTER_KEY = bytes.fromhex("E1F97A0D3E018BE0D64FA32C06DE4139")
MASTER_SALT = bytes.fromhex("0EC675AD498AFEEBB6960B3AABE6")
INLINE = base64.b64encode(MASTER_KEY + MASTER_SALT).decode()


def test_rfc3711_key_derivation_vectors():
    ctx = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    assert ctx._enc_key.hex().upper() == "C61E7A93744F39EE10734AFE3FF7A087"
    assert ctx._salt.hex().upper() == "30CBBC08863D8C85D49DB34A9AE1"
    assert ctx._auth_key.hex().upper() == (
        "CEBE321F6FF7716B6FD4AB49AF256A156D38BAA4")


def test_rfc3711_aes_cm_keystream_vector():
    # RFC 3711 B.2
    key = bytes.fromhex("2B7E151628AED2A6ABF7158809CF4F3C")
    iv = bytes.fromhex("F0F1F2F3F4F5F6F7F8F9FAFBFCFD0000")
    ks = srtp._aes_cm_keystream(key, iv, 32)
    assert ks.hex().upper() == (
        "E03EAD0935C95E80E166B16DD92B4EB4D23513162B02D0F72A43A2FE4A5F97AB")


def _protect(ctx: SRTPContext, header: bytes, payload: bytes, roc: int) -> bytes:
    """Reference encrypt (mirror of unprotect) so we can round-trip."""
    seq = struct.unpack(">H", header[2:4])[0]
    ssrc = struct.unpack(">I", header[8:12])[0]
    index = (roc << 16) | seq
    iv = ((int.from_bytes(ctx._salt, "big") << 16)
          ^ (ssrc << 64) ^ (index << 16)).to_bytes(16, "big")
    ks = srtp._aes_cm_keystream(ctx._enc_key, iv, len(payload))
    body = header + bytes(a ^ b for a, b in zip(payload, ks))
    tag = hmac.new(ctx._auth_key, body + struct.pack(">I", roc), sha1).digest()
    return body + tag[:ctx.tag_len]


def _hdr(seq: int, ssrc: int = 0xCAFEBABE, pt: int = 0) -> bytes:
    return struct.pack(">BBHII", 0x80, pt, seq, seq * 160, ssrc)


@pytest.mark.parametrize("suite", list(srtp.SUITES))
def test_round_trip(suite):
    sender = SRTPContext(suite, INLINE)
    receiver = SRTPContext(suite, INLINE)
    payload = bytes(range(160))
    for seq in (100, 101, 102):
        pkt = _protect(sender, _hdr(seq), payload, roc=0)
        assert receiver.unprotect(pkt) == _hdr(seq) + payload
    assert receiver.auth_failures == 0


def test_tamper_fails_auth():
    sender = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    receiver = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    pkt = bytearray(_protect(sender, _hdr(1), b"\x00" * 160, roc=0))
    pkt[20] ^= 0xFF
    with pytest.raises(SRTPError):
        receiver.unprotect(bytes(pkt))
    assert receiver.auth_failures == 1


def test_wrong_key_fails_auth():
    other = base64.b64encode(bytes(30)).decode()
    pkt = _protect(SRTPContext("AES_CM_128_HMAC_SHA1_80", other), _hdr(1), b"\x00" * 160, 0)
    with pytest.raises(SRTPError):
        SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE).unprotect(pkt)


def test_roc_rolls_over_at_seq_wrap():
    sender = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    receiver = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    payload = b"\x55" * 160
    assert receiver.unprotect(_protect(sender, _hdr(0xFFFE), payload, 0)) == _hdr(0xFFFE) + payload
    assert receiver.unprotect(_protect(sender, _hdr(0xFFFF), payload, 0)) == _hdr(0xFFFF) + payload
    # wrapped: sender's ROC is now 1; receiver must infer it
    assert receiver.unprotect(_protect(sender, _hdr(0x0000), payload, 1)) == _hdr(0x0000) + payload
    assert receiver.unprotect(_protect(sender, _hdr(0x0001), payload, 1)) == _hdr(0x0001) + payload
    # late packet from before the wrap still authenticates with ROC 0
    assert receiver.unprotect(_protect(sender, _hdr(0xFFFD), payload, 0)) == _hdr(0xFFFD) + payload


def test_bad_inputs():
    with pytest.raises(SRTPError):
        SRTPContext("AEAD_AES_128_GCM", INLINE)
    with pytest.raises(SRTPError):
        SRTPContext("AES_CM_128_HMAC_SHA1_80", base64.b64encode(b"short").decode())
    ctx = SRTPContext("AES_CM_128_HMAC_SHA1_80", INLINE)
    with pytest.raises(SRTPError):
        ctx.unprotect(b"\x80" * 8)


def test_choose_crypto_prefers_first_supported():
    offers = [{"tag": 1, "suite": "AEAD_AES_256_GCM", "key": "x"},
              {"tag": 2, "suite": "AES_CM_128_HMAC_SHA1_32", "key": "y"},
              {"tag": 3, "suite": "AES_CM_128_HMAC_SHA1_80", "key": "z"}]
    assert choose_crypto(offers)["tag"] == 2
    assert choose_crypto(offers[:1]) is None
