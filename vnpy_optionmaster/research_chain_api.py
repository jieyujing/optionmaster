"""D08 local archived chain API v2; separate from unchanged v1 contract.

The composition root owns the exact operator-approved packet, evidence and
D05 rate policy. No HTTP-supplied source/credentials, DB access or orders.
"""
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
    _HEADERS, _HttpRejected, _coverage_leg, _json_object, _read_bounded,
    _read_json_request, _reason, _valuation_payload, load_reviewed_archive,
)
from vnpy_optionmaster.research_api_dto import EvidenceDigestDTO, ReasonCode
from vnpy_optionmaster.research_archived_valuation import ArchivedValuationPolicy, ArchivedValuationRequest
from vnpy_optionmaster.research_chain_api_dto import (
    CHAIN_SLICE_ID, ChainApiEnvelope, ChainCoverageDTO, ChainCoverageLegDTO,
    ChainEvidenceDTO, ChainMonthDTO, ChainValuationDTO, ChainValuationRequestDTO,
)
from vnpy_optionmaster.research_chain_archive import (
    DAY, OBSERVATION, OFFICIAL, ChainEvidenceError, _check_approval, _official_catalog,
    admit_chain_selection, build_chain_candidate, selection_for_symbols,
)
from vnpy_optionmaster.research_chain_valuation import evaluate_chain_valuation
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import SliceApproval, SliceStatus, _same

CHAIN_PIN = "9216e5598c11ef2736c353dddd08bd7c2efedf742935a9a7ee9afd8383ee8846"
_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class ReviewedChainSource:
    symbols: tuple[str, ...]
    packet: bytes
    evidence: Mapping[str, bytes]
    approval: SliceApproval
    rate_artifact: bytes
    policy: ArchivedValuationPolicy

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


def load_reviewed_chain(d04_root: Path, d05_root: Path, d08_root: Path) -> ReviewedChainSource:
    """Load explicit local trusted roots and independently supplied pin."""
    cfg = _json_object(_read_bounded(d08_root / "operator_chain_approval.json", 65536))
    if (
        cfg["schema"] != "optionmaster.d08.archived_chain_approval.v1"
        or cfg["packet_sha256"] != CHAIN_PIN
        or cfg["approval_scope"] != "ARCHIVED_HISTORICAL_CHAIN_INPUTS_ONLY"
        or cfg["trade_date"] != DAY.isoformat() or cfg["underlying_symbol"] != "SHFE.cu2211"
        or cfg["approved_contract_count"] != 66
        or cfg["min_selection_legs"] != 2 or cfg["max_selection_legs"] != 8
        or cfg["production_tls_approved"] is not False or cfg["production_valuation_ready"] is not False
    ):
        raise ValueError("Unsupported approved chain scope")
    approval = SliceApproval(cfg["packet_sha256"], cfg["approved_by"], datetime.fromisoformat(cfg["approved_at"]))
    evidence = {name: _read_bounded(d04_root / name, 4 * 1048576) for name in OFFICIAL}
    evidence[OBSERVATION] = _read_bounded(d08_root / OBSERVATION, 4 * 1048576)
    packet = _read_bounded(d08_root / "chain_candidate.json", 1048576)
    candidate = build_chain_candidate(evidence)
    if not _same(candidate, _json_object(packet)):
        raise ValueError("Approved chain packet mismatch")
    _check_approval(candidate, approval, datetime.now(timezone.utc))
    original = load_reviewed_archive(d04_root, d05_root)
    return ReviewedChainSource(
        tuple(b["raw_terms"]["symbol"] for b in candidate["bindings"]),
        packet, evidence, approval, original.rate_artifact, original.policy,
    )


