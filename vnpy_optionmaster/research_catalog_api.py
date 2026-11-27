"""D09 local authenticated multi-date, cross-expiry archived research API."""
from _thread import LockType
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from secrets import compare_digest
from threading import Lock
from types import MappingProxyType
from typing import Annotated, Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from vnpy_optionmaster.research_api import (
    _HEADERS, _HttpRejected, _coverage_leg, _greeks, _json_object, _number,
    _point_payload, _read_bounded, _read_json_request,
)
from vnpy_optionmaster.research_api_dto import (
    EvidenceDigestDTO, RateDTO, ReasonCode, SourceVersionsDTO, UnderlyingExposureDTO, UnitsDTO,
)
from vnpy_optionmaster.research_archived_valuation import ArchivedValuationPolicy
from vnpy_optionmaster.research_chain_api import _chain_reason
from vnpy_optionmaster.research_chain_api_dto import ChainCoverageLegDTO, ChainEvidenceDTO, ChainGridDTO
from vnpy_optionmaster.research_chain_archive import ChainEvidenceError, _check_approval, _official_catalog, require
from vnpy_optionmaster.research_catalog_api_dto import (
    CATALOG_ID, CatalogApiEnvelope, CatalogCoverageDTO, CatalogExpiryDTO, CatalogLegDTO,
    CatalogMonthDTO, CatalogValuationDTO, CatalogValuationRequestDTO,
)
from vnpy_optionmaster.research_catalog_valuation import (
    CatalogValuation, CatalogValuationRequest, evaluate_catalog_valuation, load_pilot_rate,
)
from vnpy_optionmaster.research_multi_chain_archive import (
    CatalogChainSource, CatalogSelection, admit_catalog_selection, build_catalog_candidate,
    load_pilot_sources, _catalog, _children,
)
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceStatus, _same

CATALOG_PIN = "2c57d7df89e87db37749d4217c68117bd1d3ccc4c332bf1cfa41b4fb28264fea"
_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ReviewedCatalogSource:
    sources: tuple[CatalogChainSource, ...]
    packet: bytes
    approval: SliceApproval
    rates: Mapping[date, tuple[ArchivedValuationPolicy, bytes]]

    def __post_init__(self) -> None:
        object.__setattr__(self, "rates", MappingProxyType(dict(self.rates)))


def load_reviewed_catalog(root: Path) -> ReviewedCatalogSource:
    """Read only the server-owned reviewed archive and exact independent approval."""
    cfg = _json_object(_read_bounded(root / "m1-d09/operator_catalog_approval.json", 65536))
    sources = load_pilot_sources(root)
    packet = _read_bounded(root / "m1-d09/catalog_candidate.json", 1048576)
    candidate = build_catalog_candidate(sources)
    dates = sorted({s.scope.day for s in sources})
    if (
        cfg["schema"] != "optionmaster.d09.archived_catalog_approval.v1"
        or cfg["packet_sha256"] != CATALOG_PIN
        or cfg["approval_scope"] != "ARCHIVED_HISTORICAL_CATALOG_INPUTS_ONLY"
        or cfg["approved_contract_dates"] != 198 or candidate["matched_contract_dates"] != 198
        or cfg["approved_trade_dates"] != [d.isoformat() for d in dates]
        or cfg["min_selection_legs"] != 2 or cfg["max_selection_legs"] != 8
        or cfg["production_tls_approved"] is not False or cfg["production_valuation_ready"] is not False
        or not _same(candidate, _json_object(packet))
    ):
        raise ValueError("Unsupported approved catalog scope")
    approval = SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"]))
    _check_approval(candidate, approval, datetime.now(timezone.utc))
    return ReviewedCatalogSource(sources, packet, approval, {d: load_pilot_rate(root, d) for d in dates})


def _artifacts(candidate: dict[str, Any]) -> list[EvidenceDigestDTO]:
    return [
        EvidenceDigestDTO(artifact=c["trade_date"] + "/" + c["underlying"] + "/" + name, sha256=digest)
        for c in candidate["chains"] for name, digest in sorted(c["artifact_sha256"].items())
    ]


def _review(source: ReviewedCatalogSource, read_at: datetime) -> tuple[tuple[tuple[CatalogChainSource, dict[str, Any]], ...], dict[str, Any]]:
    entries = _children(source.sources)
    candidate = _catalog(entries)
    require(_same(candidate, _json_object(source.packet)), "PACKET_DIGEST_MISMATCH")
    _check_approval(candidate, source.approval, read_at)
    return entries, candidate


