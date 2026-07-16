"""quarterly.py 단위 테스트 — 단독분기 역산 (명세 A4 §4, §9)."""

from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.financials.normalizer import (
    NormalizationResult,
    ObservationKey,
    ReportObservation,
)
from research_backtest.core.financials.quarterly import (
    DERIVED_QUARTER,
    REPORTED,
    Fact,
    derive_facts,
    period_bounds,
    rcept_to_date,
)
from research_backtest.core.financials.registry import CanonicalAccount, load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "configs" / "account_registry.yaml"

_RCEPT = {
    "11013": "20240516000001",
    "11012": "20240814000001",
    "11014": "20241114000001",
    "11011": "20250320000001",
}


@pytest.fixture
def registry() -> dict[str, CanonicalAccount]:
    return load_registry(REGISTRY_PATH)


def _obs(
    result: NormalizationResult,
    canonical: str,
    reprt: str,
    thstrm: int | None,
    add: int | None = None,
    *,
    sj: str = "CIS",
) -> None:
    result.observations[ObservationKey(canonical, "CFS", 2024, reprt)] = ReportObservation(
        canonical_id=canonical,
        fs_scope="CFS",
        fiscal_year=2024,
        reprt_code=reprt,
        sj_div=sj,
        thstrm_amount=thstrm,
        thstrm_add_amount=add,
        rcept_no=_RCEPT[reprt],
        source_account_id="src_id",
        source_account_nm="src_nm",
    )


def _find(facts: list[Fact], canonical: str, quarter: int | None) -> Fact | None:
    return next(
        (f for f in facts if f.canonical_id == canonical and f.fiscal_quarter == quarter), None
    )


def _require(facts: list[Fact], canonical: str, quarter: int | None) -> Fact:
    fact = _find(facts, canonical, quarter)
    assert fact is not None, f"fact 없음: {canonical} Q{quarter}"
    return fact


# --- rcept_to_date / period_bounds -------------------------------------------


def test_rcept_to_date() -> None:
    assert rcept_to_date("20241114001712") == date(2024, 11, 14)


def test_rcept_to_date_invalid() -> None:
    with pytest.raises(ValueError, match="접수일"):
        rcept_to_date("abc")


def test_period_bounds_quarters_and_annual() -> None:
    assert period_bounds(2024, 1) == (date(2024, 1, 1), date(2024, 3, 31))
    assert period_bounds(2024, 2) == (date(2024, 4, 1), date(2024, 6, 30))
    assert period_bounds(2024, 4) == (date(2024, 10, 1), date(2024, 12, 31))
    assert period_bounds(2024, None) == (date(2024, 1, 1), date(2024, 12, 31))


# --- 손익(period_flow) 역산 --------------------------------------------------


def test_income_q2_direct_path_reported(registry: dict[str, CanonicalAccount]) -> None:
    result = NormalizationResult()
    _obs(result, "revenue", "11013", 100, 100)
    _obs(result, "revenue", "11012", 110, 210)  # thstrm(3개월) 존재 → 직접
    _obs(result, "revenue", "11014", 120, 330)
    _obs(result, "revenue", "11011", 460, None)
    facts = derive_facts(result, registry).facts
    q2 = _find(facts, "revenue", 2)
    assert q2 is not None and q2.value == 110 and q2.value_type == REPORTED
    q4 = _require(facts, "revenue", 4)
    assert q4.value == 130 and q4.value_type == DERIVED_QUARTER  # 연간460 - 3Q누적330
    assert _require(facts, "revenue", None).value == 460


def test_income_q2_reversal_path_derived(registry: dict[str, CanonicalAccount]) -> None:
    # 반기 thstrm 결측 → 반기누적 - Q1
    result = NormalizationResult()
    _obs(result, "revenue", "11013", 100, 100)
    _obs(result, "revenue", "11012", None, 210)  # thstrm 없음, add만
    _obs(result, "revenue", "11014", 120, 330)
    _obs(result, "revenue", "11011", 460, None)
    facts = derive_facts(result, registry).facts
    q2 = _find(facts, "revenue", 2)
    assert q2 is not None and q2.value == 110 and q2.value_type == DERIVED_QUARTER


