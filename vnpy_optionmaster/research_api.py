"""Optional, authenticated, archived-only research API; no listener on import.

The trusted application composition layer supplies one immutable archive and
a bearer token. HTTP clients cannot provide paths, credentials, rate policy,
models, evidence or database sources. Every calculation re-admits D04.
"""
from collections.abc import Callable, Mapping
from _thread import LockType
from dataclasses import dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from math import fsum
from pathlib import Path
from secrets import compare_digest
from threading import Lock
from types import MappingProxyType
from typing import Annotated, Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect
from starlette.responses import JSONResponse

from vnpy_optionmaster.research_api_dto import (
    ApiEnvelope, CellContributionDTO, CoverageDTO, CoverageLegDTO, EvidenceDigestDTO,
    GreeksDTO, LegDTO, RateDTO, ReasonCode, SLICE_ID,
    SourceVersionsDTO, UnderlyingExposureDTO, UnitsDTO, ValuationDTO, ValuationRequestDTO,
)
from vnpy_optionmaster.research_archived_valuation import (
    ArchivedValuation, ArchivedValuationPolicy, ArchivedValuationRequest, evaluate_archived_valuation,
)
from vnpy_optionmaster.research_contract_terms import SourceKey
from vnpy_optionmaster.research_grid import ResearchGridPoint
from vnpy_optionmaster.research_history_repository import HistoricalFuturesLeg, HistoricalReadRequest
from vnpy_optionmaster.research_risk import ResearchGreeks, ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import (
    ReviewedSliceLeg, SliceApproval, SliceStatus, VerifiedHistoricalSlice, admit_verified_slice,
)

_MAX_BODY = 8192
_ZONE = ZoneInfo("Asia/Shanghai")
_DAY = date(2022, 9, 7)
_SYMBOLS = ("CU2211C61000.XSGE", "CU2211P61000.XSGE")
_PIN = "85a27c0d97b836cf51d2f8db7ac92c1e9681138ffe477d0f324f4518276ad826"
_POLICY_PIN = "4c2ef1ce4173a385168189b74b7bbf1361e35276b3c34056a675f107e26e2aa9"
_ARTIFACTS = (
    "option_contract_base.json", "option_daily.json", "future_contract_base.json",
    "future_daily.json", "cu_historical_contract_rule.html", "reference_slice_result.json",
    "clickhouse_live_wire_observation.json", "clickhouse_catalog_calendar_observation.json",
)
_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


@dataclass(frozen=True)
class ReviewedArchiveSource:
    """Server-owned, immutable bytes and trusted local policy; not a request DTO."""

    selection: HistoricalReadRequest
    packet: bytes
    evidence: Mapping[str, bytes]
    approval: SliceApproval
    rate_artifact: bytes
    policy: ArchivedValuationPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(value: str) -> Any:
    raise ValueError("Nonfinite JSON constant")


def _json_object(body: bytes) -> dict[str, Any]:
    parsed = json.loads(body.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_reject_constant)
    if not isinstance(parsed, dict):
        raise ValueError("Expected JSON object")
    return cast(dict[str, Any], parsed)


def _read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if not 0 < len(body) <= limit:
        raise ValueError("Missing or oversized local archive artifact")
    return body