def _coverage(source: ReviewedCatalogSource, day: date, read_at: datetime) -> CatalogCoverageDTO:
    entries, candidate = _review(source, read_at)
    dated = tuple((src, child) for src, child in entries if src.scope.day == day)
    require(bool(dated), "SLICE_SCOPE_MISMATCH")
    by_symbol = {b["raw_terms"]["symbol"]: b for _, child in dated for b in child["bindings"]}
    symbols = tuple(sorted(by_symbol))
    legs: list[ChainCoverageLegDTO] = []
    # Verify every displayed admitted input through the same admission seam as
    # valuation. D01 requires >=2 legs; avoid a one-element final group.
    offset = 0
    while offset < len(symbols):
        left = len(symbols) - offset
        n = min(8, left)
        if left - n == 1:
            n -= 1
        keys = symbols[offset:offset + n]
        result = admit_catalog_selection(CatalogSelection(day, keys), source.packet, source.sources,
                                         source.approval, read_at)
        if result.status is not SliceStatus.ADMITTED_REFERENCE or result.portfolio is None:
            raise ChainEvidenceError(result.reason_code or "ARCHIVE_UNAVAILABLE")
        for reviewed in result.portfolio.legs:
            t = reviewed.contract.terms
            o = by_symbol[reviewed.raw_terms.option_key.symbol]["official"]
            intrinsic = max(0.0, (1 if t.option_type == "CALL" else -1) * (o["underlying_settlement"] - t.strike))
            legs.append(ChainCoverageLegDTO(
                **_coverage_leg(reviewed).model_dump(),
                settlement=o["settlement"], close=o["close"], underlying_settlement=o["underlying_settlement"],
                volume=o["volume"], open_interest=o["open_interest"], effective_contract_version=t.contract_version,
                official_record_id=t.evidence.record_id, at_intrinsic=o["settlement"] <= intrinsic,
            ))
        offset += n
    first, first_child = dated[0]
    official, _ = _official_catalog(first.evidence, first.scope)
    children = {src.scope.underlying: child for src, child in dated}
    months = []
    for month, count in sorted(first_child["official_coverage"].items()):
        expiry = {r["expiry"] for r in official if r["underlying_native"] == month}
        require(len(expiry) == 1, "OFFICIAL_EXPIRY_AMBIGUOUS")
        child = children.get(month)
        months.append(CatalogMonthDTO(
            underlying_id="SHFE." + month, expiry_date=date.fromisoformat(next(iter(expiry))),
            discovered_contracts=count, admitted_contracts=sum(leg.underlying_id == "SHFE." + month for leg in legs),
            child_packet_sha256=child["candidate_packet_sha256"] if child else None,
            observed_at=datetime.fromisoformat(child["observed_at"]) if child else None,
            underlying_settlement=child["underlying"]["settlement"] if child else None,
        ))
    return CatalogCoverageDTO(
        catalog_id=CATALOG_ID, trade_date=day, available_dates=sorted(source.rates),
        approved_contract_dates=candidate["matched_contract_dates"],
        data_observed_at=max(datetime.fromisoformat(c["observed_at"]) for _, c in dated),
        approved_at=source.approval.approved_at, admitted_at=read_at,
        requested_legs=len(symbols), admitted_legs=len(legs), months=months, legs=legs,
        source_versions=ChainEvidenceDTO(packet_sha256=source.approval.packet_sha256, artifacts=_artifacts(candidate)),
    )