def test_income_q3_reversal_path_derived(registry: dict[str, CanonicalAccount]) -> None:
    result = NormalizationResult()
    _obs(result, "revenue", "11013", 100, 100)
    _obs(result, "revenue", "11012", 110, 210)
    _obs(result, "revenue", "11014", None, 330)  # thstrm 없음 → 3Q누적 - 반기누적
    _obs(result, "revenue", "11011", 460, None)
    facts = derive_facts(result, registry).facts
    q3 = _find(facts, "revenue", 3)
    assert q3 is not None and q3.value == 120 and q3.value_type == DERIVED_QUARTER


def test_income_q4_fallback_to_singles_sum(registry: dict[str, CanonicalAccount]) -> None:
    # 3Q누적(add) 결측 → 연간 - (Q1+Q2+Q3 단독합)
    result = NormalizationResult()
    _obs(result, "revenue", "11013", 100, 100)
    _obs(result, "revenue", "11012", 110, 210)
    _obs(result, "revenue", "11014", 120, None)  # add 결측이지만 thstrm(단독)은 존재
    _obs(result, "revenue", "11011", 460, None)
    facts = derive_facts(result, registry).facts
    q4 = _find(facts, "revenue", 4)
    assert q4 is not None and q4.value == 130 and q4.value_type == DERIVED_QUARTER


def test_income_missing_half_yields_gap(registry: dict[str, CanonicalAccount]) -> None:
    result = NormalizationResult()
    _obs(result, "revenue", "11013", 100, 100)
    _obs(result, "revenue", "11011", 460, None)  # 반기·3Q 관측치 없음
    quarterly = derive_facts(result, registry)
    assert _find(quarterly.facts, "revenue", 2) is None  # Q2 미생성
    assert any(g.canonical_id == "revenue" and g.fiscal_quarter == 2 for g in quarterly.gaps)


# --- CF(cumulative_flow) 역산 ------------------------------------------------


def test_cf_cumulative_differencing(registry: dict[str, CanonicalAccount]) -> None:
    result = NormalizationResult()
    _obs(result, "operating_cash_flow", "11013", 50, sj="CF")  # 누적 = Q1 단독
    _obs(result, "operating_cash_flow", "11012", 110, sj="CF")  # 6M 누적
    _obs(result, "operating_cash_flow", "11014", 180, sj="CF")  # 9M 누적
    _obs(result, "operating_cash_flow", "11011", 260, sj="CF")  # 연간
    facts = derive_facts(result, registry).facts
    assert _require(facts, "operating_cash_flow", 1).value == 50
    assert _require(facts, "operating_cash_flow", 1).value_type == REPORTED
    q2 = _find(facts, "operating_cash_flow", 2)
    assert q2 is not None and q2.value == 60 and q2.value_type == DERIVED_QUARTER
    assert _require(facts, "operating_cash_flow", 3).value == 70
    assert _require(facts, "operating_cash_flow", 4).value == 80
    # telescoping: 단독합 == 연간
    singles = sum(_require(facts, "operating_cash_flow", q).value for q in (1, 2, 3, 4))
    assert singles == 260


# --- BS(instant) -------------------------------------------------------------


def test_bs_instant_all_reported_period_start_none(registry: dict[str, CanonicalAccount]) -> None:
    result = NormalizationResult()
    _obs(result, "total_assets", "11013", 1000, sj="BS")
    _obs(result, "total_assets", "11012", 1010, sj="BS")
    _obs(result, "total_assets", "11014", 1020, sj="BS")
    _obs(result, "total_assets", "11011", 1030, sj="BS")
    facts = derive_facts(result, registry).facts
    q1 = _find(facts, "total_assets", 1)
    assert q1 is not None and q1.value == 1000 and q1.value_type == REPORTED
    assert q1.period_start is None and q1.period_end == date(2024, 3, 31)
    # Q4 잔액 = 연말 잔액 = 사업보고서 값
    assert _require(facts, "total_assets", 4).value == 1030
    assert _require(facts, "total_assets", None).value == 1030
