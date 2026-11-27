"""Offline proof that no-login is confined to the explicit local preview adapter."""
import asyncio
from pathlib import Path

import httpx

from webui.local_preview_access import LocalPreviewAccess
from webui.strategy_preview import create_strategy_preview_app
from vnpy_optionmaster.research_strategy_archive import ReviewedStrategyArchive
from vnpy_optionmaster.research_strategy_api import create_strategy_payoff_app

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:52341"
PATH = "/api/v4/research/strategy/"
TOKEN = "local-preview-test-only-not-a-real-secret"


def test_local_access_and_ordinary_api_auth(tmp_path):
    (tmp_path / "strategy.js").write_text("/* local fixture */")
    (tmp_path / "strategy.css").write_text("/* local fixture */")
    provider = ReviewedStrategyArchive(ROOT / "docs/evidence")
    raw = create_strategy_preview_app(TOKEN, tmp_path, provider=provider)
    wrapped = LocalPreviewAccess(raw, port=52341, internal_token=TOKEN)

    async def run():
        def client(app, peer="127.0.0.1"):
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(peer, 12345)), base_url=BASE)
        async with client(wrapped) as c:
            assert (await c.get("/strategy-preview")).status_code == 200
            assert (await c.get("/strategy-preview/assets/strategy.js")).status_code == 200
            res = await c.get(PATH + "coverage")
            assert res.status_code == 200 and res.json()["coverage"]["products"]
            assert (await c.get(PATH + "coverage", headers={"Sec-Fetch-Site":"same-origin"})).status_code == 200
            for headers in (
                {"Host":"evil.example:52341"},
                {"Host":"localhost:52341"},
                {"Origin":"http://evil.example"},
                {"Origin":"null"},
                {"Sec-Fetch-Site":"cross-site"},
                {"Sec-Fetch-Site":"same-site"},
                {"X-Forwarded-For":"127.0.0.1"},
            ):
                assert (await c.get(PATH + "coverage", headers=headers)).status_code == 403
            assert (await c.post(PATH + "payoffs", json={})).status_code == 403
            # Invalid body reaches unchanged API validation, not an auth gate.
            res = await c.post(PATH + "payoffs", json={}, headers={"Origin":BASE, "Sec-Fetch-Site":"same-origin"})
            assert res.status_code == 422
            assert (await c.get("/api/v1/research/coverage")).status_code == 404
            assert (await c.options(PATH + "coverage", headers={"Origin":BASE})).status_code == 404
            assert (await c.get("/strategy-preview", headers={"Sec-Fetch-Site":"cross-site"})).status_code == 200
        async with client(wrapped, "192.168.1.10") as c:
            assert (await c.get(PATH + "coverage")).status_code == 403
        async with client(create_strategy_payoff_app(provider, bearer_token=TOKEN, market_data=provider)) as c:
            assert (await c.get(PATH + "coverage")).status_code == 401
            assert (await c.get(PATH + "coverage", headers={"Authorization":"Bearer "+TOKEN})).status_code == 200
    asyncio.run(run())
