"""D08 independently pinned same-chain archived research valuation.

The public entry point does not accept a caller-built admitted slice.
Reuses D05's research-only rate, clock, IV inversion and grid logic.
"""
from collections.abc import Mapping
from datetime import datetime

from vnpy_optionmaster.research_archived_valuation import (
    ArchivedValuationPolicy, ArchivedValuationRequest, ArchivedValuationResult, _evaluate, _Rejected,
)
from vnpy_optionmaster.research_chain_archive import admit_chain_selection
from vnpy_optionmaster.research_risk import ResearchValuationStatus
from vnpy_optionmaster.research_verified_slice import SliceApproval


def evaluate_chain_valuation(
    request: ArchivedValuationRequest,
    packet: bytes,
    evidence: Mapping[str, bytes],
    approval: SliceApproval | None,
    rate_artifact: bytes,
    policy: ArchivedValuationPolicy | None,
    read_at: datetime,
) -> ArchivedValuationResult:
    """Research-only 2..8 distinct approved legs of the exact same chain."""
    try:
        return _evaluate(request, packet, evidence, approval, rate_artifact, policy, read_at,
                         admission_fn=admit_chain_selection, max_legs=8, require_positive_extrinsic=True)
    except _Rejected as error:
        return ArchivedValuationResult(ResearchValuationStatus.UNAVAILABLE, str(error))
    except (TypeError, ValueError, OverflowError, AttributeError, KeyError, IndexError, RecursionError):
        return ArchivedValuationResult(ResearchValuationStatus.UNAVAILABLE, "INVALID_INPUT")
