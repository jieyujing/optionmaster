"""M1-D02 public boundary tests. All positive registry receipts are synthetic.

The negative real-shaped sample reproduces observed missing fields, NOT a
verified production alias. No database or trading gateway is accessed.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from vnpy_optionmaster.research_combination import ResearchAssetClass
from vnpy_optionmaster.research_contract_terms import (
    AliasAttestation,
    ContractEvidence,
    EvidenceKind,
    ExerciseStyle,
    HistoricalContractEngine,
    HistoricalContractRegistry,
    HistoricalContractRequest,
    HistoricalTermsRow,
    SourceKey,
    UnderlyingIdentity,
    VerifiedTerms,
)
from vnpy_optionmaster.research_risk import ResearchValuationStatus

DAY = date(2022, 9, 7)
START = date(2022, 8, 8)
END = date(2022, 10, 14)
CN = timezone(timedelta(hours=8))
READ_AT = datetime(2026, 11, 24, 10, 0, tzinfo=CN)
INGESTED = datetime(2026, 10, 11, 21, 38, tzinfo=CN)
OPT = SourceKey("option_terms", "XDCE", "A2211-C-5000.XDCE")
UND = SourceKey("option_terms", "XDCE", "A2211.XDCE")
QUOTE = SourceKey("futures_daily", "DCE", "DCE.a2211")
IDENTITY = UnderlyingIdentity("DCE", "DCE.A", "a2211", date(2022, 11, 1), ResearchAssetClass.FUTURES_OPTION)


def _evidence(record_id: str) -> ContractEvidence:
    # Explicitly fictional test receipt; never load these into a real registry.
    return ContractEvidence(
        EvidenceKind.VENDOR_CONTRACT_MASTER, "TEST_ONLY", "https://example.invalid/fixture",
        record_id, "fixture-r1", "a" * 64, "TEST_REVIEWER", INGESTED,
    )


def _registry() -> HistoricalContractRegistry:
    return HistoricalContractRegistry(
        "fixture-registry-v1",
        (
            AliasAttestation(UND, IDENTITY, START, END, _evidence("fixture-left")),
            AliasAttestation(QUOTE, IDENTITY, START, END, _evidence("fixture-right")),
        ),
        (
            VerifiedTerms(
                OPT, "DCE", "a2211-c-5000", IDENTITY, START, END, END, "CALL", ExerciseStyle.AMERICAN,
                5000, 10, "fixture-contract-v1", _evidence("fixture-terms"),
            ),
        ),
    )


def _request() -> HistoricalContractRequest:
    return HistoricalContractRequest(
        DAY, READ_AT, OPT, QUOTE,
        (HistoricalTermsRow(
            OPT, DAY, UND, END, "CALL", "UNKNOWN", 5000, 10,
            "fixture-contract-v1", INGESTED,
        ),),
    )


def _assert_unavailable(request: HistoricalContractRequest, registry: HistoricalContractRegistry, reason: str) -> None:
    result = HistoricalContractEngine.evaluate(request, registry)
    assert result.status is ResearchValuationStatus.UNAVAILABLE
    assert result.reason_code == reason
    assert result.contract is None


def test_exact_independently_attested_aliases_and_version_resolve_unknown_style() -> None:
    result = HistoricalContractEngine.evaluate(_request(), _registry())
    assert result.status is ResearchValuationStatus.OK
    assert result.reason_code is None
    contract = result.contract
    assert contract is not None
    assert contract.terms.exercise_style is ExerciseStyle.AMERICAN
    assert contract.terms.strike == 5000
    assert contract.terms.contract_size == 10
    assert contract.terms.expiry_date == END
    assert contract.terms.contract_version == "fixture-contract-v1"
    assert contract.terms.underlying == IDENTITY
    assert contract.selection.option_symbol == OPT.symbol
    assert contract.selection.option_exchange == OPT.exchange
    assert contract.selection.underlying_contract_id == UND.symbol
    assert contract.selection.underlying_quote_symbol == QUOTE.symbol
    assert contract.selection.underlying_quote_exchange == QUOTE.exchange
    assert contract.selection.product_family_id == "DCE.A"
    assert contract.selection.mapping_provenance.startswith("D02:")
    assert contract.terms.evidence.record_id == "fixture-terms"
    assert contract.terms_alias.evidence.record_id == "fixture-left"
    assert contract.quote_alias.evidence.record_id == "fixture-right"
    assert contract.registry_version == "fixture-registry-v1"
    assert contract.raw_terms == _request().rows[0]


def test_string_similarity_without_reviewed_registry_is_not_mapping_evidence() -> None:
    _assert_unavailable(_request(), replace(_registry(), aliases=()), "MISSING_ALIAS")


def test_real_observed_blank_version_is_not_invented() -> None:
    req = _request()
    observed = replace(req.rows[0], contract_version="", exercise_style="UNKNOWN")
    _assert_unavailable(replace(req, rows=(observed,)), _registry(), "MISSING_CONTRACT_VERSION")

@pytest.mark.parametrize("quote_key", [
    SourceKey("futures_daily", "DCE", "DCE.A2211"),
    SourceKey("futures_daily", "DCE", "DCE.a2301"),
    SourceKey("futures_daily", "SHFE", "DCE.a2211"),
    SourceKey("other_feed", "DCE", "DCE.a2211"),
])
def test_exact_namespace_exchange_symbol_are_not_normalized(quote_key: SourceKey) -> None:
    _assert_unavailable(replace(_request(), underlying_quote_key=quote_key), _registry(), "MISSING_ALIAS")


@pytest.mark.parametrize("wrong_identity", [
    replace(IDENTITY, exchange="SHFE"),
    replace(IDENTITY, product_family_id="DCE.B"),
    replace(IDENTITY, delivery_month=date(2023, 1, 1)),
    replace(IDENTITY, contract_code="a2301"),
    replace(IDENTITY, delivery_month=None, asset_class=ResearchAssetClass.ETF_OPTION),
])
def test_authoritative_identity_disagreement_is_not_an_alias(wrong_identity: UnderlyingIdentity) -> None:
    reg = _registry()
    _assert_unavailable(
        _request(),
        replace(reg, aliases=(reg.aliases[0], replace(reg.aliases[1], identity=wrong_identity))),
        "UNDERLYING_IDENTITY_CONFLICT",
    )


def test_option_terms_underlying_must_match_both_aliases() -> None:
    reg = _registry()
    terms = replace(reg.terms[0], underlying=replace(IDENTITY, delivery_month=date(2023, 1, 1)))
    _assert_unavailable(_request(), replace(reg, terms=(terms,)), "UNDERLYING_IDENTITY_CONFLICT")


@pytest.mark.parametrize(("field", "value", "reason"), [
    ("trade_date", date(2022, 9, 8), "MISMATCHED_TRADE_DATE"),
    ("option_key", SourceKey("option_terms", "XDCE", "A2301-C-5000.XDCE"), "UNEXPECTED_TERMS_ROW"),
    ("underlying_key", SourceKey("option_terms", "XDCE", "A2301.XDCE"), "MISSING_ALIAS"),
    ("ingested_at", READ_AT + timedelta(seconds=1), "FUTURE_INGESTED_AT"),
    ("ingested_at", INGESTED.replace(tzinfo=None), "INVALID_TERMS_ROW"),
    ("expiry_date", date(2022, 9, 6), "INVALID_TERMS_ROW"),
    ("expiry_date", date(2022, 10, 13), "TERMS_CONFLICT"),
    ("expiry_date", None, "INVALID_TERMS_ROW"),
    ("expiry_date", datetime(2022, 10, 14, tzinfo=CN), "INVALID_TERMS_ROW"),
    ("option_type", "PUT", "TERMS_CONFLICT"),
    ("option_type", "OTHER", "INVALID_TERMS_ROW"),
    ("exercise_style", "EUROPEAN", "TERMS_CONFLICT"),
    ("exercise_style", "american", "INVALID_TERMS_ROW"),
    ("exercise_style", "", "INVALID_TERMS_ROW"),
    ("strike", 5050, "TERMS_CONFLICT"),
    ("strike", None, "INVALID_TERMS_ROW"),
    ("strike", float("nan"), "INVALID_TERMS_ROW"),
    ("strike", float("inf"), "INVALID_TERMS_ROW"),
    ("strike", 0, "INVALID_TERMS_ROW"),
    ("contract_size", 100, "TERMS_CONFLICT"),
    ("contract_size", None, "INVALID_TERMS_ROW"),
    ("contract_size", -1, "INVALID_TERMS_ROW"),
    ("contract_size", True, "INVALID_TERMS_ROW"),
    ("contract_size", 10**500, "INVALID_TERMS_ROW"),
    ("contract_version", "other-version", "CONTRACT_VERSION_CONFLICT"),
    ("contract_version", "", "MISSING_CONTRACT_VERSION"),
])
def test_terms_rows_reject_missing_invalid_or_conflicting_fields(field: str, value: object, reason: str) -> None:
    req = _request()
    _assert_unavailable(replace(req, rows=(replace(req.rows[0], **{field: value}),)), _registry(), reason)


def test_missing_duplicate_and_unexpected_rows_fail_closed() -> None:
    req = _request()
    _assert_unavailable(replace(req, rows=()), _registry(), "MISSING_TERMS_ROW")
    _assert_unavailable(replace(req, rows=req.rows * 2), _registry(), "DUPLICATE_TERMS_ROW")
    _assert_unavailable(
        replace(req, rows=(req.rows[0], replace(req.rows[0], contract_version="other"))),
        _registry(), "DUPLICATE_TERMS_ROW",
    )


@pytest.mark.parametrize("same", [True, False])
def test_two_active_alias_records_even_identical_are_ambiguous(same: bool) -> None:
    reg = _registry()
    duplicate = reg.aliases[1] if same else replace(reg.aliases[1], identity=replace(IDENTITY, contract_code="wrong"))
    _assert_unavailable(_request(), replace(reg, aliases=reg.aliases + (duplicate,)), "AMBIGUOUS_ALIAS")


def test_alias_validity_must_cover_requested_date() -> None:
    reg = _registry()
    _assert_unavailable(
        _request(), replace(reg, aliases=(reg.aliases[0], replace(reg.aliases[1], valid_through=DAY - timedelta(days=1)))),
        "ALIAS_OUTSIDE_VALIDITY",
    )
    _assert_unavailable(
        _request(), replace(reg, aliases=(reg.aliases[0], replace(reg.aliases[1], valid_from=END, valid_through=START))),
        "INVALID_ALIAS",
    )


def test_future_delivery_month_is_explicit_month_not_date_or_missing() -> None:
    reg = _registry()
    for bad_month in (None, date(2022, 11, 15), datetime(2022, 11, 1, tzinfo=CN)):
        alias = replace(reg.aliases[1], identity=replace(IDENTITY, delivery_month=bad_month))
        _assert_unavailable(_request(), replace(reg, aliases=(reg.aliases[0], alias)), "INVALID_ALIAS")


def test_unknown_style_without_exact_terms_evidence_is_unavailable() -> None:
    _assert_unavailable(_request(), replace(_registry(), terms=()), "MISSING_TERMS_EVIDENCE")


@pytest.mark.parametrize("duplicate_kind", ["identical", "different_version", "different_terms"])
def test_overlapping_terms_versions_are_ambiguous(duplicate_kind: str) -> None:
    reg = _registry()
    term = reg.terms[0]
    duplicate = {
        "identical": term,
        "different_version": replace(term, contract_version="other"),
        "different_terms": replace(term, strike=5050),
    }[duplicate_kind]
    _assert_unavailable(_request(), replace(reg, terms=(term, duplicate)), "AMBIGUOUS_TERMS_VERSION")


def test_reused_version_identifier_on_nonoverlapping_dates_is_ambiguous() -> None:
    reg = _registry()
    term = reg.terms[0]
    old = replace(term, valid_through=DAY - timedelta(days=1), strike=4950)
    current = replace(term, valid_from=DAY)
    _assert_unavailable(_request(), replace(reg, terms=(old, current)), "AMBIGUOUS_TERMS_VERSION")


def test_distinct_versions_select_exact_effective_date_no_latest_guess() -> None:
    req, reg = _request(), _registry()
    term = reg.terms[0]
    previous_day = DAY - timedelta(days=1)
    old = replace(term, valid_through=previous_day, strike=4950, contract_version="fixture-contract-v0")
    current = replace(term, valid_from=DAY)
    registry = replace(reg, terms=(old, current))
    current_result = HistoricalContractEngine.evaluate(req, registry)
    assert current_result.contract is not None
    assert current_result.contract.terms.contract_version == "fixture-contract-v1"
    old_row = replace(req.rows[0], trade_date=previous_day, strike=4950, contract_version="fixture-contract-v0")
    old_result = HistoricalContractEngine.evaluate(replace(req, trade_date=previous_day, rows=(old_row,)), registry)
    assert old_result.contract is not None
    assert old_result.contract.terms.contract_version == "fixture-contract-v0"
    assert old_result.contract.terms.strike == 4950


def test_same_raw_source_code_can_have_distinct_lifetimes_without_string_inference() -> None:
    req, reg = _request(), _registry()
    old_day, old_start, old_end = date(2012, 9, 7), date(2012, 8, 8), date(2012, 10, 14)
    old_identity = replace(IDENTITY, contract_code="different-native-id", delivery_month=date(2012, 11, 1))
    old_aliases = tuple(replace(a, identity=old_identity, valid_from=old_start, valid_through=old_end) for a in reg.aliases)
    old_terms = replace(
        reg.terms[0], option_contract_code="old-native-option-id", underlying=old_identity, valid_from=old_start, valid_through=old_end,
        expiry_date=old_end, contract_version="fixture-2012",
    )
    registry = replace(reg, aliases=reg.aliases + old_aliases, terms=reg.terms + (old_terms,))
    old_row = replace(req.rows[0], trade_date=old_day, expiry_date=old_end, contract_version="fixture-2012")
    old_result = HistoricalContractEngine.evaluate(replace(req, trade_date=old_day, rows=(old_row,)), registry)
    current_result = HistoricalContractEngine.evaluate(req, registry)
    assert old_result.contract is not None
    assert old_result.contract.terms.underlying == old_identity
    assert current_result.contract is not None
    assert current_result.contract.terms.underlying == IDENTITY


def test_expiry_date_and_effective_interval_are_inclusive_for_terms_only() -> None:
    req = _request()
    for day in (START, END):
        result = HistoricalContractEngine.evaluate(replace(req, trade_date=day, rows=(replace(req.rows[0], trade_date=day),)), _registry())
        assert result.status is ResearchValuationStatus.OK
    # After expiry no terms are returned; intraday expiry cutoff belongs to M0 time engine.
    day = END + timedelta(days=1)
    _assert_unavailable(replace(req, trade_date=day, rows=(replace(req.rows[0], trade_date=day),)), _registry(), "INVALID_TERMS_ROW")


def test_terms_effective_interval_and_expiry_are_independent_checks() -> None:
    reg = _registry()
    term = reg.terms[0]
    _assert_unavailable(_request(), replace(reg, terms=(replace(term, valid_from=DAY + timedelta(days=1)),)), "TERMS_OUTSIDE_VALIDITY")
    _assert_unavailable(_request(), replace(reg, terms=(replace(term, valid_through=END + timedelta(days=1)),)), "INVALID_TERMS_EVIDENCE")


@pytest.mark.parametrize(("field", "value"), [
    ("kind", "PRODUCT_RULE"),
    ("publisher", ""),
    ("source_uri", "not-a-uri"),
    ("source_uri", "https://user:password@example.invalid/path"),
    ("record_id", ""),
    ("revision", ""),
    ("content_sha256", "bad"),
    ("reviewed_by", ""),
    ("reviewed_at", INGESTED.replace(tzinfo=None)),
    ("reviewed_at", READ_AT + timedelta(seconds=1)),
])
def test_provenance_requires_exact_reviewed_record_receipt(field: str, value: object) -> None:
    reg = _registry()
    alias = replace(reg.aliases[0], evidence=replace(reg.aliases[0].evidence, **{field: value}))
    _assert_unavailable(_request(), replace(reg, aliases=(alias, reg.aliases[1])), "INVALID_EVIDENCE")


def test_unreviewed_product_rule_cannot_validate_terms() -> None:
    reg = _registry()
    term = replace(reg.terms[0], evidence=replace(reg.terms[0].evidence, kind="PRODUCT_RULE"))
    _assert_unavailable(_request(), replace(reg, terms=(term,)), "INVALID_EVIDENCE")


def test_real_shaped_record_without_production_registry_is_unavailable() -> None:
    req = _request()
    observed = replace(req.rows[0], exercise_style="UNKNOWN", contract_version="")
    _assert_unavailable(
        replace(req, rows=(observed,)),
        HistoricalContractRegistry("empty-reviewed-registry", (), ()),
        "MISSING_CONTRACT_VERSION",
    )


def test_known_european_style_requires_exact_agreement() -> None:
    req, reg = _request(), _registry()
    term = replace(reg.terms[0], exercise_style=ExerciseStyle.EUROPEAN)
    row = replace(req.rows[0], exercise_style="EUROPEAN")
    result = HistoricalContractEngine.evaluate(replace(req, rows=(row,)), replace(reg, terms=(term,)))
    assert result.contract is not None
    assert result.contract.terms.exercise_style is ExerciseStyle.EUROPEAN


def test_utc_instant_comparison_even_for_ambiguous_local_time() -> None:
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    req = _request()
    read_at = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    later = datetime(2026, 11, 1, 1, 15, tzinfo=ny, fold=1)
    # Local wall clocks look ordered, but the ingested instant is actually later.
    _assert_unavailable(replace(req, read_at=read_at, rows=(replace(req.rows[0], ingested_at=later),)), _registry(), "FUTURE_INGESTED_AT")


def test_validated_terms_export_explicit_exchange_native_option_identity() -> None:
    contract = HistoricalContractEngine.evaluate(_request(), _registry()).contract
    assert contract is not None
    assert contract.terms.option_exchange == "DCE"
    assert contract.terms.option_contract_code == "a2211-c-5000"


def test_d02_selections_feed_existing_d01_without_modification() -> None:
    from vnpy_optionmaster.research_data_basis import (
        HistoricalBasis,
        HistoricalBasisEngine,
        HistoricalBasisRequest,
        HistoricalQuoteRow,
    )

    req, reg = _request(), _registry()
    put_key = SourceKey("option_terms", "XDCE", "A2211-P-5000.XDCE")
    put_row = replace(req.rows[0], option_key=put_key, option_type="PUT")
    put_term = replace(reg.terms[0], option_key=put_key, option_contract_code="a2211-p-5000", option_type="PUT", evidence=_evidence("fixture-put"))
    registry = replace(reg, terms=reg.terms + (put_term,))
    call = HistoricalContractEngine.evaluate(req, registry).contract
    put = HistoricalContractEngine.evaluate(replace(req, option_key=put_key, rows=(put_row,)), registry).contract
    assert call is not None and put is not None
    result = HistoricalBasisEngine.evaluate(HistoricalBasisRequest(
        DAY, READ_AT, (call.selection, put.selection),
        (
            HistoricalQuoteRow(OPT.symbol, OPT.exchange, DAY, 772, 772, "TEST", INGESTED),
            HistoricalQuoteRow(put_key.symbol, put_key.exchange, DAY, 20, 21, "TEST", INGESTED),
        ),
        (HistoricalQuoteRow(QUOTE.symbol, QUOTE.exchange, DAY, 5768, 5772, "TEST", INGESTED),),
    ))
    assert result.status is ResearchValuationStatus.OK
    assert result.price_basis is HistoricalBasis.SETTLEMENT
    assert [(leg.option_price, leg.underlying_price) for leg in result.legs] == [(772, 5772), (21, 5772)]

@pytest.mark.parametrize(("exchange", "code"), [("SHFE", "a2211-c-5000"), ("", "a2211-c-5000"), ("DCE", "")])
def test_futures_option_native_exchange_and_code_must_be_consistent(exchange: str, code: str) -> None:
    reg = _registry()
    term = replace(reg.terms[0], option_exchange=exchange, option_contract_code=code)
    _assert_unavailable(_request(), replace(reg, terms=(term,)), "INVALID_TERMS_EVIDENCE")


@pytest.mark.parametrize("asset", [ResearchAssetClass.ETF_OPTION, ResearchAssetClass.INDEX_OPTION])
def test_spot_index_or_etf_requires_explicit_spot_identity_not_delivery_month(asset: ResearchAssetClass) -> None:
    opt = SourceKey("option_terms", "TEST_OPTION_EX", "test-spot-option")
    left = SourceKey("option_terms", "TEST_SPOT_EX", "test-spot")
    right = SourceKey("spot_quotes", "TEST_QUOTE_EX", "test-spot-alias")
    identity = UnderlyingIdentity("TEST_SPOT", "test-spot-family", "test-native-spot", None, asset)
    reg = _registry()
    reg = replace(reg, aliases=(
        replace(reg.aliases[0], source_key=left, identity=identity),
        replace(reg.aliases[1], source_key=right, identity=identity),
    ), terms=(replace(
        reg.terms[0], option_key=opt, option_exchange="TEST_OPTION",
        option_contract_code="test-native-option", underlying=identity, exercise_style=ExerciseStyle.EUROPEAN,
    ),))
    req = _request()
    req = replace(req, option_key=opt, underlying_quote_key=right, rows=(replace(req.rows[0], option_key=opt, underlying_key=left),))
    contract = HistoricalContractEngine.evaluate(req, reg).contract
    assert contract is not None and contract.selection.asset_class is asset
    assert contract.terms.exercise_style is ExerciseStyle.EUROPEAN
    bad_alias = replace(reg.aliases[1], identity=replace(identity, delivery_month=date(2022, 11, 1)))
    _assert_unavailable(req, replace(reg, aliases=(reg.aliases[0], bad_alias)), "INVALID_ALIAS")


def test_mapping_lineage_fingerprint_binds_reviewed_record_contents() -> None:
    req, reg = _request(), _registry()
    first = HistoricalContractEngine.evaluate(req, reg).contract
    same = HistoricalContractEngine.evaluate(req, reg).contract
    updated_alias = replace(reg.aliases[1], evidence=replace(reg.aliases[1].evidence, revision="fixture-r2", content_sha256="b" * 64))
    changed = HistoricalContractEngine.evaluate(req, replace(reg, aliases=(reg.aliases[0], updated_alias))).contract
    assert first is not None and same is not None and changed is not None
    assert first.selection.mapping_provenance == same.selection.mapping_provenance
    assert first.selection.mapping_provenance != changed.selection.mapping_provenance
    assert len(first.selection.mapping_provenance) == len("D02:") + 64


def test_future_review_timestamp_uses_utc_instant() -> None:
    from zoneinfo import ZoneInfo

    ny = ZoneInfo("America/New_York")
    req, reg = _request(), _registry()
    read_at = datetime(2026, 11, 1, 1, 30, tzinfo=ny, fold=0)
    later = datetime(2026, 11, 1, 1, 15, tzinfo=ny, fold=1)
    alias = replace(reg.aliases[0], evidence=replace(reg.aliases[0].evidence, reviewed_at=later))
    _assert_unavailable(replace(req, read_at=read_at), replace(reg, aliases=(alias, reg.aliases[1])), "INVALID_EVIDENCE")


def test_future_trade_date_and_naive_read_time_rejected() -> None:
    req = _request()
    _assert_unavailable(replace(req, trade_date=date(2026, 11, 25)), _registry(), "INVALID_INPUT")
    _assert_unavailable(replace(req, read_at=READ_AT.replace(tzinfo=None)), _registry(), "INVALID_INPUT")

def test_unrepresentable_timestamp_conversions_fail_closed() -> None:
    req, reg = _request(), _registry()
    _assert_unavailable(
        replace(req, read_at=datetime.max.replace(tzinfo=timezone.utc)),
        reg, "INVALID_INPUT",
    )
    too_early = datetime.min.replace(tzinfo=CN)
    _assert_unavailable(
        replace(req, rows=(replace(req.rows[0], ingested_at=too_early),)),
        reg, "INVALID_TERMS_ROW",
    )
    bad_alias = replace(reg.aliases[0], evidence=replace(reg.aliases[0].evidence, reviewed_at=too_early))
    _assert_unavailable(req, replace(reg, aliases=(bad_alias, reg.aliases[1])), "INVALID_EVIDENCE")
