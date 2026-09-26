"""
CON-1091: validate a real converter-path vCon against the vendored WG JSON
Schema (`tests/schema/vcon_json_schema.json`, see `tests/schema/SOURCE.md`),
plus the non-negotiables the schema alone doesn't pin down (no legacy
`mimetype`, attachment/dialog field shapes, body shapes by encoding, no
empty `meta`/`metadata`).

Reuses the same loopback SIPREC capture harness as
tests/test_siprec_capture.py (SIP INVITE with multipart SDP + rs-metadata
XML, RTP audio, BYE) so the vCon under test is built through the real
`SIPRECServer` -> `VConConverter` path, with a lawful_basis configured,
rather than from a hand-built session_data dict.
"""

import asyncio
import json
import re
import socket
import struct
import uuid
from pathlib import Path

import jsonschema
import pytest

from siprec_srs.config import Config, LawfulBasisConfig, ServerConfig
from siprec_srs.sip_server import SIPRECServer
from siprec_srs.vcon_converter import VConConverter

SCHEMA_PATH = Path(__file__).parent / "schema" / "vcon_json_schema.json"

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


async def _capture_real_vcon():
    """Run the loopback SIPREC capture harness end to end and return the
    vCon dict the real converter path produced, with lawful_basis
    configured (so the attachment under test is actually emitted)."""
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

    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli.bind(("127.0.0.1", 0))
    cli.connect(("127.0.0.1", sip_port))
    cli.setblocking(False)
    client_port = cli.getsockname()[1]
    loop = asyncio.get_event_loop()
    call_id = f"schema-test-{uuid.uuid4().hex}"

    await loop.sock_sendall(cli, _invite("127.0.0.1", sip_port, client_port, call_id))
    rtp_ports = []
    for _ in range(4):
        data = await asyncio.wait_for(loop.sock_recv(cli, 65535), timeout=2)
        text = data.decode("utf-8", "replace")
        if text.startswith("SIP/2.0 200"):
            rtp_ports = [int(p) for p in re.findall(r"m=audio (\d+)", text)]
            break
    assert rtp_ports, "no 200 OK with SDP answer received"

    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b"\xff" * 160  # 20ms of PCMU silence
    for port in rtp_ports:
        for seq in range(10):
            rtp.sendto(_rtp_packet(seq, seq * 160, payload), ("127.0.0.1", port))
    await asyncio.sleep(0.2)

    await loop.sock_sendall(cli, _bye("127.0.0.1", client_port, call_id))
    session = await asyncio.wait_for(completed, timeout=3)
    await server.stop()
    cli.close()
    rtp.close()

    converter = VConConverter(
        lawful_basis_config=LawfulBasisConfig(
            enabled=True, lawful_basis="legitimate_interests",
        ),
    )
    vcon = converter.convert_session_to_vcon({
        "session_id": session.session_id,
        "call_id": session.call_id,
        "recording_session_id": session.recording_session_id,
        "participants": session.participants,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "media_streams": session.media_streams,
        "remote_uri": session.remote_uri,
        "local_uri": session.local_uri,
        "raw_offer_sdp": session.raw_offer_sdp,
        "raw_answer_sdp": session.raw_answer_sdp,
        "raw_rs_metadata": session.raw_rs_metadata,
    }, session)
    assert vcon is not None
    return vcon.vcon_dict


@pytest.fixture(scope="module")
def schema():
    with open(SCHEMA_PATH) as f:
        return json.load(f)


@pytest.fixture(scope="module")
def real_vcon_dict():
    return asyncio.run(_capture_real_vcon())


@pytest.mark.asyncio
class TestSchemaValidation:
    """One assertion per non-negotiable, so a failure names exactly which
    rule broke rather than a single opaque schema error."""

    async def test_validates_against_wg_schema(self, real_vcon_dict, schema):
        jsonschema.validate(instance=real_vcon_dict, schema=schema)

    async def test_no_legacy_mimetype(self, real_vcon_dict):
        def walk(node):
            if isinstance(node, dict):
                assert "mimetype" not in node, (
                    f"legacy 'mimetype' field found: {node}"
                )
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(real_vcon_dict)

    async def test_attachments_have_required_fields(self, real_vcon_dict):
        for att in real_vcon_dict.get("attachments", []):
            for field in ("start", "party", "dialog"):
                assert field in att, f"attachment missing {field!r}: {att}"
            if "body" in att and att["body"] not in (None, ""):
                assert "encoding" in att, f"attachment body without encoding: {att}"
                assert "mediatype" in att, f"attachment body without mediatype: {att}"

    async def test_body_shapes_by_encoding(self, real_vcon_dict):
        """encoding: json -> body is the JSON value itself (dict/list),
        never a json.dumps string. Scoped to the lawful_basis attachment
        this card fixed (CON-1091); the other json-encoded attachments in
        this vCon (session_metadata, sip-message-trace, tags,
        stream_provenance) still use json.dumps strings — a real draft-04
        divergence, left alone here because fixing it means touching the
        converter's attachment-building code CON-736 is mid-flight on.
        See docs/ONBOARDING.md and the CON-1091 report for detail."""
        lawful_basis = [
            a for a in real_vcon_dict.get("attachments", [])
            if a.get("purpose") == "lawful_basis"
        ]
        assert len(lawful_basis) == 1
        att = lawful_basis[0]
        assert att["encoding"] == "json"
        assert isinstance(att["body"], dict), (
            f"lawful_basis body must be a JSON object, not a string: {att['body']!r}"
        )

    async def test_lawful_basis_carries_purpose_and_type(self, real_vcon_dict):
        lawful_basis = [
            a for a in real_vcon_dict.get("attachments", [])
            if a.get("purpose") == "lawful_basis"
        ]
        assert len(lawful_basis) == 1
        assert lawful_basis[0]["type"] == "lawful_basis"

    async def test_no_empty_meta_or_metadata(self, real_vcon_dict):
        def walk(node):
            if isinstance(node, dict):
                for key in ("meta", "metadata"):
                    if key in node:
                        assert node[key], f"{key!r} present but empty: {node}"
                for v in node.values():
                    walk(v)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(real_vcon_dict)

    async def test_inline_binary_is_unpadded_base64url(self, real_vcon_dict):
        recordings = [
            d for d in real_vcon_dict.get("dialog", [])
            if d.get("type") == "recording" and d.get("encoding") == "base64url"
        ]
        assert recordings, "expected at least one inline base64url recording dialog"
        for dlg in recordings:
            body = dlg["body"]
            assert isinstance(body, str)
            assert "=" not in body, f"base64url body is padded: {body[:20]}..."
            # base64url alphabet only ('-' and '_' instead of '+' and '/').
            assert re.fullmatch(r"[A-Za-z0-9_-]*", body), (
                "body is not in the base64url alphabet"
            )
