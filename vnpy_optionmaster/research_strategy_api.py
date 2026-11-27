"""D12 v4 research ASGI adapter. Explicit provider injection; no listener/DB."""
import asyncio
from collections.abc import Callable
from datetime import date
from hashlib import sha256
import json
import re
from secrets import compare_digest
from threading import Lock
from typing import Annotated, Any, Protocol
from uuid import uuid4

from fastapi import FastAPI, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from vnpy_optionmaster.research_api import _HEADERS, _json_object
from vnpy_optionmaster.research_strategy_api_dto import (
    BASE_PATH, MAX_BODY_BYTES, MAX_RESPONSE_BYTES, Reason, StrategyEnvelopeDTO,
    StrategyChainDTO, StrategyCoverageDTO, StrategyCurveDTO, StrategyGreekGroupDTO,
    StrategyPayoffRequestDTO, StrategyValuationRequestDTO, strategy_openapi,
)
from vnpy_optionmaster.research_strategy_payoff import (
    StrategyQuoteSnapshot, StrategyUnavailable, evaluate_payoff, resolve_strategy,
)


class StrategySnapshotProvider(Protocol):
    """Server-owned provider, responsible for source admission and bounded reads.

    A real provider must freshly materialize one coherent selected snapshot
    and independently verified entry history; fixtures cannot be live providers.
    """

    def load_snapshot(self, snapshot_id: str) -> StrategyQuoteSnapshot | None: ...


class StrategyMarketDataProvider(StrategySnapshotProvider, Protocol):
    def coverage(self, exchange: str | None, product: str | None, cursor: str | None) -> StrategyCoverageDTO: ...
    def chain(self, exchange: str, product: str, trade_date: str, underlying_id: str,
              snapshot_id: str | None, cursor: str | None) -> StrategyChainDTO: ...


def _selectors(request: Request, *, chain: bool) -> dict[str, str]:
    allowed = {"exchange", "product", "cursor"}
    if chain:
        allowed |= {"trade_date", "underlying_id", "snapshot_id"}
    result: dict[str, str] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed or key in result or not 1 <= len(value) <= 128 or any(c.isspace() for c in value):
            raise StrategyHttpRejected(422, "INVALID_REQUEST")
        if key in {"cursor", "snapshot_id"} and not re.fullmatch(r"[0-9a-f]{64}", value):
            raise StrategyHttpRejected(422, "INVALID_REQUEST")
        result[key] = value
    if ("exchange" in result) != ("product" in result):
        raise StrategyHttpRejected(422, "INVALID_REQUEST")
    if chain:
        if not {"exchange", "product", "trade_date", "underlying_id"} <= result.keys():
            raise StrategyHttpRejected(422, "INVALID_REQUEST")
        try:
            if date.fromisoformat(result["trade_date"]).isoformat() != result["trade_date"]:
                raise ValueError()
        except ValueError as exc:
            raise StrategyHttpRejected(422, "INVALID_REQUEST") from exc
    return result


class StrategyHttpRejected(Exception):
    def __init__(self, status: int, reason: Reason) -> None:
        self.status = status
        self.reason = reason


async def read_strategy_json(request: Request) -> dict[str, Any]:
    # Same security semantics as D06, separate v4 size ceiling. The shared
    # duplicate-key/constant decoder is reused; v1-v3 limits are untouched.
    media, encoding = request.headers.getlist("content-type"), request.headers.getlist("content-encoding")
    if len(media) != 1 or media[0].strip().lower() not in (
        "application/json", "application/json; charset=utf-8", "application/json;charset=utf-8",
    ) or (encoding and encoding != ["identity"]):
        raise StrategyHttpRejected(415, "UNSUPPORTED_MEDIA_TYPE")
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdecimal())):
        raise StrategyHttpRejected(422, "INVALID_REQUEST")
    if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > MAX_BODY_BYTES):
        raise StrategyHttpRejected(413, "PAYLOAD_TOO_LARGE")
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY_BYTES:
                raise StrategyHttpRejected(413, "PAYLOAD_TOO_LARGE")
            body.extend(chunk)
    except ClientDisconnect:
        raise StrategyHttpRejected(422, "INVALID_REQUEST") from None
    try:
        return _json_object(bytes(body))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise StrategyHttpRejected(422, "INVALID_JSON") from None


