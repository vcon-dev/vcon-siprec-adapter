"""Re-offer handling for complex SIPREC call flows (transfer, hold, barge).

A transfer is normally signalled as a re-INVITE (or UPDATE) on the existing
recording dialog, carrying updated SDP and updated rs-metadata. The SRS used to
answer that with a bare `200 OK`: no SDP, no Contact, `Content-Length: 0`. Since
the NetSapiens SRC derives the media destination and the stream correlation from
our SDP answer (see CON-704 and `test_sdp_labels.py`), an answer with no SDP at
all leaves it nothing to work from and no RTP is ever sourced for the new leg.

These drive the SRS over loopback as a SIPREC client, the same approach as
`test_siprec_capture.py`, and assert on what goes out on the wire.

Prep for the NetSapiens metadata 1.1 session, 2026-07-30 10:00 PT.
"""

import asyncio
import re
import socket
import uuid

import pytest

from siprec_srs.config import Config, ServerConfig
from siprec_srs.sip_server import SIPRECServer

GROUP = "b11bd6bad68bb9e3dd2e2d727d39526e"
# Participant ids in the shape metadata 1.1 uses: the leg's SIP Call-ID, not a
# system user id. David Wang, 2026-07-29.
P_ALICE = "20260729164412058133-0018491486ec5db64acd5aca455acfe8"
P_BOB = "20260729164413058137-0018491486ec5db64acd5aca455acfe9"
P_CAROL = "20260729164500058200-0018491486ec5db64acd5aca455acff0"


def _metadata(*participants, streams=(("1", P_ALICE), ("2", P_BOB)), ext=None):
    """rs-metadata with explicit stream/participant associations."""
    who = "".join(
        f'<participant participant_id="{pid}">'
        f'<nameID aor="sip:{name.lower()}@dwang.netsapiens.com">'
        f'<name>{name}</name></nameID></participant>'
        for pid, name in participants
    )
    stream_els = "".join(
        f'<stream stream_id="s-{label}" session_id="sess-1">'
        f'<label>{label}</label></stream>'
        for label, _ in streams
    )
    assocs = "".join(
        f'<participantstreamassoc participant_id="{pid}">'
        f'<send>s-{label}</send></participantstreamassoc>'
        for label, pid in streams
    )
    extension = (
        f'<netsapiensExtension xmlns="http://schema.netsapiens.com/'
        f'netsapiensSipRec" version="1.1">{ext}</netsapiensExtension>'
    ) if ext else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<recording xmlns="urn:ietf:params:xml:ns:recording:1">'
        '<datamode>complete</datamode>'
        f'<group group_id="{GROUP}"><associate-time>2026-07-30T17:00:00Z'
        '</associate-time></group>'
        f'<session session_id="sess-1"><group-ref>{GROUP}</group-ref></session>'
        + who + stream_els + assocs + extension +
        '</recording>'
    )


def _sdp(*labels):
    lines = [
        "v=0", "o=- 1 1 IN IP4 127.0.0.1", "s=siprec",
        "c=IN IP4 127.0.0.1", "t=0 0",
    ]
    for i, label in enumerate(labels):
        lines += [
            f"m=audio {40000 + i * 2} RTP/AVP 0",
            "a=rtpmap:0 PCMU/8000",
            "a=sendonly",
        ]
        if label:
            lines.append(f"a=label:{label}")
    return "\r\n".join(lines) + "\r\n"


def _request(method, dst_ip, client_port, call_id, cseq, sdp=None, meta=None):
    if sdp is None and meta is None:
        body, ctype = b"", None
    elif meta is None:
        body, ctype = sdp.encode(), "application/sdp"
    else:
        parts = []
        if sdp is not None:
            parts.append(f"Content-Type: application/sdp\r\n\r\n{sdp}")
        parts.append(
            f"Content-Type: application/rs-metadata+xml\r\n\r\n{meta}")
        body = ("".join(f"--bnd\r\n{p}\r\n" for p in parts)
                + "--bnd--\r\n").encode()
        ctype = "multipart/mixed;boundary=bnd"

    headers = [
        f"{method} sip:recorder@{dst_ip} SIP/2.0",
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};"
        f"branch=z9hG4bK{uuid.uuid4().hex[:8]};rport",
        "Max-Forwards: 70",
        "From: <sip:src@srs.example>;tag=srctag",
        f"To: <sip:recorder@{dst_ip}>",
        f"Call-ID: {call_id}",
        f"CSeq: {cseq} {method}",
        f"Contact: <sip:src@127.0.0.1:{client_port}>",
    ]
    if ctype:
        headers.append(f"Content-Type: {ctype}")
    headers.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(headers) + "\r\n\r\n").encode() + body


