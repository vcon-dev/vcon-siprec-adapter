"""
SRTP (RFC 3711) unprotect for SDES-keyed (RFC 4568) receive-only streams.

The SRS never sends media, so this is decrypt + authenticate only. Supports
the four SDES suites SIPREC SRCs actually offer:

    AES_CM_128_HMAC_SHA1_80 / _32   (RFC 4568, 128-bit key)
    AES_256_CM_HMAC_SHA1_80 / _32   (RFC 6188, 256-bit key, same PRF)

`SRTPContext.unprotect(packet)` returns the plain RTP packet (header + payload)
or raises `SRTPError`. Feed the result straight into the existing depacketizer.

ponytail: no AES-GCM suites, no MKI, no replay window beyond ROC tracking.
Add GCM when an SRC offers AEAD_AES_128_GCM; add MKI if one ever shows up.
"""

import base64
import hmac
import struct
from hashlib import sha1
from typing import Dict, Optional, Tuple

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

SUITES: Dict[str, Tuple[int, int, int]] = {
    # suite -> (key_len, salt_len, auth_tag_len) in bytes
    "AES_CM_128_HMAC_SHA1_80": (16, 14, 10),
    "AES_CM_128_HMAC_SHA1_32": (16, 14, 4),
    "AES_256_CM_HMAC_SHA1_80": (32, 14, 10),
    "AES_256_CM_HMAC_SHA1_32": (32, 14, 4),
}

# RFC 3711 4.3.1 key derivation labels
_LABEL_RTP_ENC, _LABEL_RTP_AUTH, _LABEL_RTP_SALT = 0x00, 0x01, 0x02


class SRTPError(Exception):
    """Authentication failure or malformed SRTP packet."""


def _aes_cm_keystream(key: bytes, iv: bytes, n: int) -> bytes:
    """AES-CM (AES in counter mode, RFC 3711 4.1.1): n bytes of keystream.
    AES-128 or AES-256 by key length (RFC 6188 uses the same construction)."""
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    return enc.update(bytes(n)) + enc.finalize()


def _derive(master_key: bytes, master_salt: bytes, label: int, n: int) -> bytes:
    """RFC 3711 4.3.1 with key_derivation_rate = 0 (the SDES default)."""
    x = int.from_bytes(master_salt, "big") ^ (label << 48)
    iv = (x << 16).to_bytes(16, "big")
    return _aes_cm_keystream(master_key, iv, n)


class SRTPContext:
    """Per-stream receive context: derived session keys plus ROC per SSRC."""

    def __init__(self, suite: str, inline_key_b64: str):
        if suite not in SUITES:
            raise SRTPError(f"unsupported crypto suite {suite}")
        key_len, salt_len, self.tag_len = SUITES[suite]
        raw = base64.b64decode(inline_key_b64 + "=" * (-len(inline_key_b64) % 4))
        if len(raw) != key_len + salt_len:
            raise SRTPError(f"{suite} needs {key_len + salt_len} key bytes, got {len(raw)}")
        master_key, master_salt = raw[:key_len], raw[key_len:]
        self.suite = suite
        self._enc_key = _derive(master_key, master_salt, _LABEL_RTP_ENC, key_len)
        self._auth_key = _derive(master_key, master_salt, _LABEL_RTP_AUTH, 20)
        self._salt = _derive(master_key, master_salt, _LABEL_RTP_SALT, salt_len)
        # ssrc -> (roc, last_seq); RFC 3711 3.3.1 index estimation
        self._roc: Dict[int, Tuple[int, int]] = {}
        self.auth_failures = 0

    def _index(self, ssrc: int, seq: int) -> int:
        roc, last = self._roc.get(ssrc, (0, None))
        if last is None:
            v = roc
        elif last < 0x8000:
            v = roc - 1 if seq - last > 0x8000 else roc
        else:
            v = roc + 1 if last - seq > 0x8000 else roc
        v = max(v, 0)
        if last is None or (v, seq) > (roc, last):
            self._roc[ssrc] = (v, seq)
        return (v << 16) | seq

    def unprotect(self, packet: bytes) -> bytes:
        """Authenticate and decrypt one SRTP packet; return plain RTP."""
        if len(packet) < 12 + self.tag_len:
            raise SRTPError("packet too short")
        auth_portion, tag = packet[:-self.tag_len], packet[-self.tag_len:]
        b0 = auth_portion[0]
        if (b0 >> 6) != 2:
            raise SRTPError("not RTP v2")
        seq = struct.unpack(">H", auth_portion[2:4])[0]
        ssrc = struct.unpack(">I", auth_portion[8:12])[0]
        index = self._index(ssrc, seq)
        roc = index >> 16

        mac = hmac.new(self._auth_key,
                       auth_portion + struct.pack(">I", roc), sha1).digest()
        if not hmac.compare_digest(mac[:self.tag_len], tag):
            self.auth_failures += 1
            raise SRTPError("authentication failed")

        header_len = 12 + (b0 & 0x0F) * 4
        if (b0 >> 4) & 1:
            if len(auth_portion) < header_len + 4:
                raise SRTPError("truncated header extension")
            ext_words = struct.unpack(">H", auth_portion[header_len + 2:header_len + 4])[0]
            header_len += 4 + ext_words * 4
        if len(auth_portion) < header_len:
            raise SRTPError("truncated header")

        # RFC 3711 4.1.1: IV = (salt<<16) XOR (ssrc<<64) XOR (index<<16)
        iv = ((int.from_bytes(self._salt, "big") << 16)
              ^ (ssrc << 64) ^ (index << 16)).to_bytes(16, "big")
        ct = auth_portion[header_len:]
        ks = _aes_cm_keystream(self._enc_key, iv, len(ct))
        return auth_portion[:header_len] + bytes(a ^ b for a, b in zip(ct, ks))


def choose_crypto(offers) -> Optional[Dict]:
    """First offered a=crypto dict whose suite we support, else None."""
    return next((c for c in offers if c["suite"] in SUITES), None)
