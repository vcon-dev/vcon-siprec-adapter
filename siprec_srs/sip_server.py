"""
SIPREC Session Recording Server (SRS) as a minimal, receive-only SIP UAS.

No pjsua2. SIPREC is a one-way recording delivery: the recording client (SRC)
sends an INVITE carrying a multipart body (SDP offer + rs-metadata), streams
RTP to the ports we advertise, and ends with BYE. We only need to be a UAS
that answers, captures RTP to WAV, and reports the finished session. Signaling
runs over UDP / TCP / TLS; media is plain RTP (no SRTP for bring-up).

The finished session exposes `get_audio_files()` so it plugs straight into the
existing VConConverter without changes.
"""

import asyncio
import logging
import os
import ssl
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional, Tuple

from .config import Config
from .siprec_parser import SIPRECParser, split_multipart
from .rtp_recorder import RTPRecorder

logger = logging.getLogger(__name__)

SessionCallback = Callable[["SIPRECSession"], Awaitable[None]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SIPMessage:
    """A parsed SIP message (request or response)."""

    def __init__(self, start_line: str, headers: List[Tuple[str, str]], body: bytes):
        self.start_line = start_line
        self.headers = headers  # ordered, may repeat (e.g. Via)
        self.body = body
        parts = start_line.split(" ", 2)
        self.is_request = not start_line.startswith("SIP/2.0")
        self.method = parts[0].upper() if self.is_request else ""
        self.ruri = parts[1] if self.is_request and len(parts) > 1 else ""

    def get(self, name: str, default: str = "") -> str:
        name = name.lower()
        for k, v in self.headers:
            if k.lower() == name:
                return v
        return default

    def get_all(self, name: str) -> List[str]:
        name = name.lower()
        return [v for k, v in self.headers if k.lower() == name]

    @classmethod
    def parse(cls, data: bytes) -> Optional["SIPMessage"]:
        if b"\r\n\r\n" in data:
            head, body = data.split(b"\r\n\r\n", 1)
        elif b"\n\n" in data:
            head, body = data.split(b"\n\n", 1)
        else:
            head, body = data, b""
        lines = head.decode("utf-8", "replace").replace("\r\n", "\n").split("\n")
        if not lines or not lines[0].strip():
            return None
        headers: List[Tuple[str, str]] = []
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                headers.append((k.strip(), v.strip()))
        return cls(lines[0].strip(), headers, body)

    def content_length(self) -> int:
        for name in ("content-length", "l"):
            v = self.get(name)
            if v.isdigit():
                return int(v)
        return 0


class SIPRECSession:
    """A recording session, built from an INVITE and finalized on BYE."""

    def __init__(self, call_id: str):
        self.session_id = str(uuid.uuid4())
        self.call_id = call_id
        self.recording_session_id = ""
        self.participants: List[Dict] = []
        self.vendor_extension: Dict = {}
        self.stream_labels: Dict[str, str] = {}  # sdp label -> participant_id
        self.rs_keys: Dict = {}  # RFC 7865 group/session correlation keys
        self.media_streams: List[Dict] = []
        self.remote_uri = ""
        self.local_uri = ""
        self.start_time = _now()
        self.end_time: Optional[str] = None
        self.recorders: Dict[str, RTPRecorder] = {}
        # Raw wire input/output, retained for the interop-debug attachment:
        # what the SRC offered, what we answered, and the raw rs-metadata.
        self.raw_offer_sdp = ""
        self.raw_answer_sdp = ""
        self.raw_rs_metadata = ""

    def get_audio_files(self) -> Dict[str, str]:
        """{stream_id: wav_path} for streams that captured >0 packets."""
        return {
            sid: rec.wav_path
            for sid, rec in self.recorders.items()
            if rec.packet_count > 0
        }

    def stop(self):
        self.end_time = self.end_time or _now()
        for rec in self.recorders.values():
            rec.stop()


class SIPRECServer:
    """Receive-only SIP UAS that records SIPREC media to WAV per stream."""

    def __init__(self, config: Config):
        self.config = config
        self.parser = SIPRECParser()
        self.sessions: Dict[str, SIPRECSession] = {}
        self._on_complete: Optional[SessionCallback] = None
        self._servers: List[asyncio.AbstractServer] = []
        self._udp_transports: List[asyncio.BaseTransport] = []
        # transaction dedup: {(call_id, cseq): response_bytes}
        self._responses: Dict[Tuple[str, str], bytes] = {}
        self._public_ip = os.getenv("SIPREC_PUBLIC_IP") or getattr(
            config.server, "public_ip", None
        )

    def set_session_complete_callback(self, cb: SessionCallback):
        self._on_complete = cb

    # Back-compat alias for existing callers.
    set_session_callback = set_session_complete_callback

    async def start(self):
        srv = self.config.server
        loop = asyncio.get_running_loop()

        # UDP
        udp_tr, _ = await loop.create_datagram_endpoint(
            lambda: _UDPProtocol(self),
            local_addr=(srv.listen_address, srv.sip_port_udp),
        )
        self._udp_transports.append(udp_tr)

        # TCP
        self._servers.append(await asyncio.start_server(
            self._tcp_client, srv.listen_address, srv.sip_port_tcp))

        # TLS
        if srv.tls_cert and srv.tls_key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(srv.tls_cert, srv.tls_key)
            self._servers.append(await asyncio.start_server(
                self._tcp_client, srv.listen_address, srv.sip_port_tls, ssl=ctx))

        logger.info("SIPREC server started successfully")
        logger.info("Listening on UDP:%d, TCP:%d, TLS:%s",
                    srv.sip_port_udp, srv.sip_port_tcp,
                    srv.sip_port_tls if (srv.tls_cert and srv.tls_key) else "off")

    async def stop(self):
        for tr in self._udp_transports:
            tr.close()
        for s in self._servers:
            s.close()
        for session in list(self.sessions.values()):
            session.stop()
        logger.info("SIPREC server stopped")

    # ---- transports ----------------------------------------------------

    async def _tcp_client(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        local_ip = writer.get_extra_info("sockname")[0]
        try:
            while not reader.at_eof():
                msg = await self._read_stream_message(reader)
                if msg is None:
                    break

                def reply(data: bytes):
                    writer.write(data)

                await self._dispatch(msg, local_ip, reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()

    async def _read_stream_message(self, reader) -> Optional[SIPMessage]:
        """Read one SIP message from a stream, framed by Content-Length."""
        header = await reader.readuntil(b"\r\n\r\n") if not reader.at_eof() else b""
        if not header:
            return None
        msg = SIPMessage.parse(header)
        if msg is None:
            return None
        clen = msg.content_length()
        body = await reader.readexactly(clen) if clen else b""
        return SIPMessage(msg.start_line, msg.headers, body)

    # ---- dispatch ------------------------------------------------------

    async def _dispatch(self, msg: SIPMessage, local_ip: str,
                        reply: Callable[[bytes], None]):
        if not msg.is_request:
            return
        method = msg.method
        advertise_ip = self._public_ip or local_ip
        try:
            if method == "INVITE":
                await self._on_invite(msg, advertise_ip, reply)
            elif method == "BYE":
                await self._on_bye(msg, reply)
            elif method == "ACK":
                pass  # dialog confirmed; media flows
            elif method == "UPDATE" and msg.get("Call-ID") in self.sessions:
                # UPDATE can carry a re-offer just as a re-INVITE does, and it
                # needs the same SDP answer.
                await self._on_reoffer(
                    msg, advertise_ip, reply,
                    self.sessions[msg.get("Call-ID")],
                    (msg.get("Call-ID"), msg.get("CSeq")))
            elif method in ("OPTIONS", "INFO", "UPDATE"):
                reply(self._response(msg, 200, "OK"))
            else:
                reply(self._response(msg, 405, "Method Not Allowed"))
        except Exception as e:
            logger.error("Error handling %s: %s", method, e, exc_info=True)
            reply(self._response(msg, 500, "Server Internal Error"))

    async def _on_invite(self, msg: SIPMessage, advertise_ip: str,
                         reply: Callable[[bytes], None]):
        call_id = msg.get("Call-ID")
        cseq = msg.get("CSeq")
        key = (call_id, cseq)

        # Retransmitted INVITE: resend the cached final response.
        if key in self._responses:
            reply(self._responses[key])
            return
        # Re-INVITE on an existing dialog: a re-offer, not a no-op.
        if call_id in self.sessions:
            await self._on_reoffer(msg, advertise_ip, reply,
                                   self.sessions[call_id], key)
            return

        reply(self._response(msg, 100, "Trying"))

        sdp, rs_text = self._split_body(msg)
        streams = self.parser.parse_sdp(sdp) if sdp else []
        session = SIPRECSession(call_id)
        session.raw_offer_sdp = sdp or ""
        session.raw_rs_metadata = rs_text or ""
        session.remote_uri = _uri(msg.get("From"))
        session.local_uri = _uri(msg.get("To"))
        session.recording_session_id = call_id  # SRC dialog id; refine if metadata carries one

        if rs_text:
            self._refresh_metadata(session, rs_text)

        answer_media = [await self._bind_stream(session, s) for s in streams]
        self.sessions[call_id] = session

        sdp_answer = self._build_sdp_answer(advertise_ip, answer_media)
        session.raw_answer_sdp = sdp_answer
        resp = self._response(msg, 200, "OK", body=sdp_answer.encode(),
                              content_type="application/sdp",
                              add_contact_ip=advertise_ip)
        self._responses[key] = resp
        reply(resp)
        logger.info("Answered SIPREC INVITE call_id=%s: %d stream(s), %d participant(s)",
                    call_id, len(streams), len(session.participants))

    async def _on_reoffer(self, msg: SIPMessage, advertise_ip: str,
                          reply: Callable[[bytes], None],
                          session: "SIPRECSession",
                          key: Tuple[str, str]):
        """Answer a re-INVITE/UPDATE re-offer with a full SDP answer.

        A transfer is normally signalled this way: the SRC re-offers on the
        existing recording dialog with updated SDP and updated rs-metadata.
        Answering with a bare `200 OK` (no SDP) leaves the SRC no media
        address and no `a=label` to correlate against, which is the CON-704
        failure with less to work from, so it never sources RTP for the new
        leg.

        Existing streams keep the port they were already advertised on, so
        media in flight is not disturbed. A label we have not seen gets a
        new recorder.
        """
        sdp, rs_text = self._split_body(msg)
        if not sdp:
            # No offer to answer (e.g. a keepalive re-INVITE). Metadata-only
            # updates are still worth absorbing.
            if rs_text:
                self._refresh_metadata(session, rs_text)
            reply(self._response(msg, 200, "OK", add_contact_ip=advertise_ip))
            return

        if rs_text:
            self._refresh_metadata(session, rs_text)

        by_label = {s["label"]: s for s in session.media_streams if s.get("label")}
        answer_media: List[Tuple[Dict, int]] = []
        added = 0
        for stream in self.parser.parse_sdp(sdp):
            known = by_label.get(stream.get("label"))
            if known is None and not stream.get("label"):
                # Unlabelled: fall back to position among existing streams.
                known = next(
                    (s for s in session.media_streams
                     if s["index"] == stream["index"] and not s.get("label")),
                    None)
            if known is not None:
                answer_media.append((known, known["local_rtp_port"]))
            else:
                answer_media.append(await self._bind_stream(session, stream))
                added += 1

        sdp_answer = self._build_sdp_answer(advertise_ip, answer_media)
        resp = self._response(msg, 200, "OK", body=sdp_answer.encode(),
                              content_type="application/sdp",
                              add_contact_ip=advertise_ip)
        self._responses[key] = resp
        reply(resp)
        logger.info(
            "Answered %s re-offer call_id=%s: %d stream(s) in answer, %d new, "
            "%d participant(s)",
            msg.method, session.call_id, len(answer_media), added,
            len(session.participants))

    def _split_body(self, msg: SIPMessage) -> Tuple[str, str]:
        """(sdp_text, rs_metadata_text) from a SIPREC request body."""
        parts = split_multipart(msg.body, msg.get("Content-Type"))
        sdp = (parts.get("sdp") or b"").decode("utf-8", "replace")
        rs_meta = parts.get("rs-metadata+xml") or parts.get("rs-metadata") or b""
        return sdp, rs_meta.decode("utf-8", "replace") if rs_meta else ""

    def _refresh_metadata(self, session: "SIPRECSession", rs_text: str):
        """Absorb rs-metadata, preserving party indices already assigned.

        Participants are appended, never reordered or dropped: a party index
        is referenced by the dialogs built at conversion time, so renumbering
        mid-session would silently re-attribute audio. A participant that
        leaves is therefore still listed; representing disassociation is
        separate work.
        """
        participants = self.parser.parse_rs_metadata(rs_text)
        seen = {p.get("id") for p in session.participants}
        for p in participants:
            if p.get("id") not in seen:
                session.participants.append(p)
                seen.add(p.get("id"))

        session.stream_labels.update(self.parser.parse_stream_labels(rs_text))
        session.rs_keys.update(self.parser.parse_session_keys(rs_text))

        # The SRC's own session id is the correlation key it will quote back to
        # us; ours is just the dialog Call-ID. Prefer theirs when offered.
        if session.rs_keys.get("session_id"):
            session.recording_session_id = session.rs_keys["session_id"]

        # Latest non-empty wins: on a transfer the newer extension is the one
        # carrying the xfer-from reference.
        ext = self.parser.parse_vendor_extension(rs_text)
        if ext:
            session.vendor_extension = ext

    async def _bind_stream(self, session: "SIPRECSession",
                           stream: Dict) -> Tuple[Dict, int]:
        """Bind an RTP recorder for one offered stream, return (stream, port).

        `stream_id` is numbered by position in `media_streams` rather than by
        the offer's m-line index, so streams added by a later re-offer cannot
        collide with the ones bound at INVITE time.
        """
        stream_id = f"{session.session_id}_stream_{len(session.media_streams)}"
        wav_path = tempfile.mktemp(prefix=f"{stream_id}_", suffix=".wav")
        rec = RTPRecorder(stream_id, wav_path,
                          sample_rate=self.config.rtp.sample_rate,
                          port_range=(self.config.rtp.port_range_start,
                                      self.config.rtp.port_range_end))
        await rec.start()
        session.recorders[stream_id] = rec
        stream["local_rtp_port"] = rec.local_port
        stream["stream_id"] = stream_id
        session.media_streams.append(stream)
        return stream, rec.local_port

    async def _on_bye(self, msg: SIPMessage, reply: Callable[[bytes], None]):
        call_id = msg.get("Call-ID")
        reply(self._response(msg, 200, "OK"))
        session = self.sessions.pop(call_id, None)
        if session is None:
            return
        session.stop()
        # drop cached INVITE responses for this dialog
        self._responses = {k: v for k, v in self._responses.items() if k[0] != call_id}
        logger.info("Session %s ended (call_id=%s); %d captured stream(s)",
                    session.session_id, call_id, len(session.get_audio_files()))
        if self._on_complete is not None:
            await self._on_complete(session)

    # ---- SIP/SDP construction -----------------------------------------

    def _response(self, req: SIPMessage, code: int, reason: str,
                  body: bytes = b"", content_type: Optional[str] = None,
                  add_contact_ip: Optional[str] = None) -> bytes:
        lines = [f"SIP/2.0 {code} {reason}"]
        # Echo Via (all), Record-Route, From, Call-ID, CSeq verbatim.
        for via in req.get_all("Via"):
            lines.append(f"Via: {via}")
        for rr in req.get_all("Record-Route"):
            lines.append(f"Record-Route: {rr}")
        lines.append(f"From: {req.get('From')}")
        to = req.get("To")
        if code >= 200 and ";tag=" not in to:
            to = f"{to};tag={uuid.uuid4().hex[:12]}"
        lines.append(f"To: {to}")
        lines.append(f"Call-ID: {req.get('Call-ID')}")
        lines.append(f"CSeq: {req.get('CSeq')}")
        if add_contact_ip:
            lines.append(f"Contact: {self._contact(req, add_contact_ip)}")
        if content_type:
            lines.append(f"Content-Type: {content_type}")
        lines.append(f"Content-Length: {len(body)}")
        lines.append("")
        return ("\r\n".join(lines) + "\r\n").encode() + body

    def _contact(self, req: SIPMessage, ip: str) -> str:
        """Contact URI reflecting the transport the request arrived on and
        our matching listening port, so in-dialog BYE/re-INVITE route back
        here rather than defaulting to 5060/UDP."""
        via = req.get("Via")
        transport = "udp"
        if via.startswith("SIP/2.0/"):
            transport = via[8:].split(" ", 1)[0].strip().lower()
        srv = self.config.server
        port = {"tls": srv.sip_port_tls, "tcp": srv.sip_port_tcp}.get(
            transport, srv.sip_port_udp)
        if transport in ("tls", "tcp"):
            return f"<sip:recorder@{ip}:{port};transport={transport}>"
        return f"<sip:recorder@{ip}:{port}>"

    def _build_sdp_answer(self, ip: str,
                          media: List[Tuple[Dict, int]]) -> str:
        sid = uuid.uuid4().int % 10_000_000
        lines = [
            "v=0",
            f"o=- {sid} {sid} IN IP4 {ip}",
            "s=SIPREC-SRS",
            f"c=IN IP4 {ip}",
            "t=0 0",
        ]
        for stream, port in media:
            # Answer with the first payload type we can record (G.711), else
            # echo the offer's first PT so the m-line stays valid.
            pt = next((p for p in stream["payload_types"] if p in (0, 8)),
                      stream["payload_types"][0] if stream["payload_types"] else 0)
            name = {0: "PCMU", 8: "PCMA"}.get(pt, "PCMU")
            lines.append(f"m=audio {port} RTP/AVP {pt}")
            lines.append(f"a=rtpmap:{pt} {name}/8000")
            lines.append("a=recvonly")
            if stream.get("label"):
                lines.append(f"a=label:{stream['label']}")
        return "\r\n".join(lines) + "\r\n"


class _UDPProtocol(asyncio.DatagramProtocol):
    def __init__(self, server: SIPRECServer):
        self._server = server
        self._transport: Optional[asyncio.DatagramTransport] = None

    def connection_made(self, transport):
        self._transport = transport

    def datagram_received(self, data: bytes, addr):
        msg = SIPMessage.parse(data)
        if msg is None:
            return
        local_ip = self._transport.get_extra_info("sockname")[0]

        def reply(out: bytes):
            self._transport.sendto(out, addr)

        asyncio.create_task(self._server._dispatch(msg, local_ip, reply))


def _uri(header_value: str) -> str:
    """Extract the SIP URI from a From/To header value."""
    if "<" in header_value and ">" in header_value:
        return header_value[header_value.index("<") + 1:header_value.index(">")]
    return header_value.split(";", 1)[0].strip()
