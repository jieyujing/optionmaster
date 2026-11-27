"""Private local preview composition. Not a deployment or default-root change."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse

from vnpy_optionmaster.research_strategy_api import StrategyMarketDataProvider, create_strategy_payoff_app
from vnpy_optionmaster.research_strategy_archive import ReviewedStrategyArchive
from webui.serve_local import ROOT, create_local_app


def create_strategy_preview_app(token: str, assets: Path, *,
                                provider: StrategyMarketDataProvider | None = None) -> FastAPI:
    """Preserve legacy routes and compose an explicitly selected research provider.

    No service startup or writes. The caller owns the temporary listener and
    resource policy. Default is the unchanged approved archive; injection is
    server-side only and never an implicit fallback or production release.
    """
    app = create_local_app(token)
    if provider is None:
        provider = ReviewedStrategyArchive(ROOT / "docs/evidence")
    strategy = create_strategy_payoff_app(provider, bearer_token=token, market_data=provider)
    app.include_router(strategy.router)
    js, css = assets / "strategy.js", assets / "strategy.css"
    if not js.is_file() or not css.is_file():
        raise RuntimeError("Explicit preview build required")
    headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
    }

    @app.get("/strategy-preview", include_in_schema=False)
    def preview() -> HTMLResponse:
        return HTMLResponse(
            '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>OptionMaster Strategy Preview</title><link rel="stylesheet" href="/strategy-preview/assets/strategy.css">'
            '</head><body><div id="root"></div><script src="/strategy-preview/assets/strategy.js"></script></body></html>',
            headers=headers,
        )

    @app.get("/strategy-preview/assets/strategy.js", include_in_schema=False)
    def script() -> FileResponse:
        return FileResponse(js, media_type="application/javascript", headers=headers)

    @app.get("/strategy-preview/assets/strategy.css", include_in_schema=False)
    def style() -> FileResponse:
        return FileResponse(css, media_type="text/css", headers=headers)

    return app
