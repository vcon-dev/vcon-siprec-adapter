"""Read-only vCon retrieval API on the health server (auth + traversal guard)."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from siprec_srs.health_server import HealthServer

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def _client(server: HealthServer) -> TestClient:
    app = web.Application()
    app.router.add_get("/vcons", server._list_vcons)
    app.router.add_get("/vcons/{name}", server._get_vcon)
    return TestClient(TestServer(app))


@pytest.mark.asyncio
async def test_vcon_api_auth_list_and_fetch(tmp_path):
    (tmp_path / "a.vcon.json").write_text('{"uuid": "a"}')
    (tmp_path / "b.vcon.json").write_text('{"uuid": "b"}')
    server = HealthServer(vcon_dir=tmp_path, auth_token=TOKEN)

    async with _client(server) as client:
        # No token -> 401.
        assert (await client.get("/vcons")).status == 401
        # Wrong token -> 401.
        bad = await client.get("/vcons", headers={"Authorization": "Bearer nope"})
        assert bad.status == 401
        # Authorized list.
        resp = await client.get("/vcons", headers=AUTH)
        assert resp.status == 200
        body = await resp.json()
        assert body["count"] == 2 and "a.vcon.json" in body["vcons"]
        # Authorized fetch returns the stored vCon.
        got = await client.get("/vcons/a.vcon.json", headers=AUTH)
        assert got.status == 200 and (await got.json())["uuid"] == "a"
        # Path traversal is rejected.
        assert (await client.get("/vcons/..%2f..%2fetc%2fpasswd", headers=AUTH)).status == 404


@pytest.mark.asyncio
async def test_vcon_api_disabled_without_token(tmp_path):
    server = HealthServer(vcon_dir=tmp_path, auth_token=None)
    async with _client(server) as client:
        # Even with no auth configured, the endpoint is closed, not open.
        assert (await client.get("/vcons", headers=AUTH)).status == 503