def _coverage(source: ReviewedChainSource, read_at: datetime) -> ChainCoverageDTO:
    candidate = build_chain_candidate(source.evidence)
    if not _same(candidate, _json_object(source.packet)):
        raise ChainEvidenceError("PACKET_DIGEST_MISMATCH")
    _check_approval(candidate, source.approval, read_at)
    by_symbol = {b["raw_terms"]["symbol"]: b for b in candidate["bindings"]}
    if tuple(by_symbol) != source.symbols:
        raise ChainEvidenceError("SLICE_SCOPE_MISMATCH")
    legs: list[ChainCoverageLegDTO] = []
    # Every returned contract is genuinely re-admitted through D02/D03/D01.
    # D01 supports max 8 selected legs; 66 = 8 * 8 + 2.
    for offset in range(0, len(source.symbols), 8):
        keys = source.symbols[offset:offset + 8]
        result = admit_chain_selection(selection_for_symbols(keys), source.packet, source.evidence,
                                       source.approval, read_at)
        a = result.slice
        if result.status is not SliceStatus.ADMITTED_REFERENCE or a is None:
            raise ChainEvidenceError(result.reason_code or "ARCHIVE_UNAVAILABLE")
        for reviewed in a.legs:
            t = reviewed.contract.terms
            b = by_symbol[reviewed.raw_terms.option_key.symbol]
            o = b["official"]
            direction = 1 if t.option_type == "CALL" else -1
            intrinsic = max(0.0, direction * (o["underlying_settlement"] - t.strike))
            legs.append(ChainCoverageLegDTO(
                **_coverage_leg(reviewed).model_dump(),
                settlement=o["settlement"], close=o["close"],
                underlying_settlement=o["underlying_settlement"], volume=o["volume"],
                open_interest=o["open_interest"],
                effective_contract_version=t.contract_version,
                official_record_id=t.evidence.record_id, at_intrinsic=o["settlement"] <= intrinsic,
            ))
    official, _ = _official_catalog(source.evidence)
    months: list[ChainMonthDTO] = []
    for month, count in sorted(candidate["official_coverage"].items()):
        expiry = {r["expiry"] for r in official if r["underlying_native"] == month}
        if len(expiry) != 1:
            raise ChainEvidenceError("OFFICIAL_EXPIRY_AMBIGUOUS")
        months.append(ChainMonthDTO(
            underlying_id="SHFE." + month, expiry_date=date.fromisoformat(next(iter(expiry))),
            discovered_contracts=count, admitted_contracts=sum(leg.underlying_id == "SHFE." + month for leg in legs),
        ))
    return ChainCoverageDTO(
        slice_id=CHAIN_SLICE_ID, trade_date=DAY,
        data_observed_at=datetime.fromisoformat(candidate["observed_at"]),
        approved_at=source.approval.approved_at, admitted_at=read_at,
        requested_legs=len(source.symbols), admitted_legs=len(legs), months=months, legs=legs,
        source_versions=ChainEvidenceDTO(
            packet_sha256=source.approval.packet_sha256,
            artifacts=[EvidenceDigestDTO(artifact=n, sha256=d) for n, d in sorted(candidate["artifact_sha256"].items())],
        ),
    )


def _failure(cid: str, reason: ReasonCode) -> ChainApiEnvelope:
    return ChainApiEnvelope(calculation_id=cid, status="UNAVAILABLE", reason_code=reason)


def _response(result: ChainApiEnvelope, status: int = 200) -> JSONResponse:
    headers = dict(_HEADERS)
    if status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if status == 429:
        headers["Retry-After"] = "1"
    return JSONResponse(result.model_dump(mode="json"), status_code=status, headers=headers)


def _chain_reason(code: str | None) -> ReasonCode:
    if code and (code.startswith(("CHAIN_", "OFFICIAL_", "WIRE_", "SOURCE_"))
                 or code in ("DUPLICATE_SOURCE_ROW", "INCOMPLETE_CHAIN_OBSERVATION")):
        return ReasonCode.ARCHIVE_UNAVAILABLE
    return _reason(code)