def load_reviewed_archive(d04_root: Path, d05_root: Path) -> ReviewedArchiveSource:
    """Explicit trusted local composition, never called with HTTP-supplied paths.

    The approved D04 packet and evaluated D05 policy are exact one-slice pins.
    Original evidence is not bundled in the wheel or altered by this loader.
    """
    cfg = _json_object(_read_bounded(d04_root / "operator_slice_approval.json", 65536))
    policy_bytes = _read_bounded(d05_root / "research_valuation_policy.json", 65536)
    if sha256(policy_bytes).hexdigest() != _POLICY_PIN:
        raise ValueError("Reviewed D05 policy digest mismatch")
    p = _json_object(policy_bytes)
    if (
        cfg["schema"] != "optionmaster.archived_slice_approval.v1"
        or cfg["packet_sha256"] != _PIN
        or cfg["approval_scope"] != "ARCHIVED_HISTORICAL_INPUTS_ONLY"
        or cfg["production_tls_approved"] is not False or cfg["valuation_ready"] is not False
        or cfg["trade_date"] != _DAY.isoformat() or tuple(cfg["option_symbols"]) != _SYMBOLS
        or cfg["underlying_symbol"] != "SHFE.cu2211"
        or p["schema"] != "optionmaster.d05.archived_valuation_policy.v1"
        or p["scope"] != "RESEARCH_REFERENCE_ONLY" or p["trade_date"] != _DAY.isoformat()
        or p["rate_artifact"] != "chinabond_3m_20220906_07.html"
    ):
        raise ValueError("Unsupported reviewed archive configuration")
    selection = HistoricalReadRequest(_DAY, tuple(
        HistoricalFuturesLeg(
            "XSGE.CU2211.XSGE", "XSGE|CU2211.XSGE|2022-10-25",
            SourceKey("quantfeed.option/JQ", "XSGE", symbol), "SHFE.CU",
            SourceKey("quantfeed.futures/JQ", "SHFE", cfg["underlying_symbol"]),
            "JOINQUANT_GET_EXTRAS:futures_sett_price",
        ) for symbol in _SYMBOLS
    ))
    return ReviewedArchiveSource(
        selection, _read_bounded(d04_root / "vendor_binding_candidate.json", 1048576),
        {name: _read_bounded(d04_root / name, 4 * 1048576) for name in _ARTIFACTS},
        SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"])),
        _read_bounded(d05_root / "chinabond_3m_20220906_07.html", 524288),
        ArchivedValuationPolicy(
            p["clock_policy"], p["day_count_basis"], p["rate_policy"], date.fromisoformat(p["rate_date"]),
            p["rate_source_uri"], p["rate_artifact_sha256"], p["tree_steps"],
        ),
    )


def _reason(code: str | None) -> ReasonCode:
    if code == "SLICE_SCOPE_MISMATCH":
        return ReasonCode.SLICE_SCOPE_MISMATCH
    if code and (code.startswith(("RATE_", "INVALID_RATE"))):
        return ReasonCode.RATE_UNAVAILABLE
    if code == "EXPIRED_SCENARIO":
        return ReasonCode.EXPIRED_SCENARIO
    if code == "INVALID_SCENARIO_TIME":
        return ReasonCode.INVALID_SCENARIO_TIME
    if code in ("INVALID_INPUT", "INVALID_SCENARIO", "UNKNOWN_SHOCK_TARGET", "INVALID_AXES"):
        return ReasonCode.INVALID_SCENARIO
    if code and code.startswith("IV_"):
        return ReasonCode.IV_UNAVAILABLE
    if code in ("NUMERICAL_FAILURE", "BASELINE_DRIFT"):
        return ReasonCode.NUMERICAL_FAILURE
    if code and (code.startswith((
        "EVIDENCE_", "SLICE_", "PACKET_", "REGISTRY_", "ARCHIVE_", "INVALID_SLICE",
    )) or code in ("CONTRACT_UNAVAILABLE", "BASIS_UNAVAILABLE")):
        return ReasonCode.ARCHIVE_UNAVAILABLE
    return ReasonCode.VALUATION_UNAVAILABLE


def _number(value: float | None) -> float:
    if value is None:
        raise ValueError("Missing required model result")
    return float(value)


def _greeks(value: ResearchGreeks | None) -> GreeksDTO:
    if value is None:
        raise ValueError("Missing model Greeks")
    return GreeksDTO(delta=value.delta, gamma=value.gamma, theta_annual=value.theta, vega_per_unit=value.vega)


def _sum_greeks(values: list[GreeksDTO]) -> GreeksDTO:
    return GreeksDTO(
        delta=fsum(v.delta for v in values), gamma=fsum(v.gamma for v in values),
        theta_annual=fsum(v.theta_annual for v in values), vega_per_unit=fsum(v.vega_per_unit for v in values),
    )


def _versions(archive: VerifiedHistoricalSlice) -> SourceVersionsDTO:
    return SourceVersionsDTO(
        packet_sha256=archive.packet_sha256, registry_sha256=archive.registry_sha256,
        artifacts=[EvidenceDigestDTO(artifact=name, sha256=digest) for name, digest in archive.artifact_digests],
    )


def _coverage_leg(source: ReviewedSliceLeg) -> CoverageLegDTO:
    t = source.contract.terms
    if t.option_type not in ("CALL", "PUT"):
        raise ValueError("Unsupported option right")
    return CoverageLegDTO(
        contract_id=t.option_contract_code, source_namespace=t.option_key.namespace,
        source_exchange=t.option_key.exchange, source_symbol=t.option_key.symbol,
        underlying_id=t.underlying.exchange + "." + t.underlying.contract_code,
        expiry_date=t.expiry_date, option_type=cast(Literal["CALL", "PUT"], t.option_type),
        exercise_style=t.exercise_style.value,
        strike=t.strike, contract_multiplier=t.contract_size,
    )


