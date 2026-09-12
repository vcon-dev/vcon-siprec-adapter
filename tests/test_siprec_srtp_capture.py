"""
End-to-end SRTP capture: an RTP/SAVP INVITE with SDES keys, encrypted RTP to
the answered port, BYE. Asserts the SRS answered SAVP with its own a=crypto,
decrypted the audio into the WAV, and rejects offers it cannot decrypt.
"""

import asyncio
import re
import socket
import uuid
import wave

import pytest

from siprec_srs.config import Config, ServerConfig
from siprec_srs.sip_server import SIPRECServer
from siprec_srs.srtp import SRTPContext
from tests.test_siprec_capture import RS_METADATA, _bye
from tests.test_srtp import INLINE, _hdr, _protect

SUITE = "AES_CM_128_HMAC_SHA1_80"


def _invite(dst_ip, client_port, call_id, crypto_lines):
    sdp = (
        "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\ns=siprec\r\n"
        "c=IN IP4 127.0.0.1\r\nt=0 0\r\n"
        "m=audio 40000 RTP/SAVP 0\r\n"
        "a=rtpmap:0 PCMU/8000\r\n"
        + "".join(f"a=crypto:{c}\r\n" for c in crypto_lines) +
        "a=sendonly\r\na=label:1\r\n"
    )
    body = (
        "--bnd\r\nContent-Type: application/sdp\r\n\r\n"
        f"{sdp}\r\n"
        "--bnd\r\nContent-Type: application/rs-metadata+xml\r\n\r\n"
        f"{RS_METADATA}\r\n--bnd--\r\n"
    ).encode()
    return (
        f"INVITE sip:recorder@{dst_ip} SIP/2.0\r\n"
        f"Via: SIP/2.0/UDP 127.0.0.1:{client_port};branch=z9hG4bK{uuid.uuid4().hex[:8]};rport\r\n"
        "Max-Forwards: 70\r\n"
        "From: <sip:src@srs.example>;tag=srctag\r\n"
        f"To: <sip:recorder@{dst_ip}>\r\n"
        f"Call-ID: {call_id}\r\nCSeq: 1 INVITE\r\n"
        f"Contact: <sip:src@127.0.0.1:{client_port}>\r\n"
        "Content-Type: multipart/mixed;boundary=bnd\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body


async def _start_server():
    cfg = Config(server=ServerConfig(
        listen_address="127.0.0.1", sip_port_udp=0, sip_port_tcp=0,
        sip_port_tls=0, tls_cert=None, tls_key=None))
    server = SIPRECServer(cfg)
    await server.start()
    sip_port = server._udp_transports[0].get_extra_info("sockname")[1]
    cli = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    cli.bind(("127.0.0.1", 0))
    cli.connect(("127.0.0.1", sip_port))
    cli.setblocking(False)
    return server, cli


async def _final_response(loop, cli):
    for _ in range(4):
        data = await asyncio.wait_for(loop.sock_recv(cli, 65535), timeout=2)
        text = data.decode("utf-8", "replace")
        if not text.startswith("SIP/2.0 1"):
            return text
    raise AssertionError("no final response")


@pytest.mark.asyncio
async def test_savp_offer_is_decrypted_to_wav():
    server, cli = await _start_server()
    loop = asyncio.get_event_loop()
    completed = loop.create_future()

    async def on_complete(session):
        if not completed.done():
            completed.set_result(session)
    server.set_session_complete_callback(on_complete)

    call_id = f"srtp-{uuid.uuid4().hex}"
    await loop.sock_sendall(cli, _invite("127.0.0.1", cli.getsockname()[1], call_id, [
        f"7 AEAD_AES_256_GCM inline:{'A' * 60}",          # unsupported, skipped
        f"2 {SUITE} inline:{INLINE}|2^31",                # chosen
        f"3 AES_CM_128_HMAC_SHA1_32 inline:{INLINE}",
    ]))
    ok = await _final_response(loop, cli)
    assert ok.startswith("SIP/2.0 200"), ok
    assert re.search(r"m=audio \d+ RTP/SAVP 0", ok)
    m = re.search(rf"a=crypto:2 {SUITE} inline:([A-Za-z0-9+/=]+)", ok)
    assert m, ok
    assert m.group(1) != INLINE  # our own key, not an echo of theirs
    assert ok.count("a=crypto:") == 1
    port = int(re.search(r"m=audio (\d+)", ok).group(1))

    sender = SRTPContext(SUITE, INLINE)
    rtp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    payload = b"\xff" * 160
    for seq in range(10):
        rtp.sendto(_protect(sender, _hdr(seq), payload, 0), ("127.0.0.1", port))
    rtp.sendto(_hdr(10) + payload, ("127.0.0.1", port))  # plaintext must be dropped
    await asyncio.sleep(0.2)

    session = server.sessions[call_id]
    rec = next(iter(session.recorders.values()))
    stats = rec.stats()
    await loop.sock_sendall(cli, _bye("127.0.0.1", cli.getsockname()[1], call_id))
    session = await asyncio.wait_for(completed, timeout=3)
    await server.stop()
    cli.close()
    rtp.close()

    assert stats["srtp"] == SUITE
    assert stats["packet_count"] == 10
    assert stats["srtp_auth_failures"] == 1
    (audio,) = session.get_audio_files().values()
    with wave.open(audio) as w:
        frames = w.readframes(w.getnframes())
    assert w.getnframes() == 10 * 160
    assert frames == b"\x00\x00" * (10 * 160)  # decrypted PCMU 0xff == linear 0


@pytest.mark.asyncio
async def test_savp_offer_without_supported_suite_gets_488():
    server, cli = await _start_server()
    loop = asyncio.get_event_loop()
    call_id = f"srtp-{uuid.uuid4().hex}"
    await loop.sock_sendall(cli, _invite("127.0.0.1", cli.getsockname()[1], call_id,
                                         [f"1 AEAD_AES_256_GCM inline:{'A' * 60}"]))
    resp = await _final_response(loop, cli)
    await server.stop()
    cli.close()
    assert resp.startswith("SIP/2.0 488"), resp
    assert call_id not in server.sessions
