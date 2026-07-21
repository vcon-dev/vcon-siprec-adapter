"""
End-to-end SIPREC capture test with no pjsip and no network target.

Acts as a SIPREC recording client on loopback: sends an INVITE (multipart
SDP + rs-metadata), streams a few PCMU RTP packets to the ports the SRS
answers with, sends BYE, then asserts the SRS produced a session that the
VConConverter turns into a spec-shaped vCon (parties from rs-metadata, a
recording dialog with captured audio).
"""

import asyncio
import re
import socket
import struct
import uuid

import pytest

from siprec_srs.config import Config, ServerConfig
from siprec_srs.sip_server import SIPRECServer
from siprec_srs.vcon_converter import VConConverter

RS_METADATA = """<?xml version="1.0"?>
<recording xmlns="urn:ietf:params:xml:ns:recording:1">
  <participant participant_id="1"><nameID aor="sip:alice@example.com"><name>Alice</name></nameID></participant>
  <participant participant_id="2"><nameID aor="tel:+15551230000"><name>Bob</name></nameID></participant>
</recording>"""


def _invite(dst_ip, dst_port, client_port, call_id):
    sdp = (
        "v=0\r\n"
        "o=- 1 1 IN IP4 127.0.0.1\r\n"
        "s=siprec\r\n"
        "c=IN IP4 127.0.0.1\r\n"
        "t=0 0\r\n"
        "m=audio 40000 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendonly\r\n"
        "m=audio 40002 RTP/AVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        "a=sendonly\r\n"
    )
    body = (
        "--bnd\r\n"
        "Content-Type: application/sdp\r\n\r\n"
        f"{sdp}\r\n"
        "--bnd\r\n"
        "Content-Type: application/rs-metadata+xml\r\n\r\n"
        f"{RS_METADATA}\r\n"
        "--bnd--\r\n"
    ).encode()
    headers = (
        f"INVITE sip:recorder@{dst_ip} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch=z9hG4bK{uuid.uuid4().hex[:8]};rport\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:src@srs.example>;tag=srctag\r\n"
        f"To: <sip:recorder@{dst_ip}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 1 INVITE\r\n"
        f"Contact: <sip:src@127.0.0.1:{client_port}>\r\n"
        "Content-Type: multipart/mixed;boundary=bnd\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode()
    return headers + body


def _bye(dst_ip, client_port, call_id):
    return (
        f"BYE sip:recorder@{dst_ip} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch=z9hG4bK{uuid.uuid4().hex[:8]}\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:src@srs.example>;tag=srctag\r\n"
        f"To: <sip:recorder@{dst_ip}>\r\n"
        f"Call-ID: {call_id}\r\n"
        "CSeq: 2 BYE\r\n"
        "Content-Length: 0\r\n\r\n"
    ).encode()


def _rtp_packet(seq, ts, payload):
    header = struct.pack(">BBHII", 0x80, 0x00, seq, ts, 0x1234ABCD)  # V=2, PT=0 (PCMU)
    return header + payload


@pytest.mark.asyncio
async def test_siprec_invite_rtp_bye_produces_vcon(tmp_path):
    completed = asyncio.get_event_loop().create_future()

    async def on_complete(session):
        if not completed.done():
            completed.set_result(session)

    cfg = Config(server=ServerConfig(
        listen_address="127.0.0.1", sip_port_udp=0, sip_port_tcp=0,
        sip_port_tls=0, tls_cert=None, tls_key=None,
    ))
    server = SIPRECServer(cfg)
    server.set_session_complete_callback(on_complete)
    await server.start()
    sip_port = server._udp_transports[0].get_extra_info("sockname")[1]

    # Client socket connected to the SRS SIP port.
    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli.bind(("127.0.0.1", 0))
    cli.connect(("127.0.0.1", sip_port))
    cli.setblocking(False)
    client_port = cli.getsockname()[1]
    loop = asyncio.get_event_loop()
    call_id = f"test-{uuid.uuid4().hex}"

    # 1) INVITE -> collect 200 OK (skip 100 Trying), parse answered RTP ports.
    await loop.sock_sendall(cli, _invite("127.0.0.1", sip_port, client_port, call_id))
    rtp_ports = []
    for _ in range(4):
        data = await asyncio.wait_for(loop.sock_recv(cli, 65535), timeout=2)
        text = data.decode("utf-8", "replace")
        if text.startswith("SIP/2.0 200"):
            rtp_ports = [int(p) for p in re.findall(r"m=audio (\d+)", text)]
            break
    assert rtp_ports, "no 200 OK with SDP answer received"
    assert len(rtp_ports) == 2, f"expected 2 answered streams, got {rtp_ports}"
    # Advertised media ports must fall in the configured firewall range,
    # else RTP is dropped upstream (the David interop bug, 2026-07-20).
    assert all(cfg.rtp.port_range_start <= p <= cfg.rtp.port_range_end
               for p in rtp_ports), f"RTP ports outside firewall range: {rtp_ports}"

    # 2) Stream PCMU RTP to each answered port.
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b"\xff" * 160  # 20ms of PCMU silence
    for port in rtp_ports:
        for seq in range(10):
            rtp.sendto(_rtp_packet(seq, seq * 160, payload), ("127.0.0.1", port))
    await asyncio.sleep(0.2)  # let the loop drain the RTP datagrams

    # 3) BYE -> triggers session completion.
    await loop.sock_sendall(cli, _bye("127.0.0.1", client_port, call_id))

    session = await asyncio.wait_for(completed, timeout=3)
    await server.stop()
    cli.close()
    rtp.close()

    # ---- assertions on the captured session + emitted vCon ----
    assert len(session.participants) == 2
    audio = session.get_audio_files()
    assert len(audio) == 2, f"expected 2 captured streams, got {audio}"

    vcon = VConConverter(
        lawful_basis_config=cfg.lawful_basis, media_config=cfg.media,
    ).convert_session_to_vcon({
        "session_id": session.session_id,
        "call_id": session.call_id,
        "recording_session_id": session.recording_session_id,
        "participants": session.participants,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "media_streams": session.media_streams,
        "remote_uri": session.remote_uri,
        "local_uri": session.local_uri,
    }, session)

    assert vcon is not None
    d = vcon.vcon_dict
    assert d["vcon"] == "0.4.0"
    names = {p.get("name") for p in d["parties"]}
    assert {"Alice", "Bob"} <= names
    tels = {p.get("tel") for p in d["parties"] if p.get("tel")}
    assert "+15551230000" in tels
    recordings = [dlg for dlg in d["dialog"] if dlg.get("type") == "recording"]
    assert len(recordings) == 2
    assert all(dlg.get("body") for dlg in recordings)  # inline base64url audio


def test_contact_reflects_transport_and_port():
    """In-dialog BYE/re-INVITE must route back to us, not default 5060/UDP."""
    from siprec_srs.sip_server import SIPMessage

    cfg = Config(server=ServerConfig(sip_port_tls=5061, sip_port_tcp=5062,
                                     sip_port_udp=5060))
    srv = SIPRECServer(cfg)

    def contact(via_transport, port_hdr):
        msg = SIPMessage(f"INVITE sip:recorder@host SIP/2.0",
                         [("Via", f"SIP/2.0/{via_transport} 1.2.3.4:{port_hdr}")], b"")
        return srv._contact(msg, "9.9.9.9")

    assert contact("TLS", 5061) == "<sip:recorder@9.9.9.9:5061;transport=tls>"
    assert contact("TCP", 5062) == "<sip:recorder@9.9.9.9:5062;transport=tcp>"
    assert contact("UDP", 5060) == "<sip:recorder@9.9.9.9:5060>"