def _payload(a: CatalogValuation, request: CatalogValuationRequest, source: ReviewedCatalogSource) -> CatalogValuationDTO:
    portfolio, grid, rate = a.portfolio, a.grid, a.rate
    legs = []
    for reviewed, mark, qty, solved, c in zip(
        portfolio.legs, portfolio.basis.legs, request.quantities, a.calibrations, grid.configured_contributions, strict=True,
    ):
        t = reviewed.contract.terms
        if t.option_contract_code != c.contract_id or solved.iv_result is None:
            raise ValueError("Incomplete model result")
        legs.append(CatalogLegDTO(
            **_coverage_leg(reviewed).model_dump(), expiry_id=c.expiry_id, baseline_time_to_expiry=c.baseline_time_to_expiry,
            quantity=qty, raw_contract_version=reviewed.raw_terms.contract_version,
            raw_exercise_style=reviewed.raw_terms.exercise_style, effective_contract_version=t.contract_version,
            official_record_id=t.evidence.record_id, official_source_uri=t.evidence.source_uri,
            official_artifact_sha256=t.evidence.content_sha256, terms_ingested_at=reviewed.raw_terms.ingested_at,
            option_ingested_at=mark.option_ingested_at, underlying_ingested_at=mark.underlying_ingested_at,
            option_source=mark.option_source, underlying_source=mark.underlying_source,
            market_option_price=mark.option_price, market_underlying_price=mark.underlying_price,
            implied_volatility=_number(solved.implied_volatility), iv_price_residual=_number(solved.iv_result.residual),
            baseline_value=_number(solved.baseline_value), baseline_greeks=_greeks(solved.baseline_greeks),
            scenario_iv=c.scenario_iv, scenario_time_to_expiry=c.scenario_time_to_expiry,
            scenario_value=c.scenario_value, value_change=c.value_change,
            scenario_greeks=_greeks(c.scenario_result.scenario_greeks),
        ))
    candidate = _json_object(source.packet)
    return CatalogValuationDTO(
        catalog_id=CATALOG_ID, trade_date=portfolio.trade_date, data_observed_at=portfolio.observed_at,
        approved_at=portfolio.approved_at, admitted_at=portfolio.admitted_at,
        reference_at=a.reference_at, scenario_at=a.scenario_at, clock_policy=a.policy.clock_policy,
        expiries=[CatalogExpiryDTO(
            expiry_id=e.expiry_id, underlying_id=e.underlying_id, expiry_date=e.expiry_date,
            nominal_expiry_at=e.nominal_expiry_at, baseline_time_to_expiry=e.baseline_time_to_expiry,
        ) for e in a.expiries],
        source_versions=SourceVersionsDTO(
            packet_sha256=portfolio.packet_sha256, registry_sha256=portfolio.registry_sha256, artifacts=_artifacts(candidate),
        ),
        child_packet_sha256=list(portfolio.child_packet_sha256),
        rate=RateDTO(quote_date=rate.quote_date, quoted_percent=rate.quoted_percent, tenor_years=rate.tenor_years,
                     continuous_rate=rate.continuous_rate, source_uri=rate.source_uri, artifact_sha256=rate.artifact_sha256,
                     convention=rate.convention),
        model=a.model.value, tree_steps=a.policy.tree_steps, units=UnitsDTO(),
        warnings=["ARCHIVED_NOT_FRESH", "RATE_PROXY", "NOMINAL_EOD_CLOCK", "NO_PUBLICATION_TIME_PIT",
                  "DISCRETIZATION_TOLERANCE_NOT_APPROVED", "PRODUCTION_NO_GO"],
        price_shocks_by_underlying=dict(request.price_shocks_by_underlying),
        iv_shocks_by_expiry=dict(request.iv_shocks_by_expiry), legs=legs,
        underlying_exposures=[UnderlyingExposureDTO(
            underlying_id=e.underlying_id, baseline_greeks=_greeks(e.baseline_greeks), scenario_greeks=_greeks(e.scenario_greeks),
        ) for e in a.exposures],
        grid=ChainGridDTO.model_validate(dict(
            baseline_value=_number(grid.baseline_value), configured_scenario_value=_number(grid.configured_scenario_value),
            configured_value_change=_number(grid.configured_value_change),
            curve_price_axis=list(grid.price_curve_axis), heatmap_price_axis=list(grid.heatmap_price_axis),
            heatmap_iv_axis=list(grid.heatmap_iv_axis), price_curve=[_point_payload(p) for p in grid.price_curve],
            heatmap_rows=[[_point_payload(p) for p in row] for row in grid.heatmap_rows],
        )),
    )


def _failure(cid: str, reason: ReasonCode) -> CatalogApiEnvelope:
    return CatalogApiEnvelope(calculation_id=cid, status="UNAVAILABLE", reason_code=reason)


def _response(result: CatalogApiEnvelope, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS)
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429:
        headers["Retry-After"] = "1"
    return JSONResponse(result.model_dump(mode="json"), status_code=status, headers=headers)


