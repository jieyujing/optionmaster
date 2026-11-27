"""Explicit local-only research UI composition; never starts on import.

Run from the repository root after building webui. The operator chooses a
local research bearer token; it is not a production credential. No live
database access, trading, public listener, or production TLS claim.
"""
from argparse import ArgumentParser
from getpass import getpass
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse
import uvicorn

from vnpy_optionmaster.research_api import create_research_app, load_reviewed_archive
from vnpy_optionmaster.research_chain_api import create_chain_research_app, load_reviewed_chain
from vnpy_optionmaster.research_catalog_api import create_catalog_research_app, load_reviewed_catalog

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "webui/dist"
CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; "
    "connect-src 'self'; font-src 'self'; img-src 'self' data:; "
    "base-uri 'none'; object-src 'none'; frame-ancestors 'none'; form-action 'self'"
)
STATIC_HEADERS = {
    "Content-Security-Policy": CSP,
    "Cache-Control": "no-store",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def create_local_app(token: str) -> FastAPI:
    """Serve the built shell and v1/v2/v3 APIs with one shared worker lock."""
    for name in ("index.html", "assets/app.js", "assets/app.css"):
        if not (DIST / name).is_file():
            raise RuntimeError("Build webui before starting the local service")
    source = load_reviewed_archive(
        ROOT / "docs/evidence/m1-d04/shfe-20220907",
        ROOT / "docs/evidence/m1-d05/cu2211-20220907",
    )
    lock = Lock()
    app = create_research_app(source, bearer_token=token, worker_lock=lock)
    chain = load_reviewed_chain(
        ROOT / "docs/evidence/m1-d04/shfe-20220907",
        ROOT / "docs/evidence/m1-d05/cu2211-20220907",
        ROOT / "docs/evidence/m1-d08/cu2211-20220907",
    )
    app.include_router(create_chain_research_app(chain, bearer_token=token, worker_lock=lock).router)
    catalog = load_reviewed_catalog(ROOT / "docs/evidence")
    app.include_router(create_catalog_research_app(catalog, bearer_token=token, worker_lock=lock).router)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1"])

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(DIST / "index.html", headers=STATIC_HEADERS)

    @app.get("/assets/app.js", include_in_schema=False)
    async def script() -> FileResponse:
        return FileResponse(DIST / "assets/app.js", media_type="text/javascript", headers=STATIC_HEADERS)

    @app.get("/assets/app.css", include_in_schema=False)
    async def stylesheet() -> FileResponse:
        return FileResponse(DIST / "assets/app.css", media_type="text/css", headers=STATIC_HEADERS)

    return app


def main() -> None:
    parser = ArgumentParser(description="OptionMaster archived research desk (loopback only)")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be in 1024..65535")
    token = getpass("Choose an ephemeral local research token (32..256 ASCII characters): ")
    app = create_local_app(token)
    print(f"Local archived research only: http://127.0.0.1:{args.port}/")
    uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False,
                ws="none", limit_concurrency=16, timeout_keep_alive=5)


if __name__ == "__main__":
    main()
