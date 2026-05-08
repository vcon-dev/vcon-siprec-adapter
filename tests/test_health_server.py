"""Tests for /healthz and /metrics."""

import pytest

from siprec_srs.health_server import HealthServer


@pytest.mark.asyncio
async def test_healthz_returns_ok(aiohttp_client, unused_tcp_port):
    server = HealthServer(host="127.0.0.1", port=unused_tcp_port)
    # Use aiohttp_client + the underlying app rather than starting a TCP site.
    from aiohttp import web
    app = web.Application()
    app.router.add_get("/healthz", server._healthz)
    app.router.add_get("/metrics", server._metrics)
    client = await aiohttp_client(app)

    resp = await client.get("/healthz")
    assert resp.status == 200
    body = await resp.json()
    assert body["status"] == "ok"
    assert "timestamp" in body


@pytest.mark.asyncio
async def test_metrics_emits_prometheus_text(aiohttp_client):
    stats = {
        "total_attempts": 10,
        "successful_deliveries": 7,
        "failed_deliveries": 3,
        "retry_attempts": 4,
    }
    server = HealthServer(webhook_stats_provider=lambda: stats)

    from aiohttp import web
    app = web.Application()
    app.router.add_get("/metrics", server._metrics)
    client = await aiohttp_client(app)

    resp = await client.get("/metrics")
    assert resp.status == 200
    text = await resp.text()
    assert "siprec_webhook_total_attempts 10" in text
    assert "siprec_webhook_successful 7" in text
    assert "siprec_webhook_failed 3" in text
    assert "siprec_webhook_retries 4" in text
    assert resp.content_type.startswith("text/plain")


@pytest.mark.asyncio
async def test_metrics_without_provider_returns_placeholder(aiohttp_client):
    server = HealthServer()  # no provider

    from aiohttp import web
    app = web.Application()
    app.router.add_get("/metrics", server._metrics)
    client = await aiohttp_client(app)

    resp = await client.get("/metrics")
    assert resp.status == 200
    assert "no metrics provider" in await resp.text()
