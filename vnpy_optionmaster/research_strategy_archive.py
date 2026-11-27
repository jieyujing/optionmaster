"""D12 adapter over the exact independently approved immutable D09 archive.

No ClickHouse, discovery admission, or new source approval. Positive price
domain is the existing CU research model's mathematical domain, not a claim
about exchange-wide price limits or all futures. The D05 nominal clock is a
hypothetical end-of-day convention, never an exercise cutoff attestation.
"""
from datetime import date, datetime, time, timezone
from hashlib import sha256
import json
from pathlib import Path
from types import MappingProxyType
from zoneinfo import ZoneInfo

from vnpy_optionmaster.research_archived_valuation import _rate
from vnpy_optionmaster.research_catalog_api import CATALOG_PIN, _coverage, load_reviewed_catalog
from vnpy_optionmaster.research_strategy_api_dto import (
    CapabilityDTO, ChainRowDTO, CoverageDayDTO, CoverageMonthDTO, CoverageProductDTO,
    DomainDTO, OptionQuoteDTO, SnapshotDTO, StrategyChainDTO, StrategyCoverageDTO,
    StrategyRateEvidenceDTO, UnderlyingQuoteDTO,
)
from vnpy_optionmaster.research_strategy_payoff import StrategyQuoteSnapshot, StrategyUnavailable

_ZONE = ZoneInfo("Asia/Shanghai")
_EXCHANGE = "SHFE"
_PRODUCT = "CU"
_QUOTE_UNIT = "CNY_PER_TON"
_ELIGIBLE = CapabilityDTO(status="ELIGIBLE", reason=None)
_DOMAIN = DomainDTO(status="VERIFIED", min_price=0., max_price=None, min_inclusive=False, max_inclusive=False)


def _digest(value: object) -> str:
    return sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _nominal(day: date) -> str:
    return datetime.combine(day, time(15), tzinfo=_ZONE).isoformat()