def _point_payload(p: ResearchGridPoint) -> dict[str, Any]:
    ok = p.status is ResearchValuationStatus.OK
    return dict(
        price_shock=p.price_shock, iv_shock=p.iv_shock, status="OK" if ok else "UNAVAILABLE",
        reason_code=None if ok else _reason(p.reason_code),
        baseline_value=p.baseline_value, scenario_value=p.scenario_value, value_change=p.value_change,
        contributions=[CellContributionDTO(
            contract_id=c.contract_id, scenario_value=c.scenario_value, value_change=c.value_change,
            scenario_iv=c.scenario_iv, scenario_time_to_expiry=c.scenario_time_to_expiry,
        ) for c in p.contributions],
    )


def _valuation_payload(a: ArchivedValuation, request: ArchivedValuationRequest) -> dict[str, Any]:
    archive, grid = a.historical_slice, a.grid
    if archive.basis.price_basis is None:
        raise ValueError("Missing basis")
    legs: list[LegDTO] = []
    for source, mark, quantity, calibration, configured in zip(
        archive.legs, archive.basis.legs, request.quantities, a.calibrations,
        grid.configured_contributions, strict=True,
    ):
        t = source.contract.terms
        if t.option_contract_code != configured.contract_id or calibration.iv_result is None:
            raise ValueError("Mismatched or incomplete model output")
        legs.append(LegDTO(
            **_coverage_leg(source).model_dump(),
            quantity=quantity, raw_contract_version=source.raw_terms.contract_version,
            raw_exercise_style=source.raw_terms.exercise_style,
            effective_contract_version=t.contract_version, official_record_id=t.evidence.record_id,
            official_source_uri=t.evidence.source_uri, official_artifact_sha256=t.evidence.content_sha256,
            terms_ingested_at=source.raw_terms.ingested_at, option_ingested_at=mark.option_ingested_at,
            underlying_ingested_at=mark.underlying_ingested_at, option_source=mark.option_source,
            underlying_source=mark.underlying_source, market_option_price=mark.option_price,
            market_underlying_price=mark.underlying_price,
            implied_volatility=_number(calibration.implied_volatility),
            iv_price_residual=_number(calibration.iv_result.residual),
            baseline_value=_number(calibration.baseline_value), baseline_greeks=_greeks(calibration.baseline_greeks),
            scenario_iv=configured.scenario_iv, scenario_time_to_expiry=configured.scenario_time_to_expiry,
            scenario_value=configured.scenario_value, value_change=configured.value_change,
            scenario_greeks=_greeks(configured.scenario_result.scenario_greeks),
        ))
    grouped = {leg.underlying_id for leg in legs}
    exposures = [UnderlyingExposureDTO(
        underlying_id=identity,
        baseline_greeks=_sum_greeks([leg.baseline_greeks for leg in legs if leg.underlying_id == identity]),
        scenario_greeks=_sum_greeks([leg.scenario_greeks for leg in legs if leg.underlying_id == identity]),
    ) for identity in sorted(grouped)]
    r = a.rate
    return dict(
        trade_date=archive.trade_date, data_observed_at=archive.observed_at,
        approved_at=archive.approved_at, admitted_at=archive.admitted_at, reference_at=a.reference_at,
        scenario_at=request.scenario_at, nominal_expiry_at=a.nominal_expiry_at,
        clock_policy=a.policy.clock_policy, time_to_expiry_years=a.time_to_expiry,
        price_basis=archive.basis.price_basis.value, price_basis_fallback_reason=archive.basis.fallback_reason,
        source_versions=_versions(archive),
        rate=RateDTO(
            quote_date=r.quote_date, quoted_percent=r.quoted_percent, tenor_years=r.tenor_years,
            continuous_rate=r.continuous_rate, source_uri=r.source_uri, artifact_sha256=r.artifact_sha256,
            convention=r.convention,
        ),
        model=a.model.value, tree_steps=a.policy.tree_steps, units=UnitsDTO(),
        warnings=[
            "ARCHIVED_NOT_FRESH", "RATE_PROXY", "NOMINAL_EOD_CLOCK", "NO_PUBLICATION_TIME_PIT",
            "DISCRETIZATION_TOLERANCE_NOT_APPROVED", "PRODUCTION_NO_GO",
        ],
        legs=legs, underlying_exposures=exposures,
        grid=dict(
            baseline_value=_number(grid.baseline_value), configured_scenario_value=_number(grid.configured_scenario_value),
            configured_value_change=_number(grid.configured_value_change),
            curve_price_axis=list(grid.price_curve_axis), heatmap_price_axis=list(grid.heatmap_price_axis),
            heatmap_iv_axis=list(grid.heatmap_iv_axis), price_curve=[_point_payload(p) for p in grid.price_curve],
            heatmap_rows=[[_point_payload(p) for p in row] for row in grid.heatmap_rows],
        ),
    )