class Client:
    """Minimal loopback SIPREC client: send a request, read the final response."""

    def __init__(self, server, call_id=None):
        self.server = server
        self.port = server._udp_transports[0].get_extra_info("sockname")[1]
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.connect(("127.0.0.1", self.port))
        self.sock.setblocking(False)
        self.client_port = self.sock.getsockname()[1]
        self.call_id = call_id or f"test-{uuid.uuid4().hex}"
        self.cseq = 0

    async def send(self, method, sdp=None, meta=None):
        """Return the final (non-100) response text."""
        self.cseq += 1
        loop = asyncio.get_event_loop()
        await loop.sock_sendall(self.sock, _request(
            method, "127.0.0.1", self.client_port, self.call_id, self.cseq,
            sdp=sdp, meta=meta))
        for _ in range(4):
            data = await asyncio.wait_for(loop.sock_recv(self.sock, 65535),
                                          timeout=2)
            text = data.decode("utf-8", "replace")
            if not text.startswith("SIP/2.0 100"):
                return text
        raise AssertionError(f"no final response to {method}")

    def close(self):
        self.sock.close()


def answered_ports(response):
    return [int(p) for p in re.findall(r"m=audio (\d+)", response)]


def answered_labels(response):
    return re.findall(r"a=label:(\S+)", response)


async def _server():
    cfg = Config(server=ServerConfig(
        listen_address="127.0.0.1", sip_port_udp=0, sip_port_tcp=0,
        sip_port_tls=0, tls_cert=None, tls_key=None,
    ))
    server = SIPRECServer(cfg)
    await server.start()
    return server


@pytest.mark.asyncio
async def test_reinvite_adding_stream_is_answered_with_full_sdp():
    """Attended transfer shape: a third labelled stream and a third party."""
    server = await _server()
    cli = Client(server)
    try:
        first = await cli.send(
            "INVITE", sdp=_sdp("1", "2"),
            meta=_metadata((P_ALICE, "Alice"), (P_BOB, "Bob")))
        assert first.startswith("SIP/2.0 200")
        ports_before = answered_ports(first)
        assert len(ports_before) == 2

        second = await cli.send(
            "INVITE", sdp=_sdp("1", "2", "3"),
            meta=_metadata(
                (P_ALICE, "Alice"), (P_BOB, "Bob"), (P_CAROL, "Carol"),
                streams=(("1", P_ALICE), ("2", P_BOB), ("3", P_CAROL)),
                ext="<byAction>Xfer</byAction>"
                    "<xferFromCallID>consult-call-9</xferFromCallID>"))

        assert second.startswith("SIP/2.0 200")
        # The whole point: a re-INVITE gets an SDP answer, not a bare 200.
        assert "application/sdp" in second, "re-INVITE answered without SDP"
        assert "Contact:" in second
        ports_after = answered_ports(second)
        assert len(ports_after) == 3, f"expected 3 m-lines, got {ports_after}"
        assert answered_labels(second) == ["1", "2", "3"]
        # Existing streams keep their ports so media in flight is undisturbed.
        assert ports_after[:2] == ports_before

        session = server.sessions[cli.call_id]
        assert len(session.recorders) == 3
        assert [p["name"] for p in session.participants] == \
            ["Alice", "Bob", "Carol"]
        assert session.stream_labels == {"1": P_ALICE, "2": P_BOB, "3": P_CAROL}
        # Latest extension wins: it carries the transfer reference.
        assert session.vendor_extension.get("xferFromCallID") == "consult-call-9"
    finally:
        cli.close()
        await server.stop()


