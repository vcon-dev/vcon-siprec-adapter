"""Lightweight HTTP server exposing /healthz and /metrics for the adapter.

Designed to be cheap and dependency-free relative to the rest of the app:
uses aiohttp (already a dep). The server is optional — if disabled in
config, nothing is started.
"""

from __future__ import annotations

import hmac
import logging
from datetime import datetime, timezone
from pathlib import Path
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
        vcon_dir: Optional[Path] = None,
        auth_token: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.webhook_stats_provider = webhook_stats_provider
        # Read-only vCon retrieval API for QA/partners. Serves the vCons already
        # written to disk. Disabled unless BOTH a directory and a token are set,
        # so recordings are never exposed without auth.
        self.vcon_dir = Path(vcon_dir) if vcon_dir else None
        self.auth_token = auth_token
        self._runner: Optional[web.AppRunner] = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/healthz", self._healthz)
        app.router.add_get("/metrics", self._metrics)
        app.router.add_get("/vcons", self._list_vcons)
        app.router.add_get("/vcons/{name}", self._get_vcon)
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

    # ---- read-only vCon retrieval API ---------------------------------

    def _gate(self, request: web.Request) -> Optional[web.Response]:
        """Return an error Response if the request may not access vCons, else None."""
        if self.vcon_dir is None or not self.auth_token:
            return web.json_response({"error": "vcon API disabled"}, status=503)
        got = request.headers.get("Authorization", "")
        if not hmac.compare_digest(got, f"Bearer {self.auth_token}"):
            return web.json_response({"error": "unauthorized"}, status=401)
        return None

    async def _list_vcons(self, request: web.Request) -> web.Response:
        denied = self._gate(request)
        if denied is not None:
            return denied
        names = sorted((p.name for p in self.vcon_dir.glob("*.json")), reverse=True)
        return web.json_response({"count": len(names), "vcons": names})

    async def _get_vcon(self, request: web.Request) -> web.Response:
        denied = self._gate(request)
        if denied is not None:
            return denied
        name = request.match_info["name"]
        # Basename only: no path traversal, must be a .json in vcon_dir.
        if "/" in name or "\\" in name or ".." in name or not name.endswith(".json"):
            return web.json_response({"error": "not found"}, status=404)
        path = self.vcon_dir / name
        if not path.is_file():
            return web.json_response({"error": "not found"}, status=404)
        return web.FileResponse(path)