def create_catalog_research_app(
    source: ReviewedCatalogSource, *, bearer_token: str,
    clock: Callable[[], datetime] | None = None, worker_lock: LockType | None = None,
) -> FastAPI:
    if (not isinstance(bearer_token, str) or not 32 <= len(bearer_token) <= 256
            or not bearer_token.isascii() or not bearer_token.isprintable() or any(c.isspace() for c in bearer_token)):
        raise ValueError("Explicit research bearer token required")
    if source.approval.packet_sha256 != CATALOG_PIN or not source.rates:
        raise ValueError("Unsupported catalog source")
    expected = bearer_token.encode("ascii")
    now = clock if clock is not None else lambda: datetime.now(timezone.utc)
    lock = worker_lock if worker_lock is not None else Lock()
    app = FastAPI(title="OptionMaster Dated Research Catalog API", version="3.0",
                  docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    security = HTTPBearer(auto_error=False, scheme_name="ResearchBearer")

    def authorized(request: Request, credentials: HTTPAuthorizationCredentials | None) -> bool:
        if len(request.headers.getlist("authorization")) != 1 or credentials is None:
            return False
        token = credentials.credentials
        return (credentials.scheme.lower() == "bearer" and token.isascii()
                and len(token) <= 256 and compare_digest(token.encode("ascii"), expected))

    def execute(cid: str, operation: Callable[[], CatalogApiEnvelope]) -> CatalogApiEnvelope:
        if not lock.acquire(blocking=False):
            return _failure(cid, ReasonCode.BUSY)
        try:
            return operation()
        except ChainEvidenceError:
            return _failure(cid, ReasonCode.ARCHIVE_UNAVAILABLE)
        except Exception:
            return _failure(cid, ReasonCode.INTERNAL_ERROR)
        finally:
            lock.release()

    async def run(cid: str, operation: Callable[[], CatalogApiEnvelope]) -> JSONResponse:
        result = await run_in_threadpool(execute, cid, operation)
        return _response(result, 429 if result.reason_code is ReasonCode.BUSY else
                         500 if result.reason_code is ReasonCode.INTERNAL_ERROR else
                         422 if result.reason_code is ReasonCode.SLICE_SCOPE_MISMATCH else 200)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        reason = {404: ReasonCode.NOT_FOUND, 405: ReasonCode.METHOD_NOT_ALLOWED,
                  401: ReasonCode.UNAUTHORIZED}.get(exc.status_code, ReasonCode.INTERNAL_ERROR)
        return _response(_failure(uuid4().hex, reason), exc.status_code)

    errors: dict[int | str, dict[str, Any]] = {
        401: {"model": CatalogApiEnvelope}, 422: {"model": CatalogApiEnvelope},
        429: {"model": CatalogApiEnvelope}, 500: {"model": CatalogApiEnvelope},
    }

    @app.get("/api/v3/research/coverage", response_model=CatalogApiEnvelope, responses=errors,
             openapi_extra={"parameters": [{"name": "trade_date", "in": "query", "required": False,
                                           "schema": {"type": "string", "format": "date"}}]})
    async def coverage(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    ) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return _response(_failure(cid, ReasonCode.UNAUTHORIZED), 401)
        params = list(request.query_params.multi_items())
        try:
            if not params:
                day = min(source.rates)
            elif len(params) == 1 and params[0][0] == "trade_date":
                day = date.fromisoformat(params[0][1])
                if day.isoformat() != params[0][1]:
                    raise ValueError()
            else:
                raise ValueError()
        except ValueError:
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        if day not in source.rates:
            return _response(_failure(cid, ReasonCode.SLICE_SCOPE_MISMATCH), 422)
        return await run(cid, lambda: CatalogApiEnvelope(calculation_id=cid, status="OK", coverage=_coverage(source, day, now())))

    @app.post("/api/v3/research/valuations", response_model=CatalogApiEnvelope,
              responses={**errors, 413: {"model": CatalogApiEnvelope}, 415: {"model": CatalogApiEnvelope}},
              openapi_extra={"requestBody": {"required": True, "content": {
                  "application/json": {"schema": CatalogValuationRequestDTO.model_json_schema()},
              }}})
    async def valuation(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    ) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return _response(_failure(cid, ReasonCode.UNAUTHORIZED), 401)
        if request.query_params:
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        try:
            body = CatalogValuationRequestDTO.model_validate(await _read_json_request(request), strict=True)
            day = date.fromisoformat(body.trade_date)
            scenario_at = datetime.fromisoformat(body.scenario_at).astimezone(_ZONE)
        except _HttpRejected as exc:
            return _response(_failure(cid, exc.reason), exc.status)
        except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        if day not in source.rates:
            return _response(_failure(cid, ReasonCode.SLICE_SCOPE_MISMATCH), 422)
        symbols = tuple(sorted(body.positions))
        domain = CatalogValuationRequest(
            CatalogSelection(day, symbols), tuple(body.positions[s] for s in symbols), scenario_at,
            tuple(body.curve_price_shocks), tuple(body.heatmap_price_shocks), tuple(body.heatmap_iv_shocks),
            price_shocks_by_underlying=tuple(sorted(body.price_shocks_by_underlying.items())),
            iv_shocks_by_expiry=tuple(sorted(body.iv_shocks_by_expiry.items())),
        )

        def operation() -> CatalogApiEnvelope:
            policy, rate = source.rates[day]
            result = evaluate_catalog_valuation(domain, source.packet, source.sources, source.approval, rate, policy, now())
            if result.status is not ResearchValuationStatus.OK or result.valuation is None:
                return _failure(cid, _chain_reason(result.reason_code))
            return CatalogApiEnvelope(calculation_id=cid, status="OK", valuation=_payload(result.valuation, domain, source))

        return await run(cid, operation)

    return app