def create_chain_research_app(
    source: ReviewedChainSource, *, bearer_token: str,
    clock: Callable[[], datetime] | None = None, worker_lock: LockType | None = None,
) -> FastAPI:
    """Local v2 ASGI factory. Caller may share its worker lock with v1."""
    if (
        not isinstance(bearer_token, str) or not 32 <= len(bearer_token) <= 256
        or not bearer_token.isascii() or not bearer_token.isprintable() or any(c.isspace() for c in bearer_token)
    ):
        raise ValueError("Explicit nonempty research bearer token required")
    if (source.approval.packet_sha256 != CHAIN_PIN or len(source.symbols) != 66
            or tuple(sorted(set(source.symbols))) != source.symbols):
        raise ValueError("Unsupported source scope")
    expected = bearer_token.encode("ascii")
    now = clock if clock is not None else lambda: datetime.now(timezone.utc)
    lock = worker_lock if worker_lock is not None else Lock()
    app = FastAPI(title="OptionMaster Archived Chain API", version="2.0",
                  docs_url=None, redoc_url=None, openapi_url=None, redirect_slashes=False)
    security = HTTPBearer(auto_error=False, scheme_name="ResearchBearer")

    def authorized(request: Request, credentials: HTTPAuthorizationCredentials | None) -> bool:
        if len(request.headers.getlist("authorization")) != 1 or credentials is None:
            return False
        token = credentials.credentials
        return (credentials.scheme.lower() == "bearer" and token.isascii()
                and len(token) <= 256 and compare_digest(token.encode("ascii"), expected))

    def execute(cid: str, operation: Callable[[], ChainApiEnvelope]) -> ChainApiEnvelope:
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

    async def run(cid: str, operation: Callable[[], ChainApiEnvelope]) -> JSONResponse:
        result = await run_in_threadpool(execute, cid, operation)
        return _response(result, 429 if result.reason_code is ReasonCode.BUSY else
                         500 if result.reason_code is ReasonCode.INTERNAL_ERROR else 200)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        reason = {404: ReasonCode.NOT_FOUND, 405: ReasonCode.METHOD_NOT_ALLOWED,
                  401: ReasonCode.UNAUTHORIZED}.get(exc.status_code, ReasonCode.INTERNAL_ERROR)
        return _response(_failure(uuid4().hex, reason), exc.status_code)

    error_responses: dict[int | str, dict[str, Any]] = {
        401: {"model": ChainApiEnvelope, "description": "Bearer authentication required"},
        422: {"model": ChainApiEnvelope, "description": "Invalid request"},
        429: {"model": ChainApiEnvelope, "description": "Local computation busy"},
        500: {"model": ChainApiEnvelope, "description": "Internal computation failure"},
    }

    @app.get("/api/v2/research/coverage", response_model=ChainApiEnvelope, responses=error_responses)
    async def coverage(
        request: Request, credentials: Annotated[HTTPAuthorizationCredentials | None, Security(security)],
    ) -> JSONResponse:
        cid = uuid4().hex
        if not authorized(request, credentials):
            return _response(_failure(cid, ReasonCode.UNAUTHORIZED), 401)
        if request.query_params:
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        return await run(cid, lambda: ChainApiEnvelope(calculation_id=cid, status="OK", coverage=_coverage(source, now())))

    @app.post(
        "/api/v2/research/valuations", response_model=ChainApiEnvelope,
        responses={**error_responses, 413: {"model": ChainApiEnvelope}, 415: {"model": ChainApiEnvelope}},
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/json": {"schema": ChainValuationRequestDTO.model_json_schema()},
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
            body = ChainValuationRequestDTO.model_validate(await _read_json_request(request), strict=True)
            scenario_at = datetime.fromisoformat(body.scenario_at).astimezone(_ZONE)
        except _HttpRejected as exc:
            return _response(_failure(cid, exc.reason), exc.status)
        except (ValidationError, ValueError, TypeError, OverflowError, RecursionError):
            return _response(_failure(cid, ReasonCode.INVALID_REQUEST), 422)
        if not set(body.positions) <= set(source.symbols):
            return _response(_failure(cid, ReasonCode.SLICE_SCOPE_MISMATCH), 422)
        # Order is canonical packet order, not untrusted JSON property order.
        symbols = tuple(s for s in source.symbols if s in body.positions)
        domain = ArchivedValuationRequest(
            selection_for_symbols(symbols), tuple(body.positions[s] for s in symbols), scenario_at,
            tuple(body.curve_price_shocks), tuple(body.heatmap_price_shocks), tuple(body.heatmap_iv_shocks),
        )

        def operation() -> ChainApiEnvelope:
            result = evaluate_chain_valuation(domain, source.packet, source.evidence, source.approval,
                                              source.rate_artifact, source.policy, now())
            if result.status is not ResearchValuationStatus.OK or result.valuation is None:
                return _failure(cid, _chain_reason(result.reason_code))
            return ChainApiEnvelope(calculation_id=cid, status="OK",
                                    valuation=ChainValuationDTO(slice_id=CHAIN_SLICE_ID, **_valuation_payload(result.valuation, domain)))

        return await run(cid, operation)

    return app
