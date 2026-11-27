"""M1-D12 T03 deterministic research payoff, independent of IV and live trading.

The source boundary supplies an independently admitted, coherent snapshot.
This module never accesses a database or recalibrates IV. Exact piecewise
arithmetic uses rational numbers derived from the admitted decimal inputs;
nonrepresentable wire results fail closed.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from math import isfinite
from typing import Literal

from pydantic import ValidationError

from vnpy_optionmaster.research_strategy_api_dto import (
    EntryDTO, LegContributionDTO, OptionQuoteDTO, PayoffAccountingDTO, Reason,
    RiskMetricDTO, RootIntervalDTO, SelectedLegDTO, SnapshotDTO, StrategyCurveDTO,
    StrategyOptionDTO, StrategyPayoffDTO, StrategyPayoffRequestDTO, StrategyPointDTO,
    StrategyRateEvidenceDTO, StrategyRiskDTO, StrategyUnderlyingDTO, UnderlyingQuoteDTO, ValuationRequestDTO,
)


class StrategyUnavailable(ValueError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class StrategyQuoteSnapshot:
    """Internal provider result, NOT evidence of admission on its own."""

    metadata: SnapshotDTO
    options: tuple[OptionQuoteDTO, ...]
    underlyings: tuple[UnderlyingQuoteDTO, ...]
    rates: tuple[StrategyRateEvidenceDTO, ...] = ()
    chain_adjustments: tuple[tuple[str, float | None], ...] = ()

    def pricing_adjustment(self, expiry_id: str) -> float | None:
        if "LOCAL_USER_ASSUMPTION" not in self.metadata.source_versions:
            return 0.0
        return dict(self.chain_adjustments).get(expiry_id)

    def __post_init__(self) -> None:
        if (len(dict(self.chain_adjustments)) != len(self.chain_adjustments)
                or any(value is not None and not isfinite(value) for _, value in self.chain_adjustments)
                or (self.chain_adjustments and "LOCAL_USER_ASSUMPTION" not in self.metadata.source_versions)):
            raise StrategyUnavailable("SOURCE_AMBIGUOUS")
        uids = {u.underlying_id for u in self.underlyings}
        ids = {o.contract_id for o in self.options}
        if len(uids) != len(self.underlyings) or len(ids) != len(self.options) or uids & ids:
            raise StrategyUnavailable("SOURCE_AMBIGUOUS")
        for option in self.options:
            if option.underlying_id not in uids:
                raise StrategyUnavailable("SOURCE_INCOMPLETE")
            if date.fromisoformat(option.expiry_date).isoformat() != option.expiry_date:
                raise StrategyUnavailable("TERMS_UNAVAILABLE")
            if option.expiry_at is not None:
                ValuationRequestDTO.market_clock(option.expiry_at)
                if option.expiry_at[:10] != option.expiry_date:
                    raise StrategyUnavailable("TERMS_UNAVAILABLE")


@dataclass(frozen=True)
class ResolvedLeg:
    entry: StrategyOptionDTO | StrategyUnderlyingDTO
    underlying: UnderlyingQuoteDTO
    option: OptionQuoteDTO | None
    pricing_adjustment: float | None = 0.0

    @property
    def instrument_id(self) -> str:
        return self.option.contract_id if self.option is not None else self.underlying.underlying_id

    @property
    def multiplier(self) -> float:
        return self.option.multiplier if self.option is not None else self.underlying.multiplier

    def dto(self) -> SelectedLegDTO:
        quote = self.option
        return SelectedLegDTO(
            quantity=self.entry.quantity, entry_price=self.entry.entry_price,
            entry_trade_date=self.entry.entry_trade_date, entry_snapshot_id=self.entry.entry_snapshot_id,
            entry_origin=self.entry.entry_origin, instrument_id=self.instrument_id,
            underlying_id=self.underlying.underlying_id, kind="OPTION" if quote is not None else "UNDERLYING",
            quantity_unit="CONTRACTS" if quote is not None else self.underlying.quantity_unit,
            expiry_id=quote.expiry_id if quote is not None else None,
            payoff_eligibility=quote.payoff_eligibility if quote is not None else self.underlying.eligibility,
            model_eligibility=quote.model_eligibility if quote is not None else self.underlying.eligibility,
            market_price=quote.market_price if quote is not None else self.underlying.market_price,
            multiplier=self.multiplier, quote_unit=quote.quote_unit if quote is not None else self.underlying.quote_unit,
            implied_volatility=quote.implied_volatility if quote is not None else None,
        )


@dataclass(frozen=True)
class ResolvedStrategy:
    request: StrategyPayoffRequestDTO
    snapshot: SnapshotDTO
    legs: tuple[ResolvedLeg, ...]
    underlyings: tuple[UnderlyingQuoteDTO, ...]


def _number(value: float | int) -> Fraction:
    if not isfinite(value):
        raise StrategyUnavailable("NUMERICAL_FAILURE")
    return Fraction(str(value))


def _wire(value: Fraction) -> float:
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise StrategyUnavailable("NUMERICAL_FAILURE") from exc
    if not isfinite(number):
        raise StrategyUnavailable("NUMERICAL_FAILURE")
    return number


def _admitted(option: OptionQuoteDTO | UnderlyingQuoteDTO) -> bool:
    eligibility = option.payoff_eligibility if isinstance(option, OptionQuoteDTO) else option.eligibility
    return eligibility.status == "ELIGIBLE" and option.market_price is not None


def _option_terms(option: OptionQuoteDTO, *, local_model_override: bool = False) -> tuple[object, ...]:
    return (
        option.contract_id, option.underlying_id, option.option_type, option.strike,
        option.expiry_id, option.expiry_date, option.expiry_at, option.expiry_clock,
        None if local_model_override else option.exercise_style,
        None if local_model_override else option.model, option.multiplier, option.quote_unit, option.terms_version,
    )


def _underlying_terms(underlying: UnderlyingQuoteDTO) -> tuple[object, ...]:
    return (
        underlying.underlying_id, underlying.kind, underlying.delivery_month, underlying.multiplier,
        underlying.quantity_unit, underlying.quote_unit, underlying.terms_version,
    )


def _price_in_domain(underlying: UnderlyingQuoteDTO, price: float) -> bool:
    domain = underlying.domain
    if domain.status != "VERIFIED":
        return False
    if domain.min_price is not None and (
        price < domain.min_price or (price == domain.min_price and not domain.min_inclusive)
    ):
        return False
    return not (domain.max_price is not None and (
        price > domain.max_price or (price == domain.max_price and not domain.max_inclusive)
    ))


def _entry_snapshot(entry: EntryDTO, current: StrategyQuoteSnapshot,
                    snapshots: Mapping[str, StrategyQuoteSnapshot]) -> StrategyQuoteSnapshot:
    old = snapshots.get(entry.entry_snapshot_id)
    if old is None or old.metadata.snapshot_id != entry.entry_snapshot_id:
        raise StrategyUnavailable("ENTRY_UNAVAILABLE")
    if old.metadata.trade_date != entry.entry_trade_date or entry.entry_trade_date > current.metadata.trade_date:
        raise StrategyUnavailable("ENTRY_DATE_INVALID")
    if (old.metadata.exchange, old.metadata.product, old.metadata.provider, old.metadata.currency) != (
        current.metadata.exchange, current.metadata.product, current.metadata.provider, current.metadata.currency,
    ):
        raise StrategyUnavailable("ENTRY_UNAVAILABLE")
    return old


def _check_entry(entry: EntryDTO, market_price: float | None) -> None:
    if market_price is None or (entry.entry_origin == "ADMITTED_MARK" and entry.entry_price != market_price):
        raise StrategyUnavailable("ENTRY_UNAVAILABLE")


def resolve_strategy(request: StrategyPayoffRequestDTO, current: StrategyQuoteSnapshot,
                     entry_snapshots: Mapping[str, StrategyQuoteSnapshot]) -> ResolvedStrategy:
    """Bind all client identities to server-owned current and original snapshots.

    A changed term/version, missing leg, invalid source or future entry is
    rejected as a whole; caller must retain its draft, not drop a leg.
    """
    if current.metadata.snapshot_id != request.snapshot_id:
        raise StrategyUnavailable("SNAPSHOT_STALE")
    option_map = {q.contract_id: q for q in current.options}
    underlying_map = {q.underlying_id: q for q in current.underlyings}
    selected: list[ResolvedLeg] = []
    referenced: dict[str, UnderlyingQuoteDTO] = {}

    for entry in request.options:
        option = option_map.get(entry.contract_id)
        if option is None:
            raise StrategyUnavailable("INSTRUMENT_UNAVAILABLE")
        underlying = underlying_map[option.underlying_id]
        if not _admitted(option) or not _admitted(underlying):
            raise StrategyUnavailable("MARK_UNAVAILABLE")
        if option.quote_unit != underlying.quote_unit:
            raise StrategyUnavailable("TERMS_UNAVAILABLE")
        if current.metadata.trade_date >= option.expiry_date:
            raise StrategyUnavailable("EXPIRED_SCENARIO")
        old = _entry_snapshot(entry, current, entry_snapshots)
        original = next((q for q in old.options if q.contract_id == entry.contract_id), None)
        original_underlying = next((q for q in old.underlyings if q.underlying_id == option.underlying_id), None)
        if (original is None or original_underlying is None or not _admitted(original)
                or not _admitted(original_underlying)):
            raise StrategyUnavailable("ENTRY_UNAVAILABLE")
        # Only the explicitly user-authorized immutable local cache permits a
        # model change without changing economic contract identity or entry cost.
        local_model_override = (
            old.metadata.source_pin == current.metadata.source_pin
            and "LOCAL_USER_ASSUMPTION" in old.metadata.source_versions
            and "LOCAL_USER_ASSUMPTION" in current.metadata.source_versions
            and original.terms_version.startswith("LOCAL_CACHE:")
            and option.terms_version.startswith("LOCAL_CACHE:")
        )
        if (_option_terms(original, local_model_override=local_model_override)
                != _option_terms(option, local_model_override=local_model_override)
                or _underlying_terms(original_underlying) != _underlying_terms(underlying)):
            raise StrategyUnavailable("TERMS_UNAVAILABLE")
        _check_entry(entry, original.market_price)
        if underlying.domain.status == "VERIFIED" and (
            underlying.market_price is None or not _price_in_domain(underlying, underlying.market_price)
        ):
            raise StrategyUnavailable("DOMAIN_UNAVAILABLE")
        referenced[underlying.underlying_id] = underlying
        selected.append(ResolvedLeg(entry, underlying, option, current.pricing_adjustment(option.expiry_id)))

    for underlying_entry in request.underlyings:
        referenced_underlying = referenced.get(underlying_entry.underlying_id)
        if referenced_underlying is None:
            raise StrategyUnavailable("INSTRUMENT_UNAVAILABLE")
        underlying = referenced_underlying
        if underlying_entry.quantity_unit != underlying.quantity_unit:
            raise StrategyUnavailable("TERMS_UNAVAILABLE")
        old = _entry_snapshot(underlying_entry, current, entry_snapshots)
        original_underlying = next((q for q in old.underlyings if q.underlying_id == underlying.underlying_id), None)
        if original_underlying is None or not _admitted(original_underlying):
            raise StrategyUnavailable("ENTRY_UNAVAILABLE")
        if _underlying_terms(original_underlying) != _underlying_terms(underlying):
            raise StrategyUnavailable("TERMS_UNAVAILABLE")
        _check_entry(underlying_entry, original_underlying.market_price)
        if not _price_in_domain(original_underlying, underlying_entry.entry_price):
            raise StrategyUnavailable("ENTRY_UNAVAILABLE")
        selected.append(ResolvedLeg(underlying_entry, underlying, None))

    if request.reference_underlying_id not in referenced or set(request.price_shocks_by_underlying) - referenced.keys():
        raise StrategyUnavailable("INVALID_SCENARIO")
    if len({u.kind for u in referenced.values()}) != 1:
        raise StrategyUnavailable("TERMS_UNAVAILABLE")
    return ResolvedStrategy(request, current.metadata, tuple(selected), tuple(referenced.values()))


@dataclass(frozen=True)
class Interval:
    lower: Fraction | None
    upper: Fraction | None
    lower_inclusive: bool
    upper_inclusive: bool

    def contains(self, x: Fraction, *, closure: bool = False) -> bool:
        return (
            (self.lower is None or x > self.lower or (x == self.lower and (closure or self.lower_inclusive)))
            and (self.upper is None or x < self.upper or (x == self.upper and (closure or self.upper_inclusive)))
        )

    def dto(self) -> RootIntervalDTO:
        return RootIntervalDTO(
            start=None if self.lower is None else _wire(self.lower),
            end=None if self.upper is None else _wire(self.upper),
            start_inclusive=self.lower_inclusive, end_inclusive=self.upper_inclusive,
        )


@dataclass(frozen=True)
class PriceMapping:
    base: Fraction
    slope: Fraction
    domain: Interval

    def price(self, x: Fraction) -> Fraction:
        return self.base + self.slope * x

    def at_price(self, price: Fraction) -> Fraction:
        return (price - self.base) / self.slope


def _intersection(left: Interval, right: Interval) -> Interval:
    lo, hi, li, ui = left.lower, left.upper, left.lower_inclusive, left.upper_inclusive
    if right.lower is not None:
        if lo is None or right.lower > lo:
            lo, li = right.lower, right.lower_inclusive
        elif right.lower == lo:
            li = li and right.lower_inclusive
    if right.upper is not None:
        if hi is None or right.upper < hi:
            hi, ui = right.upper, right.upper_inclusive
        elif right.upper == hi:
            ui = ui and right.upper_inclusive
    if lo is not None and hi is not None and (lo > hi or (lo == hi and not (li and ui))):
        raise StrategyUnavailable("DOMAIN_UNAVAILABLE")
    return Interval(lo, hi, li, ui)


def price_mappings(resolved: ResolvedStrategy) -> tuple[dict[str, PriceMapping], Interval]:
    result: dict[str, PriceMapping] = {}
    common = Interval(None, None, False, False)
    for underlying in resolved.underlyings:
        domain = underlying.domain
        if domain.status != "VERIFIED" or underlying.market_price is None or underlying.market_price == 0:
            raise StrategyUnavailable("DOMAIN_UNAVAILABLE")
        slope = _number(underlying.market_price)
        shift = resolved.request.price_shocks_by_underlying.get(
            underlying.underlying_id, resolved.request.global_price_shock,
        )
        base = slope * (1 + _number(shift))
        lo = None if domain.min_price is None else (_number(domain.min_price) - base) / slope
        hi = None if domain.max_price is None else (_number(domain.max_price) - base) / slope
        li, ui = domain.min_inclusive, domain.max_inclusive
        if slope < 0:
            lo, hi, li, ui = hi, lo, ui, li
        interval = Interval(lo, hi, li, ui)
        result[underlying.underlying_id] = PriceMapping(base, slope, interval)
        common = _intersection(common, interval)
    return result, common


def _contributions(resolved: ResolvedStrategy, mappings: Mapping[str, PriceMapping],
                   x: Fraction) -> tuple[Fraction, ...]:
    values = []
    for leg in resolved.legs:
        price = mappings[leg.underlying.underlying_id].price(x)
        if leg.option is None:
            value = price
        else:
            direction = 1 if leg.option.option_type == "CALL" else -1
            value = max(Fraction(0), direction * (price - _number(leg.option.strike)))
        values.append(leg.entry.quantity * _number(leg.multiplier) * (value - _number(leg.entry.entry_price)))
    return tuple(values)


def _total(resolved: ResolvedStrategy, mappings: Mapping[str, PriceMapping], x: Fraction) -> Fraction:
    return sum(_contributions(resolved, mappings, x), Fraction(0))


def _metric(value: Fraction | None) -> RiskMetricDTO:
    return RiskMetricDTO(kind="UNBOUNDED" if value is None else "FINITE",
                         value=None if value is None else _wire(value), reason=None)


def unavailable_risk(reason: Reason) -> StrategyRiskDTO:
    absent = RiskMetricDTO(kind="UNAVAILABLE", value=None, reason=reason)
    return StrategyRiskDTO(scope="UNAVAILABLE", domain=None, max_profit=absent, max_loss=absent,
                           reward_risk_ratio=absent, breakevens=[], zero_intervals=[], roots_exhaustive=False)


def _merge_zero_intervals(intervals: list[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for interval in intervals:
        if merged and merged[-1].upper == interval.lower and (
            merged[-1].upper_inclusive or interval.lower_inclusive
        ):
            previous = merged[-1]
            merged[-1] = Interval(previous.lower, interval.upper, previous.lower_inclusive, interval.upper_inclusive)
        else:
            merged.append(interval)
    return merged


def _analytic_risk(resolved: ResolvedStrategy, mappings: Mapping[str, PriceMapping],
                   domain: Interval) -> tuple[StrategyRiskDTO, set[Fraction]]:
    knots: set[Fraction] = {x for x in (domain.lower, domain.upper) if x is not None}
    for leg in resolved.legs:
        if leg.option is not None:
            x = mappings[leg.underlying.underlying_id].at_price(_number(leg.option.strike))
            if domain.contains(x, closure=True):
                knots.add(x)
    interior = sorted(x for x in knots if x != domain.lower and x != domain.upper)
    boundaries: list[Fraction | None] = [domain.lower, *interior, domain.upper]
    roots: set[Fraction] = set()
    zero_segments: list[Interval] = []
    values = [_total(resolved, mappings, x) for x in knots]
    unbounded_profit = unbounded_loss = False

    if domain.lower is not None and domain.lower == domain.upper:
        if _total(resolved, mappings, domain.lower) == 0:
            roots.add(domain.lower)
    else:
        for lo, hi in zip(boundaries[:-1], boundaries[1:], strict=True):
            # Evaluate the continuous affine extension at segment endpoints
            # (or two finite interior/closure points for unbounded tails).
            a = lo if lo is not None else (hi - 1 if hi is not None else Fraction(0))
            b = hi if hi is not None else a + 1
            va, vb = _total(resolved, mappings, a), _total(resolved, mappings, b)
            slope = (vb - va) / (b - a)
            values.extend((va, vb))
            if lo is None:
                unbounded_profit |= slope < 0
                unbounded_loss |= slope > 0
            if hi is None:
                unbounded_profit |= slope > 0
                unbounded_loss |= slope < 0
            segment = Interval(lo, hi, lo is not None and domain.contains(lo),
                               hi is not None and domain.contains(hi))
            if slope == 0:
                if va == 0:
                    zero_segments.append(segment)
            else:
                root = a - va / slope
                if segment.contains(root):
                    roots.add(root)
    intervals = _merge_zero_intervals(zero_segments)
    roots = {r for r in roots if not any(i.contains(r) for i in intervals)}
    # Finite risk on open domains is the supremum/infimum over the
    # continuous extension, not an assertion that an endpoint is attained.
    profit = None if unbounded_profit else max(Fraction(0), max(values))
    loss = None if unbounded_loss else max(Fraction(0), -min(values))
    ratio = (_metric(profit / loss) if profit is not None and loss is not None and loss > 0
             else RiskMetricDTO(kind="UNAVAILABLE", value=None, reason="CONDITIONAL_ONLY"))
    result = StrategyRiskDTO(
        scope="EXACT_DOMAIN" if len(mappings) == 1 else "CONDITIONAL_COMMON_RETURN",
        domain=domain.dto(), max_profit=_metric(profit), max_loss=_metric(loss), reward_risk_ratio=ratio,
        breakevens=[_wire(x) for x in sorted(roots)], zero_intervals=[i.dto() for i in intervals],
        roots_exhaustive=True,
    )
    return result, knots | roots


def _point(resolved: ResolvedStrategy, mappings: Mapping[str, PriceMapping],
           domain: Interval, x: Fraction) -> StrategyPointDTO:
    prices = {key: _wire(mapping.price(x)) for key, mapping in mappings.items()}
    if not domain.contains(x):
        return StrategyPointDTO(price_return=_wire(x), underlying_prices=prices, status="UNAVAILABLE",
                                reason="INVALID_SCENARIO", pnl=None, contributions=[])
    values = _contributions(resolved, mappings, x)
    return StrategyPointDTO(
        price_return=_wire(x), underlying_prices=prices, status="OK", reason=None,
        pnl=_wire(sum(values, Fraction(0))),
        contributions=[LegContributionDTO(instrument_id=leg.instrument_id, pnl=_wire(value))
                       for leg, value in zip(resolved.legs, values, strict=True)],
    )


def _accounting(resolved: ResolvedStrategy) -> PayoffAccountingDTO:
    option = sum((leg.entry.quantity * _number(leg.multiplier) * _number(leg.entry.entry_price)
                  for leg in resolved.legs if leg.option is not None), Fraction(0))
    spot = sum((leg.entry.quantity * _number(leg.multiplier) * _number(leg.entry.entry_price)
                for leg in resolved.legs if leg.option is None and leg.underlying.kind != "FUTURE"), Fraction(0))
    return PayoffAccountingDTO(status="OK", reason=None, net_premium=_wire(option), spot_entry_cash=_wire(spot))


def expiry_boundary(resolved: ResolvedStrategy) -> tuple[bool, str, str | None]:
    quotes = [leg.option for leg in resolved.legs if leg.option is not None]
    first_day = min(q.expiry_date for q in quotes)
    keys = {(q.expiry_date, q.expiry_clock, q.expiry_at) for q in quotes}
    if len(keys) == 1:
        return True, first_day, quotes[0].expiry_at
    clocks = [q.expiry_at for q in quotes if q.expiry_date == first_day]
    return False, first_day, min(c for c in clocks if c is not None) if all(c is not None for c in clocks) else None


def evaluate_payoff(resolved: ResolvedStrategy) -> StrategyPayoffDTO:
    """Compute exact same-expiry payoff and full-domain analytic risk."""
    accounting = _accounting(resolved)
    same_expiry, day, horizon_at = expiry_boundary(resolved)
    kind: Literal["TERMINAL", "FIRST_EXPIRY_CONDITIONAL"] = "TERMINAL" if same_expiry else "FIRST_EXPIRY_CONDITIONAL"
    try:
        if not same_expiry:
            raise StrategyUnavailable("HORIZON_UNAVAILABLE")
        mappings, domain = price_mappings(resolved)
        risk, knots = _analytic_risk(resolved, mappings, domain)
        knots.update(_number(x) for x in resolved.request.curve_price_returns)
        points = [_point(resolved, mappings, domain, x) for x in sorted(knots)]
        ok = sum(p.status == "OK" for p in points)
        terminal = StrategyCurveDTO(
            kind=kind, status="OK" if ok == len(points) else "PARTIAL" if ok else "UNAVAILABLE",
            reason=None if ok == len(points) else "INVALID_SCENARIO",
            evaluation_date=day, evaluation_at=horizon_at, points=points,
        )
    except (StrategyUnavailable, ValidationError, ArithmeticError) as exc:
        reason: Reason = exc.reason if isinstance(exc, StrategyUnavailable) else "NUMERICAL_FAILURE"
        risk = unavailable_risk(reason)
        terminal = StrategyCurveDTO(kind=kind, status="UNAVAILABLE", reason=reason,
                                    evaluation_date=day, evaluation_at=horizon_at, points=[])
    return StrategyPayoffDTO(
        snapshot=resolved.snapshot, reference_underlying_id=resolved.request.reference_underlying_id,
        global_price_shock=resolved.request.global_price_shock,
        price_shocks_by_underlying=resolved.request.price_shocks_by_underlying,
        legs=[leg.dto() for leg in resolved.legs], accounting=accounting, terminal=terminal, risk=risk,
    )