def _valuation(a: ArchivedValuation, request: ArchivedValuationRequest) -> ValuationDTO:
    payload = _valuation_payload(a, request)
    return ValuationDTO(slice_id=SLICE_ID, **payload)


class _HttpRejected(Exception):
    def __init__(self, status: int, reason: ReasonCode) -> None:
        self.status = status
        self.reason = reason


async def _read_json_request(request: Request) -> dict[str, Any]:
    media = request.headers.getlist("content-type")
    encoding = request.headers.getlist("content-encoding")
    if len(media) != 1 or media[0].strip().lower() not in (
        "application/json", "application/json; charset=utf-8", "application/json;charset=utf-8",
    ) or (encoding and encoding != ["identity"]):
        raise _HttpRejected(415, ReasonCode.UNSUPPORTED_MEDIA_TYPE)
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (lengths and (not lengths[0].isascii() or not lengths[0].isdecimal())):
        raise _HttpRejected(422, ReasonCode.INVALID_REQUEST)
    if lengths and (len(lengths[0]) > 10 or int(lengths[0]) > _MAX_BODY):
        raise _HttpRejected(413, ReasonCode.PAYLOAD_TOO_LARGE)
    body = bytearray()
    try:
        async for chunk in request.stream():
            if len(body) + len(chunk) > _MAX_BODY:
                raise _HttpRejected(413, ReasonCode.PAYLOAD_TOO_LARGE)
            body.extend(chunk)
    except ClientDisconnect:
        raise _HttpRejected(422, ReasonCode.INVALID_REQUEST) from None
    try:
        decoded = _json_object(bytes(body))
    except (ValueError, UnicodeError, RecursionError, OverflowError):
        raise _HttpRejected(422, ReasonCode.INVALID_JSON) from None
    return decoded


async def _read_request(request: Request) -> ValuationRequestDTO:
    decoded = await _read_json_request(request)
    try:
        return ValuationRequestDTO.model_validate(decoded, strict=True)
    except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
        raise _HttpRejected(422, ReasonCode.INVALID_REQUEST) from None


def _failure(cid: str, reason: ReasonCode) -> ApiEnvelope:
    return ApiEnvelope(calculation_id=cid, status="UNAVAILABLE", reason_code=reason)


def _response(result: ApiEnvelope, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS)
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429:
        headers["Retry-After"] = "1"
    return JSONResponse(result.model_dump(mode="json"), status_code=status, headers=headers)


