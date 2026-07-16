"""compare 단위 테스트 — 값 비교·상태 분류·Decimal 정밀 (명세 §4·§6)."""

from __future__ import annotations

from decimal import Decimal

from research_backtest.core.reconciliation.compare import (
    ABSOLUTE_TOLERANCE,
    ReconciliationResult,
    ReconciliationStatus,
    classify,
)
from research_backtest.core.reconciliation.xbrl_select import (
    FactSelection,
    SelectionStage,
    XbrlFactView,
)


def _selected(*raw_values: str) -> FactSelection:
    """SELECTED 단계의 합성 선택 결과 — raw_value별 후보 fact."""
    candidates = tuple(
        XbrlFactView(
            concept_namespace="ns",
            concept_local_name="Assets",
            context_id=f"c{i}",
            raw_value=v,
        )
        for i, v in enumerate(raw_values)
    )
    return FactSelection(SelectionStage.SELECTED, candidates, len(candidates), len(candidates))


def _classify(selection: FactSelection, api_value: Decimal | None) -> ReconciliationResult:
    return classify(
        selection,
        api_value=api_value,
        canonical_account_id="total_assets",
        period_end="2023-12-31",
        fs_scope="CFS",
    )


# --- 값 비교 -----------------------------------------------------------------


def test_match_when_equal() -> None:
    res = _classify(_selected("96386474000000"), Decimal("96386474000000"))
    assert res.status == ReconciliationStatus.MATCH
    assert res.absolute_difference == Decimal(0)
    assert res.relative_difference == 0.0
    assert res.xbrl_value == Decimal("96386474000000")


def test_rounding_at_absolute_boundary() -> None:
    # DATA_NOTES A4-④: 정확히 1e6 KRW 경계는 '<=' 판정으로 ROUNDING.
    api = Decimal("12000000000")
    xbrl = api - ABSOLUTE_TOLERANCE  # 차이 정확히 1,000,000
    res = _classify(_selected(str(xbrl)), api)
    assert res.status == ReconciliationStatus.ROUNDING_DIFFERENCE
    assert res.absolute_difference == ABSOLUTE_TOLERANCE


def test_rounding_via_relative_tolerance() -> None:
    # 절대차 > 1e6이지만 상대차 <= 0.1% → ROUNDING (OR 조건).
    api = Decimal("2000000000")
    xbrl = api + Decimal("1500000")  # 절대 1.5e6, 상대 0.00075
    res = _classify(_selected(str(xbrl)), api)
    assert res.status == ReconciliationStatus.ROUNDING_DIFFERENCE


def test_requires_review_when_exceeds_tolerance() -> None:
    res = _classify(_selected("1000000"), Decimal("3000000"))  # 절대 2e6, 상대 0.667
    assert res.status == ReconciliationStatus.REQUIRES_REVIEW
    assert res.absolute_difference == Decimal("2000000")
    assert res.reason is not None and "허용 오차 초과" in res.reason


def test_decimal_precision_no_float_pollution() -> None:
    # 1e18 규모에서 +1 KRW 차이 — float이면 0으로 뭉개지나 Decimal은 정확히 1.
    api = Decimal("1000000000000000000")
    xbrl = Decimal("1000000000000000001")
    assert float(api) == float(xbrl)  # float은 두 값을 구분하지 못한다(오염 근거)
    res = _classify(_selected(str(xbrl)), api)
    assert res.absolute_difference == Decimal(1)  # Decimal은 정확히 1 KRW 차이
    assert res.status == ReconciliationStatus.ROUNDING_DIFFERENCE  # 1 <= 1e6


# --- 후보 수·단계별 상태 -----------------------------------------------------


def test_duplicate_candidates_requires_review_identical_value() -> None:
    res = _classify(_selected("8494188000000", "8494188000000"), Decimal("8494188000000"))
    assert res.status == ReconciliationStatus.REQUIRES_REVIEW
    assert res.xbrl_value is None
    assert res.reason is not None and "동일값" in res.reason and "2개" in res.reason


def test_duplicate_candidates_differing_values_flagged() -> None:
    res = _classify(_selected("100", "200"), Decimal("100"))
    assert res.status == ReconciliationStatus.REQUIRES_REVIEW
    assert res.reason is not None and "상이값" in res.reason


def test_missing_in_api() -> None:
    res = _classify(_selected("100"), None)
    assert res.status == ReconciliationStatus.MISSING_IN_API
    assert res.api_value is None


def test_missing_in_xbrl_no_concept() -> None:
    res = _classify(FactSelection(SelectionStage.NO_CONCEPT, (), 0, 0), Decimal("100"))
    assert res.status == ReconciliationStatus.MISSING_IN_XBRL
    assert res.xbrl_value is None


def test_context_mismatch_no_period() -> None:
    res = _classify(FactSelection(SelectionStage.NO_PERIOD, (), 2, 0), Decimal("100"))
    assert res.status == ReconciliationStatus.CONTEXT_MISMATCH


def test_scope_mismatch_no_scope() -> None:
    res = _classify(FactSelection(SelectionStage.NO_SCOPE, (), 3, 2), Decimal("100"))
    assert res.status == ReconciliationStatus.SCOPE_MISMATCH


def test_major_account_value_always_none() -> None:
    res = _classify(_selected("100"), Decimal("100"))
    assert res.major_account_value is None
