"""Tests for webhook hardening: HMAC signing, idempotency, DLQ."""

import hashlib
import hmac
import json
import re
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp import web

from siprec_srs.config import WebhookConfig, WebhookEndpoint
from siprec_srs.webhook_delivery import WebhookDelivery


SHA256_HEX = re.compile(r"^sha256=[0-9a-f]{64}$")


class _FakeVcon:
    """Minimal Vcon stand-in that exposes `uuid` and `to_dict()`."""

    def __init__(self, uuid="019e08c5-3065-868f-9dd8-dd37220d739c", payload=None):
        self.uuid = uuid
        self._payload = payload or {"vcon": "0.4.0", "uuid": uuid, "parties": []}

    def to_dict(self):
        return self._payload


# ---------------------------------------------------------------------------
# HMAC + idempotency: pure-function tests (no network)
# ---------------------------------------------------------------------------

class TestSignBody:
    def test_format_is_sha256_hex(self):
        sig = WebhookDelivery._sign_body("topsecret", b"hello")
        assert SHA256_HEX.match(sig)

    def test_matches_stdlib_hmac(self):
        body = b'{"vcon":"0.4.0"}'
        secret = "topsecret"
        sig = WebhookDelivery._sign_body(secret, body)
        expected = "sha256=" + hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        assert sig == expected

    def test_different_secrets_diverge(self):
        body = b"hello"
        a = WebhookDelivery._sign_body("a", body)
        b = WebhookDelivery._sign_body("b", body)
        assert a != b


class TestBuildHeaders:
    def _delivery(self, dlq=None):
        cfg = WebhookConfig(enabled=True, endpoints=[], dlq_path=dlq)
        return WebhookDelivery(cfg)

    def test_idempotency_key_is_vcon_uuid(self):
        d = self._delivery()
        ep = WebhookEndpoint(url="https://example.com/")
        headers = d._build_headers(
            ep, body_bytes=b"{}", idempotency_key="abc-uuid",
            session_id="s", call_id="c",
        )
        assert headers["Idempotency-Key"] == "abc-uuid"
        assert headers["X-Session-ID"] == "s"
        assert headers["X-Call-ID"] == "c"

    def test_hmac_header_present_only_when_secret_configured(self):
        d = self._delivery()

        ep_unsigned = WebhookEndpoint(url="https://example.com/")
        h1 = d._build_headers(
            ep_unsigned, body_bytes=b"{}", idempotency_key="k",
            session_id="s", call_id=None,
        )
        assert "X-Hub-Signature-256" not in h1

        ep_signed = WebhookEndpoint(
            url="https://example.com/", hmac_secret="shh"
        )
        body = b'{"hello":"world"}'
        h2 = d._build_headers(
            ep_signed, body_bytes=body, idempotency_key="k",
            session_id="s", call_id=None,
        )
        assert h2["X-Hub-Signature-256"] == WebhookDelivery._sign_body("shh", body)

    def test_call_id_omitted_when_none(self):
        d = self._delivery()
        ep = WebhookEndpoint(url="https://example.com/")
        h = d._build_headers(
            ep, body_bytes=b"{}", idempotency_key="k",
            session_id="s", call_id=None,
        )
        assert "X-Call-ID" not in h


# ---------------------------------------------------------------------------
# DLQ tests
# ---------------------------------------------------------------------------

class TestDLQ:
    def test_writes_files_when_path_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = WebhookConfig(enabled=True, endpoints=[], dlq_path=tmp)
            d = WebhookDelivery(cfg)

            vcon = _FakeVcon(uuid="abc-uuid")
            payload = {"vcon": "0.4.0", "uuid": "abc-uuid"}
            results = {"endpoints": [{"url": "x", "status": "failed"}]}

            written = d._write_to_dlq(vcon, payload, results)
            assert written is not None

            # vCon JSON is recoverable.
            vcon_path = Path(written).with_suffix(".vcon.json")
            assert vcon_path.exists()
            assert json.loads(vcon_path.read_text())["uuid"] == "abc-uuid"

            # Sidecar metadata describes the failure.
            meta_path = Path(written).with_suffix(".meta.json")
            assert meta_path.exists()
            meta = json.loads(meta_path.read_text())
            assert meta["endpoints"][0]["status"] == "failed"

    def test_no_path_returns_none(self):
        cfg = WebhookConfig(enabled=True, endpoints=[], dlq_path=None)
        d = WebhookDelivery(cfg)
        assert d._write_to_dlq(_FakeVcon(), {}, {}) is None


# ---------------------------------------------------------------------------
# End-to-end delivery test against an in-process aiohttp server
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_signed_delivery_round_trip(aiohttp_server):
    """Verify the receiver sees a body whose HMAC matches X-Hub-Signature-256."""
    received = {}

    async def handler(request: web.Request):
        body = await request.read()
        received["body"] = body
        received["sig"] = request.headers.get("X-Hub-Signature-256")
        received["idempotency_key"] = request.headers.get("Idempotency-Key")
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_post("/hook", handler)
    server = await aiohttp_server(app)
    url = str(server.make_url("/hook"))

    cfg = WebhookConfig(
        enabled=True,
        endpoints=[WebhookEndpoint(url=url, hmac_secret="topsecret",
                                   retry_attempts=0, timeout=5)],
        dlq_path=None,
    )
    delivery = WebhookDelivery(cfg)
    await delivery.start()
    try:
        result = await delivery.deliver_vcon(
            _FakeVcon(uuid="abc-uuid"), session_id="s", call_id="c"
        )
    finally:
        await delivery.stop()

    assert result["endpoints"][0]["status"] == "success"
    assert received["idempotency_key"] == "abc-uuid"
    expected_sig = WebhookDelivery._sign_body("topsecret", received["body"])
    assert received["sig"] == expected_sig
