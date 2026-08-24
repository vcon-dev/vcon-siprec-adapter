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
import errno
import logging
import struct
import wave
from typing import Dict, Optional, Tuple

try:  # audioop is stdlib on <=3.12, a pip shim (audioop-lts) on >=3.13
    import audioop
except ImportError:  # pragma: no cover - depends on Python version
    audioop = None

logger = logging.getLogger(__name__)

# Static RTP payload types we can decode to PCM (RFC 3551).
PT_PCMU = 0
PT_PCMA = 8

# Sequence numbers remembered per SSRC for duplicate detection. Two seconds of
# 20ms audio: long enough that a duplicate is not missed, short enough that a
# legitimate 16-bit wrap-around never collides.
_SEQ_WINDOW = 100


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
                 sample_rate: int = 8000,
                 port_range: Optional[Tuple[int, int]] = None):
        self.stream_id = stream_id
        self.wav_path = wav_path
        self.bind_host = bind_host
        self.local_port = local_port
        self.sample_rate = sample_rate
        # When set and no explicit local_port is given, bind within this
        # inclusive range so advertised media ports match the firewall.
        self.port_range = port_range
        self.codec = "PCMU"
        self.packet_count = 0
        self.bytes_received = 0
        self._decoded_payload_type: Optional[int] = None
        # Per-SSRC packet counts. More than one entry means two sources sent to
        # this port, which the WAV cannot represent: they interleave and the
        # audio sounds chopped. See _note_ssrc.
        self.ssrc_counts: Dict[int, int] = {}
        # Duplicate detection. The 2026-08-20 four-party barge delivered every
        # label-3 packet twice under ONE SSRC, so ssrc_counts saw a single
        # source and stayed quiet while the WAV came out at double length. Keep
        # a short window of recently seen sequence numbers per SSRC: a repeat is
        # the same packet arriving again, and writing it again stretches the
        # timeline. See _note_seq.
        self.duplicate_counts: Dict[int, int] = {}
        self._recent_seqs: Dict[int, Dict[int, None]] = {}
        self._transport: Optional[asyncio.BaseTransport] = None
        self._wave: Optional[wave.Wave_write] = None

    async def start(self):
        """Bind the UDP socket and open the WAV writer."""
        if audioop is None:
            raise RuntimeError(
                "audioop unavailable; install 'audioop-lts' on Python 3.13+"
            )
        loop = asyncio.get_running_loop()
        if self.port_range and not self.local_port:
            self._transport = await self._bind_in_range(loop)
        else:
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

    async def _bind_in_range(self, loop):
        """Bind the first free UDP port in self.port_range. Callers await
        start() sequentially, so an already-bound port simply fails here and
        we move to the next. RTP is even-port by convention (RFC 3550)."""
        lo, hi = self.port_range
        start = lo if lo % 2 == 0 else lo + 1
        for port in range(start, hi + 1, 2):
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _RTPProtocol(self),
                    local_addr=(self.bind_host, port),
                )
                return transport
            except OSError as e:
                if e.errno in (errno.EADDRINUSE, errno.EACCES):
                    continue
                raise
        raise RuntimeError(
            f"no free RTP port in range {lo}-{hi} for {self.stream_id}"
        )

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
        seq = struct.unpack(">H", data[2:4])[0]
        ssrc = struct.unpack(">I", data[8:12])[0]
        self._note_ssrc(ssrc)
        if self._note_seq(ssrc, seq):
            return  # exact duplicate; writing it again would stretch the WAV
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

    def _note_ssrc(self, ssrc: int):
        """Count packets per synchronization source and warn on a second one.

        One recorder is one WAV is one audio timeline, so it can only represent
        one source. A second SSRC on the same port means the SRC put two legs
        on one `m=` line (or sent an extra stream to a port we advertised for
        another): both get written, interleaved, and the result plays back
        chopped at roughly double rate. Warn once per new SSRC and keep the
        counts so `stats()` can prove it after the fact.
        """
        if ssrc not in self.ssrc_counts:
            if self.ssrc_counts:
                logger.warning(
                    "stream %s: second SSRC 0x%08x on udp/%d (already had %s); "
                    "both sources are being written to one WAV and will interleave",
                    self.stream_id, ssrc, self.local_port,
                    ", ".join(f"0x{s:08x}" for s in self.ssrc_counts))
            self.ssrc_counts[ssrc] = 0
        self.ssrc_counts[ssrc] += 1

    def _note_seq(self, ssrc: int, seq: int) -> bool:
        """Return True if this (ssrc, seq) was already seen recently.

        A sequence number repeating inside the window means the same packet
        reached us twice, whether the SRC sent it twice or something between us
        forked the media. Either way its audio is already in the WAV, so the
        copy is dropped and counted. The window is short and per-SSRC so normal
        reordering does not read as duplication and a 16-bit wrap is harmless.
        """
        seen = self._recent_seqs.setdefault(ssrc, {})
        if seq in seen:
            if ssrc not in self.duplicate_counts:
                logger.warning(
                    "stream %s: duplicate RTP seq %d from 0x%08x on udp/%d; "
                    "dropping the copy (the sender is delivering this leg twice)",
                    self.stream_id, seq, ssrc, self.local_port)
            self.duplicate_counts[ssrc] = self.duplicate_counts.get(ssrc, 0) + 1
            return True
        seen[seq] = None
        if len(seen) > _SEQ_WINDOW:
            seen.pop(next(iter(seen)))
        return False

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
        logger.info("RTP recorder %s stopped: %d packets, %d payload bytes, codec=%s, "
                    "ssrcs=%s, duplicates_dropped=%d",
                    self.stream_id, self.packet_count, self.bytes_received,
                    self.codec, self.ssrc_summary(), self.duplicate_count)

    def ssrc_summary(self) -> str:
        """Human-readable per-SSRC packet counts, for logs."""
        if not self.ssrc_counts:
            return "none"
        return " ".join(f"0x{s:08x}={n}" for s, n in self.ssrc_counts.items())

    def stats(self) -> Dict[str, object]:
        return {
            "stream_id": self.stream_id,
            "packet_count": self.packet_count,
            "bytes_received": self.bytes_received,
            "codec": self.codec,
            "local_port": self.local_port,
            "ssrc_counts": {f"0x{s:08x}": n for s, n in self.ssrc_counts.items()},
            "mixed_ssrc": len(self.ssrc_counts) > 1,
            "duplicate_counts": {f"0x{s:08x}": n
                                 for s, n in self.duplicate_counts.items()},
            "duplicates_dropped": self.duplicate_count,
        }

    @property
    def duplicate_count(self) -> int:
        """Packets dropped as exact re-deliveries, across all sources."""
        return sum(self.duplicate_counts.values())
