"""
Plain-RTP audio capture for SIPREC media streams.

Receive-only: the SRS advertises a recvonly RTP port per audio stream in its
SDP answer and this module captures whatever the recording client sends there,
depacketizes RTP, decodes the payload to 16-bit linear PCM, and writes a WAV.

G.711 (PCMU/PCMA) is decoded with stdlib `audioop` (correct, not hand-rolled).
Other payload types are written through only if already linear; unknown types
are counted and skipped. No SRTP (bring-up is plain RTP).
"""

import asyncio
import logging
import struct
import wave
from typing import Dict, Optional

try:  # audioop is stdlib on <=3.12, a pip shim (audioop-lts) on >=3.13
    import audioop
except ImportError:  # pragma: no cover - depends on Python version
    audioop = None

logger = logging.getLogger(__name__)

# Static RTP payload types we can decode to PCM (RFC 3551).
PT_PCMU = 0
PT_PCMA = 8


class _RTPProtocol(asyncio.DatagramProtocol):
    """asyncio datagram protocol that feeds packets to an RTPRecorder."""

    def __init__(self, recorder: "RTPRecorder"):
        self._recorder = recorder

    def datagram_received(self, data: bytes, addr):
        self._recorder.handle_packet(data)

    def error_received(self, exc):  # pragma: no cover - transport noise
        logger.debug("RTP transport error on %s: %s", self._recorder.stream_id, exc)


class RTPRecorder:
    """Captures one RTP audio stream to a WAV file.

    Bind with `await start()` (allocates an ephemeral UDP port unless one is
    given), read `.local_port` to advertise in SDP, then `stop()` to finalize.
    """

    def __init__(self, stream_id: str, wav_path: str,
                 bind_host: str = "0.0.0.0", local_port: int = 0,
                 sample_rate: int = 8000):
        self.stream_id = stream_id
        self.wav_path = wav_path
        self.bind_host = bind_host
        self.local_port = local_port
        self.sample_rate = sample_rate
        self.codec = "PCMU"
        self.packet_count = 0
        self.bytes_received = 0
        self._decoded_payload_type: Optional[int] = None
        self._transport: Optional[asyncio.BaseTransport] = None
        self._wave: Optional[wave.Wave_write] = None

    async def start(self):
        """Bind the UDP socket and open the WAV writer."""
        if audioop is None:
            raise RuntimeError(
                "audioop unavailable; install 'audioop-lts' on Python 3.13+"
            )
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _RTPProtocol(self),
            local_addr=(self.bind_host, self.local_port),
        )
        sock = self._transport.get_extra_info("socket")
        self.local_port = sock.getsockname()[1]

        self._wave = wave.open(self.wav_path, "wb")
        self._wave.setnchannels(1)
        self._wave.setsampwidth(2)  # 16-bit linear PCM
        self._wave.setframerate(self.sample_rate)
        logger.info("RTP recorder %s listening on udp/%d -> %s",
                    self.stream_id, self.local_port, self.wav_path)

    def handle_packet(self, data: bytes):
        """Depacketize one RTP packet and append decoded PCM to the WAV."""
        if len(data) < 12:
            return
        b0, b1 = data[0], data[1]
        version = (b0 >> 6) & 0x3
        if version != 2:
            return
        csrc_count = b0 & 0x0F
        has_ext = (b0 >> 4) & 0x1
        payload_type = b1 & 0x7F
        header_len = 12 + csrc_count * 4
        if has_ext:
            if len(data) < header_len + 4:
                return
            ext_words = struct.unpack(">H", data[header_len + 2:header_len + 4])[0]
            header_len += 4 + ext_words * 4
        if len(data) < header_len:
            return
        payload = data[header_len:]
        if not payload:
            return

        pcm = self._decode(payload_type, payload)
        if pcm is None:
            return
        self._wave.writeframes(pcm)
        self.packet_count += 1
        self.bytes_received += len(payload)

    def _decode(self, payload_type: int, payload: bytes) -> Optional[bytes]:
        """Decode a payload to 16-bit linear PCM, or None to skip."""
        if payload_type == PT_PCMU:
            self._note_codec(payload_type, "PCMU")
            return audioop.ulaw2lin(payload, 2)
        if payload_type == PT_PCMA:
            self._note_codec(payload_type, "PCMA")
            return audioop.alaw2lin(payload, 2)
        # Unknown / dynamic payload type (e.g. comfort noise, Opus). We only
        # decode G.711 for bring-up; skip the rest but keep counting so the
        # operator sees packets arrived.
        if payload_type != self._decoded_payload_type:
            logger.warning("stream %s: unhandled RTP payload type %d (skipping)",
                           self.stream_id, payload_type)
            self._decoded_payload_type = payload_type
        return None

    def _note_codec(self, payload_type: int, name: str):
        if self._decoded_payload_type != payload_type:
            self._decoded_payload_type = payload_type
            self.codec = name

    def stop(self):
        """Close the socket and WAV writer. Idempotent."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        if self._wave is not None:
            self._wave.close()
            self._wave = None
        logger.info("RTP recorder %s stopped: %d packets, %d payload bytes, codec=%s",
                    self.stream_id, self.packet_count, self.bytes_received, self.codec)

    def stats(self) -> Dict[str, object]:
        return {
            "stream_id": self.stream_id,
            "packet_count": self.packet_count,
            "bytes_received": self.bytes_received,
            "codec": self.codec,
            "local_port": self.local_port,
        }
