"""metrics.py 단위 테스트 — YoY 부호 규약·영업이익률 (명세 A4 §6, §9)."""

from datetime import date

from research_backtest.core.financials.metrics import Metric, compute_metrics
from research_backtest.core.financials.quarterly import DERIVED_QUARTER, REPORTED, Fact


def _fact(
    canonical: str,
    year: int,
    quarter: int,
    value: int,
    value_type: str = REPORTED,
) -> Fact:
    return Fact(
        canonical_id=canonical,
        fs_scope="CFS",
        sj_div="CIS",
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_start=date(year, 1, 1),
        period_end=date(year, 3, 31),
        value=value,
        value_type=value_type,
        source_account_id="id",
        source_account_nm="nm",
        contributing_rcept_nos=[f"{year}0515000001"],
        rcept_no=f"{year}0515000001",
        rcept_dt=date(year, 5, 15),
        available_from=date(year, 5, 16),
    )


def _get(metrics: list[Metric], metric_id: str, year: int, quarter: int) -> Metric | None:
    return next(
        (
            m
            for m in metrics
            if m.metric_id == metric_id and m.fiscal_year == year and m.fiscal_quarter == quarter
        ),
        None,
    )


def test_yoy_positive() -> None:
    facts = [_fact("revenue", 2022, 1, 100), _fact("revenue", 2023, 1, 200)]
    metrics = compute_metrics(facts)
    m = _get(metrics, "revenue_yoy", 2023, 1)
    assert m is not None
    assert m.value == 1.0  # (200-100)/100
    # YoY는 최신 분기 시점에 가용 (전년 분기가 아니라)
    assert m.available_from == date(2023, 5, 16)
    assert m.inputs_derived is False


def test_yoy_negative_base_gives_positive_improvement() -> None:
    # 명세 §6: 전년 적자 → 개선이 양수. yoy=(20-(-10))/abs(-10)=3.0
    facts = [_fact("operating_income", 2022, 1, -10), _fact("operating_income", 2023, 1, 20)]
    metrics = compute_metrics(facts)
    m = _get(metrics, "operating_income_yoy", 2023, 1)
    assert m is not None and m.value == 3.0


def test_yoy_prev_missing_yields_no_metric() -> None:
    facts = [_fact("revenue", 2023, 1, 200)]  # 전년 동기 없음
    metrics = compute_metrics(facts)
    assert _get(metrics, "revenue_yoy", 2023, 1) is None


def test_yoy_prev_zero_yields_no_metric() -> None:
    facts = [_fact("revenue", 2022, 2, 0), _fact("revenue", 2023, 2, 200)]
    metrics = compute_metrics(facts)
    assert _get(metrics, "revenue_yoy", 2023, 2) is None  # 분모 0 → None


def test_operating_margin() -> None:
    facts = [_fact("operating_income", 2023, 1, 20), _fact("revenue", 2023, 1, 200)]
    metrics = compute_metrics(facts)
    m = _get(metrics, "operating_margin", 2023, 1)
    assert m is not None and m.value == 0.1  # 20/200


def test_operating_margin_negative_op() -> None:
    facts = [_fact("operating_income", 2023, 1, -20), _fact("revenue", 2023, 1, 200)]
    metrics = compute_metrics(facts)
    m = _get(metrics, "operating_margin", 2023, 1)
    assert m is not None and m.value == -0.1


def test_operating_margin_zero_revenue_yields_no_metric() -> None:
    facts = [_fact("operating_income", 2023, 1, 20), _fact("revenue", 2023, 1, 0)]
    metrics = compute_metrics(facts)
    assert _get(metrics, "operating_margin", 2023, 1) is None


def test_inputs_derived_flag_propagates() -> None:
    # 최신 분기가 DERIVED_QUARTER(Q4 역산)면 inputs_derived=True
    facts = [
        _fact("operating_income", 2022, 4, 3, DERIVED_QUARTER),
        _fact("operating_income", 2023, 4, 35, DERIVED_QUARTER),
    ]
    metrics = compute_metrics(facts)
    m = _get(metrics, "operating_income_yoy", 2023, 4)
    assert m is not None and m.inputs_derived is True


def test_annual_facts_excluded_from_metrics() -> None:
    # 지표는 단독분기 기준 — 연간(fiscal_quarter=None) fact는 제외
    facts = [
        Fact(
            "revenue",
            "CFS",
            "CIS",
            2023,
            None,
            date(2023, 1, 1),
            date(2023, 12, 31),
            920,
            REPORTED,
            "id",
            "nm",
            ["20240320000001"],
            "20240320000001",
            date(2024, 3, 20),
            date(2024, 3, 21),
        )
    ]
    assert compute_metrics(facts) == []