def input_hash(request: StrategyPayoffRequestDTO) -> str:
    raw = json.dumps(request.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def failure(cid: str, reason: Reason, request: StrategyPayoffRequestDTO | None = None) -> StrategyEnvelopeDTO:
    return StrategyEnvelopeDTO(
        schema_version="4.0", calculation_id=cid, status="UNAVAILABLE", reason=reason,
        portfolio_revision=None if request is None else request.portfolio_revision,
        snapshot_id=None if request is None else request.snapshot_id,
        input_hash=None if request is None else input_hash(request),
        coverage=None, chain=None, payoff=None, valuation=None,
    )


def strategy_response(result: StrategyEnvelopeDTO, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS)
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429:
        headers["Retry-After"] = "1"
    response = JSONResponse(result.model_dump(mode="json"), status_code=status, headers=headers)
    if len(response.body) > MAX_RESPONSE_BYTES:
        return JSONResponse(failure(result.calculation_id, "INTERNAL_ERROR").model_dump(mode="json"),
                            status_code=500, headers=_HEADERS)
    return response


def materialize_portfolio(provider: StrategySnapshotProvider, request: StrategyPayoffRequestDTO, *,
                          allow_synthetic_fixture: bool = False) -> tuple[StrategyQuoteSnapshot, dict[str, StrategyQuoteSnapshot]]:
    current = provider.load_snapshot(request.snapshot_id)
    if current is None or current.metadata.snapshot_id != request.snapshot_id:
        raise StrategyUnavailable("SNAPSHOT_STALE")
    if current.metadata.provider == "SYNTHETIC_FIXTURE" and not allow_synthetic_fixture:
        raise StrategyUnavailable("CANDIDATE_ONLY")
    entries: dict[str, StrategyQuoteSnapshot] = {request.snapshot_id: current}
    ids = sorted({leg.entry_snapshot_id for leg in [*request.options, *request.underlyings]} - entries.keys())
    for key in ids:
        snapshot = provider.load_snapshot(key)
        if snapshot is None or snapshot.metadata.snapshot_id != key:
            raise StrategyUnavailable("ENTRY_UNAVAILABLE")
        if snapshot.metadata.provider == "SYNTHETIC_FIXTURE" and not allow_synthetic_fixture:
            raise StrategyUnavailable("CANDIDATE_ONLY")
        entries[key] = snapshot
    return current, entries


def create_strategy_payoff_app(provider: StrategySnapshotProvider, *, bearer_token: str,
                               allow_synthetic_fixture: bool = False,
                               market_data: StrategyMarketDataProvider | None = None) -> FastAPI:
    if market_data is not None and market_data is not provider:
        raise ValueError("Coverage and calculation require the same provider")
    if (not isinstance(bearer_token, str) or not 32 <= len(bearer_token) <= 256
            or not bearer_token.isascii() or not bearer_token.isprintable() or any(c.isspace() for c in bearer_token)):
        raise ValueError("Explicit research bearer token required")
    expected = bearer_token.encode("ascii")
    lock = Lock()
    model_lock = Lock()
    source_lock = Lock()
    quote_lock = Lock()
    app = FastAPI(title="OptionMaster Research Strategy Payoff API", version="4.0",
                  docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    security = HTTPBearer(auto_error=False, scheme_name="ResearchBearer")

    def authorized(request: Request, credentials: HTTPAuthorizationCredentials | None) -> bool:
        if len(request.headers.getlist("authorization")) != 1 or credentials is None:
            return False
        token = credentials.credentials
        return (credentials.scheme.lower() == "bearer" and token.isascii()
                and len(token) <= 256 and compare_digest(token.encode("ascii"), expected))

    def execute(cid: str, body: StrategyPayoffRequestDTO) -> StrategyEnvelopeDTO:
        try:
            current, entries = materialize_portfolio(provider, body, allow_synthetic_fixture=allow_synthetic_fixture)
            payoff = evaluate_payoff(resolve_strategy(body, current, entries))
            complete = payoff.terminal.status == "OK" and payoff.risk.scope != "UNAVAILABLE"
            return StrategyEnvelopeDTO(
                schema_version="4.0", calculation_id=cid, status="OK" if complete else "PARTIAL",
                reason=None if complete else (payoff.terminal.reason or "DOMAIN_UNAVAILABLE"),
                portfolio_revision=body.portfolio_revision, snapshot_id=body.snapshot_id, input_hash=input_hash(body),
                coverage=None, chain=None, payoff=payoff, valuation=None,
            )
        except StrategyUnavailable as exc:
            return failure(cid, exc.reason, body)
        except (ValueError, ArithmeticError):
            return failure(cid, "NUMERICAL_FAILURE", body)
        except Exception:
            return failure(cid, "INTERNAL_ERROR", body)

    def execute_model(cid: str, body: StrategyValuationRequestDTO) -> StrategyEnvelopeDTO:
        from vnpy_optionmaster.research_strategy_valuation import evaluate_strategy_valuation

        try:
            current, entries = materialize_portfolio(provider, body, allow_synthetic_fixture=allow_synthetic_fixture)
            value = evaluate_strategy_valuation(resolve_strategy(body, current, entries), current.rates)
            capabilities: list[StrategyCurveDTO | StrategyGreekGroupDTO] = [
                value.current, value.scenario, value.horizon, *value.current_greeks, *value.scenario_greeks,
            ]
            complete = all(item.status == "OK" for item in capabilities)
            reason = next((item.reason for item in capabilities if item.status != "OK"), None)
            if not any(item.status != "UNAVAILABLE" for item in capabilities):
                return failure(cid, reason or "MODEL_UNAVAILABLE", body)
            return StrategyEnvelopeDTO(schema_version="4.0", calculation_id=cid,
                status="OK" if complete else "PARTIAL", reason=None if complete else reason or "MODEL_UNAVAILABLE",
                portfolio_revision=body.portfolio_revision, snapshot_id=body.snapshot_id, input_hash=input_hash(body),
                coverage=None, chain=None, payoff=None, valuation=value)
        except StrategyUnavailable as exc:
            return failure(cid, exc.reason, body)
        except (ValueError, ArithmeticError):
            return failure(cid, "NUMERICAL_FAILURE", body)
        except Exception:
            return failure(cid, "INTERNAL_ERROR", body)

    async def bounded(lane: Lock, func: Callable[[], StrategyEnvelopeDTO], cid: str,
                      body: StrategyPayoffRequestDTO | None) -> StrategyEnvelopeDTO:
        # Admission BEFORE dispatch: never enqueue unbounded thread-pool work.
        # Shielding and the completion callback keep the lane occupied until
        # the actual worker is done, even if the HTTP client disconnects.
        if not lane.acquire(blocking=False):
            return failure(cid, "BUSY", body)
        try:
            worker = asyncio.create_task(run_in_threadpool(func))
        except BaseException:
            lane.release()
            raise
        worker.add_done_callback(lambda _: lane.release())
        return await asyncio.shield(worker)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        reason: Reason = "NOT_FOUND" if exc.status_code == 404 else "METHOD_NOT_ALLOWED" if exc.status_code == 405 else "INTERNAL_ERROR"
        return strategy_response(failure(uuid4().hex, reason), exc.status_code)

    @app.post(BASE_PATH + "/payoffs", response_model=StrategyEnvelopeDTO)
    async def payoff(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)]) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return strategy_response(failure(cid, "UNAUTHORIZED"), 401)
        if request.query_params:
            return strategy_response(failure(cid, "INVALID_REQUEST"), 422)
        try:
            body = StrategyPayoffRequestDTO.model_validate(await read_strategy_json(request), strict=True)
        except StrategyHttpRejected as exc:
            return strategy_response(failure(cid, exc.reason), exc.status)
        except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
            return strategy_response(failure(cid, "INVALID_REQUEST"), 422)
        result = await bounded(lock, lambda: execute(cid, body), cid, body)
        return strategy_response(result, 429 if result.reason == "BUSY" else 500 if result.reason == "INTERNAL_ERROR" else 200)

    @app.post(BASE_PATH + "/valuations", response_model=StrategyEnvelopeDTO)
    async def valuation(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)]) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return strategy_response(failure(cid, "UNAUTHORIZED"), 401)
        if request.query_params:
            return strategy_response(failure(cid, "INVALID_REQUEST"), 422)
        try:
            body = StrategyValuationRequestDTO.model_validate(await read_strategy_json(request), strict=True)
        except StrategyHttpRejected as exc:
            return strategy_response(failure(cid, exc.reason), exc.status)
        except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
            return strategy_response(failure(cid, "INVALID_REQUEST"), 422)
        result = await bounded(model_lock, lambda: execute_model(cid, body), cid, body)
        return strategy_response(result, 429 if result.reason == "BUSY" else 500 if result.reason == "INTERNAL_ERROR" else 200)

    if market_data is not None:
        def market_read(cid: str, selectors: dict[str, str], *, chain: bool) -> StrategyEnvelopeDTO:
            try:
                coverage = None
                result = None
                if chain:
                    result = market_data.chain(selectors["exchange"], selectors["product"], selectors["trade_date"],
                        selectors["underlying_id"], selectors.get("snapshot_id"), selectors.get("cursor"))
                    s = result.snapshot
                    if (s.exchange != selectors["exchange"] or s.product != selectors["product"]
                            or s.trade_date != selectors["trade_date"] or result.underlying.underlying_id != selectors["underlying_id"]
                            or ("snapshot_id" in selectors and s.snapshot_id != selectors["snapshot_id"])):
                        raise StrategyUnavailable("SNAPSHOT_STALE")
                    if s.provider == "SYNTHETIC_FIXTURE" and not allow_synthetic_fixture:
                        raise StrategyUnavailable("CANDIDATE_ONLY")
                else:
                    coverage = market_data.coverage(selectors.get("exchange"), selectors.get("product"), selectors.get("cursor"))
                return StrategyEnvelopeDTO(schema_version="4.0", calculation_id=cid, status="OK", reason=None,
                    portfolio_revision=None, snapshot_id=result.snapshot.snapshot_id if result else None, input_hash=None,
                    coverage=coverage, chain=result, payoff=None, valuation=None)
            except StrategyUnavailable as exc:
                return failure(cid, exc.reason)
            except Exception:
                return failure(cid, "INTERNAL_ERROR")

        async def read_market(request: Request, credentials: HTTPAuthorizationCredentials | None, *, chain: bool) -> JSONResponse:
            cid = uuid4().hex
            if not authorized(request, credentials):
                return strategy_response(failure(cid, "UNAUTHORIZED"), 401)
            try:
                selectors = _selectors(request, chain=chain)
            except StrategyHttpRejected as exc:
                return strategy_response(failure(cid, exc.reason), exc.status)
            result = await bounded(source_lock, lambda: market_read(cid, selectors, chain=chain), cid, None)
            return strategy_response(result, 429 if result.reason == "BUSY" else 500 if result.reason == "INTERNAL_ERROR" else
                                     422 if result.reason == "INVALID_REQUEST" else 200)

        @app.get(BASE_PATH + "/coverage", response_model=StrategyEnvelopeDTO)
        async def coverage(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)]) -> JSONResponse:
            return await read_market(request, credentials, chain=False)

        @app.get(BASE_PATH + "/chain", response_model=StrategyEnvelopeDTO)
        async def chain(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)]) -> JSONResponse:
            return await read_market(request, credentials, chain=True)

        @app.get(BASE_PATH + "/quote-details-v1", include_in_schema=False)
        async def quote_details(request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)]) -> JSONResponse:
            from vnpy_optionmaster.research_quote_details import project_quote_details

            cid = uuid4().hex
            if not authorized(request, credentials):
                return strategy_response(failure(cid, "UNAUTHORIZED"), 401)
            try:
                selectors = _selectors(request, chain=True)
                if "snapshot_id" not in selectors or "cursor" in selectors:
                    raise StrategyHttpRejected(422, "INVALID_REQUEST")
            except StrategyHttpRejected as exc:
                return strategy_response(failure(cid, exc.reason), exc.status)

            def read_details() -> JSONResponse:
                try:
                    snapshot = provider.load_snapshot(selectors["snapshot_id"])
                    if snapshot is None:
                        raise StrategyUnavailable("SNAPSHOT_STALE")
                    s = snapshot.metadata
                    if (s.snapshot_id, s.exchange, s.product, s.trade_date) != (
                        selectors["snapshot_id"], selectors["exchange"], selectors["product"], selectors["trade_date"]
                    ):
                        raise StrategyUnavailable("SNAPSHOT_STALE")
                    if s.provider == "SYNTHETIC_FIXTURE" and not allow_synthetic_fixture:
                        raise StrategyUnavailable("CANDIDATE_ONLY")
                    details = project_quote_details(snapshot, selectors["underlying_id"])
                    response = JSONResponse(details.model_dump(mode="json"), headers=_HEADERS)
                    if len(response.body) > MAX_RESPONSE_BYTES:
                        raise StrategyUnavailable("SOURCE_INCOMPLETE")
                    return response
                except StrategyUnavailable as exc:
                    return strategy_response(failure(cid, exc.reason), 422)
                except Exception:
                    return strategy_response(failure(cid, "INTERNAL_ERROR"), 500)

            # Detail computation must not monopolize the chain selector lane.
            if not quote_lock.acquire(blocking=False):
                return strategy_response(failure(cid, "BUSY"), 429)
            try:
                worker = asyncio.create_task(run_in_threadpool(read_details))
            except BaseException:
                quote_lock.release()
                raise
            worker.add_done_callback(lambda _: quote_lock.release())
            return await asyncio.shield(worker)

    # Export only implemented routes from the frozen additive contract.
    def openapi() -> dict[str, Any]:
        schema = strategy_openapi()
        if market_data is None:
            schema["paths"] = {path: schema["paths"][path] for path in (BASE_PATH + "/payoffs", BASE_PATH + "/valuations")}
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
    return app
