"""Lightweight HTTP server exposing /healthz and /metrics for the adapter.

Designed to be cheap and dependency-free relative to the rest of the app:
uses aiohttp (already a dep). The server is optional — if disabled in
config, nothing is started.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from aiohttp import web

logger = logging.getLogger(__name__)


class HealthServer:
    """Serve /healthz and /metrics on an HTTP port.

    `webhook_stats_provider` is an optional callable returning the dict
    produced by `WebhookDelivery.get_stats()` — when provided, /metrics
    surfaces delivery counters in Prometheus text format.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        webhook_stats_provider: Optional[Callable[[], Dict[str, Any]]] = None,
    ):
        self.host = host
        self.port = port
        self.webhook_stats_provider = webhook_stats_provider
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/metrics", self._metrics)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port)
        await site.start()
        logger.info(f"Health server listening on http://{self.host}:{self.port}")

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _healthz(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "ok",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def _metrics(self, request: web.Request) -> web.Response:
        """Prometheus text-format exposition of webhook delivery stats."""
        if not self.webhook_stats_provider:
            return web.Response(text="# no metrics provider configured\n",
                                content_type="text/plain")
        try:
            stats = self.webhook_stats_provider()
        except Exception as e:
            logger.warning(f"Metrics provider raised: {e}")
            return web.Response(text=f"# error: {e}\n", content_type="text/plain",
                                status=500)

        lines = [
            "# HELP siprec_webhook_total_attempts Total webhook delivery attempts",
            "# TYPE siprec_webhook_total_attempts counter",
            f"siprec_webhook_total_attempts {stats.get('total_attempts', 0)}",
            "# HELP siprec_webhook_successful Successful webhook deliveries",
            "# TYPE siprec_webhook_successful counter",
            f"siprec_webhook_successful {stats.get('successful_deliveries', 0)}",
            "# HELP siprec_webhook_failed Failed webhook deliveries",
            "# TYPE siprec_webhook_failed counter",
            f"siprec_webhook_failed {stats.get('failed_deliveries', 0)}",
            "# HELP siprec_webhook_retries Retry attempts",
            "# TYPE siprec_webhook_retries counter",
            f"siprec_webhook_retries {stats.get('retry_attempts', 0)}",
        ]
        return web.Response(text="\n".join(lines) + "\n",
                            content_type="text/plain; version=0.0.4")