class ReviewedStrategyArchive:
    """Materialize only immutable pinned data, then return isolated copies.

    D09's admission seam validates the whole roster in bounded groups. Snapshot
    IDs are day + exact source versions + adapter policy, not live observations.
    Native terms identity derives from independently verified dated economics
    and source alias; the dated effective versions remain in the snapshot digest.
    """

    def __init__(self, root: Path) -> None:
        source = load_reviewed_catalog(root)
        read_at = datetime.now(timezone.utc)
        by_day: dict[str, StrategyQuoteSnapshot] = {}
        days: list[CoverageDayDTO] = []
        for day, (policy, raw_rate) in sorted(source.rates.items()):
            c = _coverage(source, day, read_at)
            if (c.source_versions.packet_sha256 != CATALOG_PIN or c.price_basis != "SETTLEMENT"
                    or policy.clock_policy != "EOD_NOMINAL_1500_DATE_ONLY"
                    or policy.day_count_basis != "ACT/365F"
                    or policy.tree_steps != 128 or c.admitted_legs != c.requested_legs):
                raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
            rate = _rate(policy, raw_rate)
            versions = {x.artifact: x.sha256 for x in c.source_versions.artifacts}
            versions["D05_RATE"] = rate.artifact_sha256
            versions["D09_DATED_TERMS"] = _digest([{
                "source_symbol": leg.source_symbol, "effective_contract_version": leg.effective_contract_version,
                "official_record_id": leg.official_record_id,
            } for leg in c.legs])
            versions["D12_ARCHIVE_POLICY"] = _digest({
                "version": "D12_ARCHIVE_V1", "domain": "CU_POSITIVE_RESEARCH_PRICE_ONLY",
                "clock": policy.clock_policy, "rate": policy.rate_policy, "native_multiplier": 5,
            })
            snap = SnapshotDTO(snapshot_id=_digest({
                    "provider": "APPROVED_ARCHIVE", "exchange": _EXCHANGE, "product": _PRODUCT,
                    "day": day.isoformat(), "pin": CATALOG_PIN, "versions": versions,
                }), provider="APPROVED_ARCHIVE", exchange=_EXCHANGE, product=_PRODUCT, trade_date=day.isoformat(),
                observed_at=c.data_observed_at.isoformat(), read_at=read_at.isoformat(),
                admitted_at=c.admitted_at.isoformat(), reference_at=_nominal(day), source_pin=CATALOG_PIN,
                source_versions=versions, price_basis="SETTLEMENT", basis_fallback_reason=None,
                currency="CNY", production_valuation_ready=False)
            options: list[OptionQuoteDTO] = []
            underlyings: list[UnderlyingQuoteDTO] = []
            rates: list[StrategyRateEvidenceDTO] = []
            months: list[CoverageMonthDTO] = []
            for month in c.months:
                if month.admitted_contracts == 0:
                    months.append(CoverageMonthDTO(underlying_id=month.underlying_id, delivery_month=month.underlying_id,
                        discovered_contracts=month.discovered_contracts, admitted_contracts=0, status="DISCOVERY_ONLY", reason="CANDIDATE_ONLY"))
                    continue
                matched = [leg for leg in c.legs if leg.underlying_id == month.underlying_id]
                if (len(matched) != month.admitted_contracts or month.discovered_contracts != month.admitted_contracts
                        or month.underlying_settlement is None or month.underlying_settlement <= 0
                        or any(leg.contract_multiplier != 5 or leg.exercise_style != "AMERICAN"
                               or leg.underlying_settlement != month.underlying_settlement
                               or leg.expiry_date != month.expiry_date for leg in matched)):
                    raise StrategyUnavailable("SOURCE_INCOMPLETE")
                # D04 immutable official CU rule: one copper futures contract
                # (5 tonnes), one option on that contract, CNY per tonne.
                delivery = month.underlying_id.split(".", 1)[1]
                expiry_id = month.underlying_id + "|" + month.expiry_date.isoformat()
                underlyings.append(UnderlyingQuoteDTO(underlying_id=month.underlying_id, kind="FUTURE",
                    delivery_month=delivery, market_price=month.underlying_settlement, multiplier=5.,
                    quantity_unit="CONTRACTS", quote_unit=_QUOTE_UNIT, terms_version=_digest({
                        "id": month.underlying_id, "unit": _QUOTE_UNIT, "multiplier": 5., "kind": "FUTURE",
                    }), domain=_DOMAIN, eligibility=_ELIGIBLE))
                rates.append(StrategyRateEvidenceDTO(expiry_id=expiry_id, quote_date=rate.quote_date.isoformat(),
                    continuous_rate=rate.continuous_rate, policy_id=policy.rate_policy, source_pin=rate.artifact_sha256))
                for leg in matched:
                    term_identity = _digest({
                        "native_contract": leg.contract_id, "source_namespace": leg.source_namespace,
                        "source_exchange": leg.source_exchange, "source_symbol": leg.source_symbol,
                        "underlying": leg.underlying_id, "expiry": leg.expiry_date.isoformat(),
                        "strike": leg.strike, "multiplier": leg.contract_multiplier,
                        "exercise_style": leg.exercise_style, "right": leg.option_type,
                    })
                    options.append(OptionQuoteDTO(contract_id=leg.source_symbol, underlying_id=leg.underlying_id,
                        option_type=leg.option_type, strike=leg.strike, expiry_id=expiry_id,
                        expiry_date=leg.expiry_date.isoformat(), expiry_at=_nominal(leg.expiry_date),
                        expiry_clock="APPROVED_NOMINAL", exercise_style="AMERICAN", model="AMERICAN_BINOMIAL",
                        multiplier=leg.contract_multiplier, quote_unit=_QUOTE_UNIT, terms_version=term_identity,
                        market_price=leg.settlement, implied_volatility=None, payoff_eligibility=_ELIGIBLE,
                        model_eligibility=CapabilityDTO(status="UNAVAILABLE" if leg.at_intrinsic else "NOT_EVALUATED",
                            reason="IV_UNAVAILABLE" if leg.at_intrinsic else "NOT_EVALUATED")))
                months.append(CoverageMonthDTO(underlying_id=month.underlying_id, delivery_month=delivery,
                    discovered_contracts=month.discovered_contracts, admitted_contracts=len(matched), status="ADMITTED", reason=None))
            if len(options) != c.admitted_legs:
                raise StrategyUnavailable("SOURCE_INCOMPLETE")
            by_day[day.isoformat()] = StrategyQuoteSnapshot(snap, tuple(options), tuple(underlyings), tuple(rates))
            days.append(CoverageDayDTO(trade_date=day.isoformat(), status="ARCHIVE_ONLY", reason=None, months=months))
        if sum(len(s.options) for s in by_day.values()) != 198:
            raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
        self._days = MappingProxyType(by_day)
        self._ids = MappingProxyType({s.metadata.snapshot_id: s for s in by_day.values()})
        self._coverage = StrategyCoverageDTO(products=[CoverageProductDTO(exchange=_EXCHANGE, product=_PRODUCT,
            latest_complete_trade_date=None, days=days, spot_status="UNAVAILABLE", spot_reason="SCOPE_UNAVAILABLE")],
            next_cursor=None)

    def load_snapshot(self, snapshot_id: str) -> StrategyQuoteSnapshot | None:
        # Immutable independent archive is the sole approved local reuse case.
        source = self._ids.get(snapshot_id)
        if source is None:
            return None
        return StrategyQuoteSnapshot(source.metadata.model_copy(deep=True), tuple(q.model_copy(deep=True) for q in source.options),
            tuple(u.model_copy(deep=True) for u in source.underlyings), tuple(r.model_copy(deep=True) for r in source.rates))

    def coverage(self, exchange: str | None, product: str | None, cursor: str | None) -> StrategyCoverageDTO:
        if cursor is not None:
            raise StrategyUnavailable("INVALID_REQUEST")
        if (exchange, product) not in ((None, None), (_EXCHANGE, _PRODUCT)):
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        return self._coverage.model_copy(deep=True)

    def chain(self, exchange: str, product: str, trade_date: str, underlying_id: str,
              snapshot_id: str | None, cursor: str | None) -> StrategyChainDTO:
        if cursor is not None:
            raise StrategyUnavailable("INVALID_REQUEST")
        if (exchange, product) != (_EXCHANGE, _PRODUCT):
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        source = self._days.get(trade_date)
        if source is None:
            raise StrategyUnavailable("SCOPE_UNAVAILABLE")
        if snapshot_id is not None and source.metadata.snapshot_id != snapshot_id:
            raise StrategyUnavailable("SNAPSHOT_STALE")
        underlying = next((u for u in source.underlyings if u.underlying_id == underlying_id), None)
        if underlying is None:
            raise StrategyUnavailable("CANDIDATE_ONLY")
        selected = [q for q in source.options if q.underlying_id == underlying_id]
        by_strike: dict[float, dict[str, OptionQuoteDTO]] = {}
        for q in selected:
            rights = by_strike.setdefault(q.strike, {})
            if q.option_type in rights:
                raise StrategyUnavailable("SOURCE_AMBIGUOUS")
            rights[q.option_type] = q
        return StrategyChainDTO(snapshot=source.metadata, underlying=underlying,
            rows=[ChainRowDTO(strike=strike, call=rights.get("CALL"), put=rights.get("PUT"))
                  for strike, rights in sorted(by_strike.items())], next_cursor=None).model_copy(deep=True)