@pytest.mark.asyncio
async def test_reinvite_unchanged_sdp_is_idempotent():
    """Hold/resume shape. Same offer twice must not duplicate anything."""
    server = await _server()
    cli = Client(server)
    try:
        meta = _metadata((P_ALICE, "Alice"), (P_BOB, "Bob"))
        first = await cli.send("INVITE", sdp=_sdp("1", "2"), meta=meta)
        second = await cli.send("INVITE", sdp=_sdp("1", "2"), meta=meta)

        assert "application/sdp" in second
        assert answered_ports(second) == answered_ports(first)
        assert answered_labels(second) == ["1", "2"]

        session = server.sessions[cli.call_id]
        assert len(session.recorders) == 2, "re-offer double-bound recorders"
        assert len(session.participants) == 2, "re-offer duplicated participants"
        assert len(session.media_streams) == 2
    finally:
        cli.close()
        await server.stop()


@pytest.mark.asyncio
async def test_reinvite_without_sdp_absorbs_metadata_only():
    """A metadata-only re-INVITE still updates parties, and gets a 200.

    No SDP means nothing to answer, but a party added by the update must
    still land, and no recorder should be bound for a stream never offered.
    """
    server = await _server()
    cli = Client(server)
    try:
        await cli.send("INVITE", sdp=_sdp("1", "2"),
                       meta=_metadata((P_ALICE, "Alice"), (P_BOB, "Bob")))
        # Carol joins, announced in metadata with no new media offered.
        resp = await cli.send("INVITE", meta=_metadata(
            (P_ALICE, "Alice"), (P_BOB, "Bob"), (P_CAROL, "Carol")))
        assert resp.startswith("SIP/2.0 200")
        session = server.sessions[cli.call_id]
        assert [p["name"] for p in session.participants] == \
            ["Alice", "Bob", "Carol"], "metadata-only update was dropped"
        assert len(session.recorders) == 2, "bound a recorder with no offer"
    finally:
        cli.close()
        await server.stop()


@pytest.mark.asyncio
async def test_update_carrying_reoffer_is_answered_with_sdp():
    """Some SRCs use UPDATE rather than re-INVITE for the same re-offer."""
    server = await _server()
    cli = Client(server)
    try:
        first = await cli.send("INVITE", sdp=_sdp("1", "2"),
                               meta=_metadata((P_ALICE, "Alice"), (P_BOB, "Bob")))
        resp = await cli.send(
            "UPDATE", sdp=_sdp("1", "2", "3"),
            meta=_metadata(
                (P_ALICE, "Alice"), (P_BOB, "Bob"), (P_CAROL, "Carol"),
                streams=(("1", P_ALICE), ("2", P_BOB), ("3", P_CAROL))))

        assert resp.startswith("SIP/2.0 200")
        assert "application/sdp" in resp, "UPDATE re-offer answered without SDP"
        assert answered_ports(resp)[:2] == answered_ports(first)
        assert answered_labels(resp) == ["1", "2", "3"]
        assert len(server.sessions[cli.call_id].recorders) == 3
    finally:
        cli.close()
        await server.stop()


@pytest.mark.asyncio
async def test_second_dialog_same_group_is_an_independent_session():
    """Documents current behavior, it is not necessarily the desired one.

    A transfer that opens a *new* recording dialog sharing the rs-metadata
    `group_id` currently yields two unrelated sessions, so two unrelated
    vCons. We do not parse `group_id` at all. Whether these should be
    cross-referenced or assembled into one vCon is an open decision; this
    test exists so the baseline is explicit rather than assumed.
    """
    server = await _server()
    a = Client(server)
    b = Client(server)
    try:
        meta = _metadata((P_ALICE, "Alice"), (P_BOB, "Bob"))
        await a.send("INVITE", sdp=_sdp("1", "2"), meta=meta)
        await b.send("INVITE", sdp=_sdp("1", "2"), meta=meta)

        assert a.call_id != b.call_id
        assert len(server.sessions) == 2
        sa, sb = server.sessions[a.call_id], server.sessions[b.call_id]
        assert sa.session_id != sb.session_id
        # Same group in the metadata, no linkage anywhere in the session.
        assert answered_ports(  # distinct media, so both really are recording
            await a.send("INVITE", sdp=_sdp("1", "2"), meta=meta)
        ) != answered_ports(
            await b.send("INVITE", sdp=_sdp("1", "2"), meta=meta)
        )
    finally:
        a.close()
        b.close()
        await server.stop()
