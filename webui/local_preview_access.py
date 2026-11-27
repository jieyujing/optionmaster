"""Opt-in, numeric-loopback-only manual preview access, not a production auth mode."""
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

_BASE = "/api/v4/research/strategy/"
_READ = frozenset({_BASE + name for name in ("coverage", "chain", "quote-details-v1")})
_CALC = frozenset({_BASE + name for name in ("payoffs", "valuations")})
_PAGE = "/strategy-preview"
_ASSETS = frozenset({_PAGE + "/assets/strategy.js", _PAGE + "/assets/strategy.css"})


class LocalPreviewAccess:
    """Accept local requests only; protect against cross-origin browser requests.

    Requires a real loopback listener, proxy headers disabled, and an exact
    numeric Host/port. Other local processes can use the read-only research UI.
    The underlying authenticated API remains unchanged.
    """

    def __init__(self, app: ASGIApp, *, port: int, internal_token: str, cache_mode: bool = False) -> None:
        if not 1 <= port <= 65535 or not 32 <= len(internal_token) <= 256:
            raise ValueError("Explicit private preview binding required")
        self.app = app
        self.cache_mode = cache_mode
        self.host = f"127.0.0.1:{port}".encode("ascii")
        self.origin = b"http://" + self.host
        self.authorization = b"Bearer " + internal_token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await self.app(scope, receive, send)
            return
        headers = scope.get("headers", [])
        def values(name: bytes) -> list[bytes]:
            return [v for k, v in headers if k.lower() == name]

        path, method = scope["path"], scope["method"]
        peer = scope.get("client")
        origin, site = values(b"origin"), values(b"sec-fetch-site")
        if (peer is None or peer[0] != "127.0.0.1"
                or scope.get("scheme") != "http"
                or values(b"host") != [self.host]
                or any(values(k) for k in (b"forwarded", b"x-forwarded-for", b"x-forwarded-host", b"x-forwarded-proto"))
                or len(origin) > 1 or (origin and origin != [self.origin])
                or len(site) > 1):
            await JSONResponse({"detail": "Local preview only"}, status_code=403)(scope, receive, send)
            return
        if path == _PAGE and method == "GET":
            # Allow a user to open the page from a bookmark or another tab.
            await self.app(scope, receive, send)
            return
        is_api = (method == "GET" and path in _READ) or (method == "POST" and path in _CALC)
        if self.cache_mode:
            is_api = is_api or (method == "GET" and path in {"/api/local-cache/chain", "/api/local-cache/details", "/api/local-cache/model"}) or (method == "POST" and path == "/api/local-cache/model")
        is_asset = method == "GET" and path in _ASSETS
        if not is_api and not is_asset:
            await JSONResponse({"detail": "Preview route unavailable"}, status_code=404)(scope, receive, send)
            return
        if (site and site != [b"same-origin"]) or (method == "POST" and origin != [self.origin]):
            await JSONResponse({"detail": "Same-origin preview only"}, status_code=403)(scope, receive, send)
            return
        if is_api:
            # Never trust a client-supplied bearer for the local adapter.
            scope = dict(scope)
            scope["headers"] = [(k, v) for k, v in headers if k.lower() != b"authorization"] + [
                (b"authorization", self.authorization),
            ]
        await self.app(scope, receive, send)
