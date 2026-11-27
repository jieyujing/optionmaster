"""Reuse the independently reviewed D09 dated contract registry.

This loader creates an explicit historical read scope, not a database
connection or fresh quote admission. The archive's source pins and review
are validated before accepting any legacy UNKNOWN/empty vendor fields.
New observations must still pass HistoricalClickHouseProvider validation.
"""
from pathlib import Path
from collections.abc import Mapping

from vnpy_optionmaster.research_catalog_api import CATALOG_PIN, load_reviewed_catalog
from vnpy_optionmaster.research_multi_chain_archive import _children
from vnpy_optionmaster.research_strategy_archive import ReviewedStrategyArchive
from vnpy_optionmaster.research_historical_provider import ChainScope, ReviewedOption, history_query_plan
from vnpy_optionmaster.research_strategy_payoff import StrategyUnavailable


def _one_text(rows: Mapping[str, Mapping[str, object]], field: str) -> str:
    values = [row[field] for row in rows.values()]
    if not values or not isinstance(values[0], str) or not values[0]:
        raise StrategyUnavailable("TERMS_UNAVAILABLE")
    if any(value != values[0] for value in values):
        raise StrategyUnavailable("SOURCE_AMBIGUOUS")
    return values[0]


def load_pinned_catalog_history_scopes(root: Path) -> tuple[ChainScope, ...]:
    """Exact approved dated CU bindings; no inferred aliases or wider claims."""
    source = load_reviewed_catalog(root)
    archive = ReviewedStrategyArchive(root)
    scopes: list[ChainScope] = []
    for child, packet in _children(source.sources):
        day = child.scope.day.isoformat()
        future = packet["underlying"]
        underlying_id = str(future["symbol"])
        chain = archive.chain("SHFE", "CU", day, underlying_id, None, None)
        snapshot = archive.load_snapshot(chain.snapshot.snapshot_id)
        u = chain.underlying
        if snapshot is None or chain.snapshot.source_pin != CATALOG_PIN or u.delivery_month is None:
            raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
        approved = {q.contract_id: q for q in snapshot.options if q.underlying_id == underlying_id}
        bindings = packet["bindings"]
        raw = {b["raw_terms"]["symbol"]: b["raw_terms"] for b in bindings}
        if len(raw) != len(bindings) or set(raw) != set(approved):
            raise StrategyUnavailable("SOURCE_AMBIGUOUS")
        options = tuple(ReviewedOption(
            symbol=q.contract_id, option_type=q.option_type, strike=q.strike,
            multiplier=q.multiplier, expiry_date=q.expiry_date, exercise_style=q.exercise_style,
            terms_version=q.terms_version, expiry_at=q.expiry_at,
            raw_exercise_style=raw[q.contract_id]["exercise_style"],
            raw_contract_version=raw[q.contract_id]["contract_version"], binding_pin=CATALOG_PIN,
        ) for q in sorted(approved.values(), key=lambda q: q.contract_id))
        scopes.append(ChainScope(
            exchange=chain.snapshot.exchange, product=chain.snapshot.product, trade_date=day,
            underlying_id=underlying_id, delivery_month=u.delivery_month,
            portfolio_code=_one_text(raw, "portfolio_code"), chain_id=_one_text(raw, "chain_id"),
            vendor_underlying=_one_text(raw, "underlying_symbol"), vendor_exchange=_one_text(raw, "exchange"),
            futures_product=str(future["product_symbol"]), futures_exchange=str(future["exchange"]),
            option_source=_one_text(raw, "source"), future_price_source=str(future["price_source"]),
            future_settlement_source=str(future["settlement_source"]),
            multiplier=u.multiplier, quote_unit=u.quote_unit, underlying_terms_version=u.terms_version,
            policy_pin=CATALOG_PIN, reference_at=chain.snapshot.reference_at,
            terms_provenance=(_one_text(raw, "provenance"),), options=options, price_domain=u.domain,
            rates=tuple(r for r in snapshot.rates if r.expiry_id == approved[options[0].symbol].expiry_id),
        ))
    result = tuple(scopes)
    if sum(len(s.options) for s in result) != 198:
        raise StrategyUnavailable("SOURCE_VERSION_UNVERIFIED")
    history_query_plan(result)
    return result