def create_research_app(
    source: ReviewedArchiveSource, *, bearer_token: str, clock: Callable[[], datetime] | None = None,
    worker_lock: LockType | None = None,
) -> FastAPI:
    """Compose a local ASGI research app; the caller owns secure configuration.

    One in-flight calculation per app instance (including input coverage
    verification). Worker lock is held until the worker actually exits,
    even if the HTTP client disconnects. No deployment, CORS or trading routes.
    """
    if (
        not isinstance(bearer_token, str) or not 32 <= len(bearer_token) <= 256
        or not bearer_token.isascii() or not bearer_token.isprintable()
        or any(c.isspace() for c in bearer_token)
    ):
        raise ValueError("Explicit nonempty research bearer token required")
    if (
        source.selection.trade_date != _DAY
        or tuple(leg.option_key.symbol for leg in source.selection.legs) != _SYMBOLS
        or source.approval.packet_sha256 != _PIN
    ):
        raise ValueError("Unsupported source scope")
    expected = bearer_token.encode("ascii")
    now = clock if clock is not None else lambda: datetime.now(timezone.utc)
    lock = worker_lock if worker_lock is not None else Lock()
    app = FastAPI(
        title="OptionMaster Archived Research API", version="1.0",
        docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False,
    )
    security = HTTPBearer(auto_error=False, scheme_name="ResearchBearer")

    def authorized(request: Request, credentials: HTTPAuthorizationCredentials | None) -> bool:
        if len(request.headers.getlist("authorization")) != 1 or credentials is None:
            return False
        token = credentials.credentials
        return (
            credentials.scheme.lower() == "bearer" and token.isascii()
            and len(token) <= 256 and compare_digest(token.encode("ascii"), expected)
        )

    def execute(cid: str, operation: Callable[[], ApiEnvelope]) -> ApiEnvelope:
        if not lock.acquire(blocking=False):
            return _failure(cid, ReasonCode.BUSY)
        try:
            return operation()
        except Exception:
            # A computation/serialization defect is not successful valuation.
            # Do not include exception text, request data or credentials.
            return _failure(cid, ReasonCode.INTERNAL_ERROR)
        finally:
            lock.release()

    async def run(cid: str, operation: Callable[[], ApiEnvelope]) -> JSONResponse:
        result = await run_in_threadpool(execute, cid, operation)
        return _response(result, 429 if result.reason_code is ReasonCode.BUSY else
                         500 if result.reason_code is ReasonCode.INTERNAL_ERROR else 200)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        reason = {
            404: ReasonCode.NOT_FOUND, 405: ReasonCode.METHOD_NOT_ALLOWED,
            401: ReasonCode.UNAUTHORIZED,
        }.get(exc.status_code, ReasonCode.INTERNAL_ERROR)
        return _response(_failure(uuid4().hex, reason), exc.status_code)

    error_responses: dict[int | str, dict[str, Any]] = {
        401: {"model": ApiEnvelope, "description": "Bearer authentication required"},
        422: {"model": ApiEnvelope, "description": "Invalid request"},
        429: {"model": ApiEnvelope, "description": "Local computation busy"},
        500: {"model": ApiEnvelope, "description": "Internal computation failure"},
    }

    @app.get("/api/v1/research/coverage", response_model=ApiEnvelope, responses=error_responses)
    async def coverage(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    ) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return _response(_failure(cid, ReasonCode.UNAUTHORIZED), 401)
        if request.query_params:
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)

        def operation() -> ApiEnvelope:
            admitted = admit_verified_slice(source.selection, source.packet, source.evidence, source.approval, now())
            if admitted.status is not SliceStatus.ADMITTED_REFERENCE or admitted.slice is None:
                return _failure(cid, _reason(admitted.reason_code))
            a = admitted.slice
            if a.basis.price_basis is None:
                return _failure(cid, ReasonCode.ARCHIVE_UNAVAILABLE)
            return ApiEnvelope(calculation_id=cid, status="OK", coverage=CoverageDTO(
                slice_id=SLICE_ID, trade_date=a.trade_date, data_observed_at=a.observed_at,
                admitted_at=a.admitted_at, price_basis=a.basis.price_basis.value,
                requested_legs=len(source.selection.legs), admitted_legs=len(a.legs),
                legs=[_coverage_leg(leg) for leg in a.legs], source_versions=_versions(a),
            ))

        return await run(cid, operation)

    @app.post(
        "/api/v1/research/valuations", response_model=ApiEnvelope,
        responses={**error_responses, 413: {"model": ApiEnvelope}, 415: {"model": ApiEnvelope}},
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": ValuationRequestDTO.model_json_schema()},
        }}},
    )
    async def valuation(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    ) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return _response(_failure(cid, ReasonCode.UNAUTHORIZED), 401)
        if request.query_params:
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        try:
            body = await _read_request(request)
        except _HttpRejected as exc:
            return _response(_failure(cid, exc.reason), exc.status)
        if set(body.positions) != set(_SYMBOLS):
            return _response(_failure(cid, ReasonCode.SLICE_SCOPE_MISMATCH), 422)
        try:
            scenario_at = datetime.fromisoformat(body.scenario_at).astimezone(_ZONE)
        except (ValueError, OverflowError):
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        domain_request = ArchivedValuationRequest(
            source.selection, tuple(body.positions[symbol] for symbol in _SYMBOLS),
            scenario_at,
            tuple(body.curve_price_shocks), tuple(body.heatmap_price_shocks), tuple(body.heatmap_iv_shocks),
        )

        def operation() -> ApiEnvelope:
            result = evaluate_archived_valuation(
                domain_request, source.packet, source.evidence, source.approval,
                source.rate_artifact, source.policy, now(),
            )
            if result.status is not ResearchValuationStatus.OK or result.valuation is None:
                return _failure(cid, _reason(result.reason_code))
            return ApiEnvelope(calculation_id=cid, status="OK", valuation=_valuation(result.valuation, domain_request))

        return await run(cid, operation)

    return app
