"""Evidence 생성 규칙 테스트 (명세 W3a-E1 §3.3) — 지표·부호 전환·추세·중복 금지·결정성.

README §18.2 예시(FIN_OP_MARGIN_2025Q3 형태·필드)와의 호환도 여기서 고정한다.
"""

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from research_backtest.research.evidence import FinancialEvidence, build_financial_evidence
from research_backtest.research.evidence.builder import _assert_unique_ids
from research_backtest.research.evidence.models import EvidenceCategory

WriteDatasets = Callable[..., Path]
_FIXED = datetime(2026, 1, 1, 0, 0, 0)


def _m(metric_id: str, year: int, quarter: int, value: float, af: date) -> dict[str, object]:
    return {
        "metric_id": metric_id,
        "year": year,
        "quarter": quarter,
        "value": value,
        "available_from": af,
    }


def _a(year: int, af: date, **acc: int) -> dict[str, object]:
    return {"year": year, "available_from": af, **acc}


def _q(year: int, quarter: int, af: date, **acc: int) -> dict[str, object]:
    return {"year": year, "quarter": quarter, "available_from": af, **acc}


def _by_id(pkg_evidence: list[FinancialEvidence], evidence_id: str) -> FinancialEvidence:
    match = [e for e in pkg_evidence if e.evidence_id == evidence_id]
    assert match, f"{evidence_id} 없음 — 있는 id: {sorted(e.evidence_id for e in pkg_evidence)}"
    return match[0]


# --- 지표 evidence -----------------------------------------------------------


def test_yoy_metric_evidence_fields(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(metrics=[_m("revenue_yoy", 2024, 2, 0.35, date(2024, 8, 16))])
    pkg = build_financial_evidence("00000000", as_of=date(2024, 12, 31), data_dir=data_dir)
    e = _by_id(pkg.evidence, "FIN_REVENUE_YOY_2024Q2")
    assert e.category == EvidenceCategory.GROWTH.value
    assert e.change_rate == pytest.approx(0.35)
    assert e.current_value is None and e.comparison_value is None
    assert e.period == "2024Q2" and e.comparison_period == "2023Q2"
    assert e.source_fact_ids == ["FACT_revenue_yoy_CFS_2024Q2"]
    assert "35.0% 증가" in e.statement
    assert e.available_from == "2024-08-16"


def test_yoy_metric_negative_direction(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(metrics=[_m("net_income_yoy", 2024, 1, -0.20, date(2024, 5, 16))])
    pkg = build_financial_evidence("00000000", as_of=date(2024, 12, 31), data_dir=data_dir)
    e = _by_id(pkg.evidence, "FIN_NET_INCOME_YOY_2024Q1")
    assert "20.0% 감소" in e.statement


def test_operating_margin_evidence_matches_readme_example(
    write_datasets: WriteDatasets,
) -> None:
    # README §18.2 예시 수치(0.284 vs 0.176 → +0.108pp, "개선")
    data_dir = write_datasets(
        metrics=[
            _m("operating_margin", 2025, 3, 0.284, date(2025, 11, 14)),
            _m("operating_margin", 2024, 3, 0.176, date(2024, 11, 14)),
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2025, 12, 31), data_dir=data_dir)
    e = _by_id(pkg.evidence, "FIN_OP_MARGIN_2025Q3")  # README 예시 id 형태
    assert e.category == EvidenceCategory.PROFITABILITY.value
    assert e.current_value == Decimal("0.284")
    assert e.comparison_value == Decimal("0.176")
    assert e.change_rate == pytest.approx(0.108)
    assert e.period == "2025Q3" and e.comparison_period == "2024Q3"
    assert e.source_fact_ids == [
        "FACT_operating_margin_CFS_2025Q3",
        "FACT_operating_margin_CFS_2024Q3",
    ]
    assert "개선" in e.statement


def test_operating_margin_without_comparison(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(metrics=[_m("operating_margin", 2025, 3, 0.284, date(2025, 11, 14))])
    pkg = build_financial_evidence("00000000", as_of=date(2025, 12, 31), data_dir=data_dir)
    e = _by_id(pkg.evidence, "FIN_OP_MARGIN_2025Q3")
    assert e.comparison_value is None
    assert e.change_rate is None
    assert e.comparison_period is None
    assert e.source_fact_ids == ["FACT_operating_margin_CFS_2025Q3"]


# --- 부호 전환(흑자↔적자) ----------------------------------------------------


def test_annual_sign_change_both_directions(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        annual=[
            _a(2022, date(2023, 3, 22), operating_income=100),
            _a(2023, date(2024, 3, 20), operating_income=-50),  # 흑자→적자
            _a(2024, date(2025, 3, 19), operating_income=80),  # 적자→흑자
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2025, 12, 31), data_dir=data_dir)
    to_loss = _by_id(pkg.evidence, "FIN_OP_INCOME_TURN_FY2023")
    assert "흑자에서 적자로" in to_loss.statement
    assert to_loss.current_value == Decimal(-50) and to_loss.comparison_value == Decimal(100)
    assert to_loss.change_rate == pytest.approx(-1.5)  # (-50-100)/100
    assert to_loss.source_fact_ids == [
        "FACT_operating_income_CFS_2023QA",
        "FACT_operating_income_CFS_2022QA",
    ]
    to_profit = _by_id(pkg.evidence, "FIN_OP_INCOME_TURN_FY2024")
    assert "적자에서 흑자로" in to_profit.statement
    # 부호 전환 TURN과 연간 YoY는 공존한다(서로 다른 evidence).
    assert any(e.evidence_id == "FIN_OP_INCOME_YOY_FY2023" for e in pkg.evidence)


def test_quarterly_sign_change_same_quarter(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        quarterly=[
            _q(2023, 2, date(2023, 8, 16), net_income=-30),
            _q(2024, 2, date(2024, 8, 16), net_income=40),
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2024, 12, 31), data_dir=data_dir)
    e = _by_id(pkg.evidence, "FIN_NET_INCOME_TURN_2024Q2")
    assert "적자에서 흑자로" in e.statement and "전년 동기 대비" in e.statement
    assert e.category == EvidenceCategory.PROFITABILITY.value


def test_no_quarterly_income_yoy_duplicate(write_datasets: WriteDatasets) -> None:
    # 분기 단독 YoY는 A4 지표가 소유 — 계정 evidence는 이를 중복 생성하지 않는다.
    data_dir = write_datasets(
        quarterly=[
            _q(2023, 2, date(2023, 8, 16), revenue=900, operating_income=-50),
            _q(2024, 2, date(2024, 8, 16), revenue=1200, operating_income=30),
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2024, 12, 31), data_dir=data_dir)
    ids = {e.evidence_id for e in pkg.evidence}
    assert not any(i.startswith("FIN_REVENUE_YOY") for i in ids)  # 분기 매출 YoY 미생성
    assert "FIN_OP_INCOME_YOY_2024Q2" not in ids  # 분기 영업이익 YoY 미생성
    assert "FIN_OP_INCOME_TURN_2024Q2" in ids  # 부호 전환은 생성


# --- 추세 --------------------------------------------------------------------


def test_annual_trend_three_plus_consecutive(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        annual=[
            _a(y, date(y + 1, 3, 20), revenue=v)
            for y, v in [(2021, 100), (2022, 150), (2023, 200), (2024, 250)]
        ]
    )
    pkg = build_financial_evidence(
        "00000000", as_of=date(2025, 12, 31), data_dir=data_dir, now=_FIXED
    )
    e = _by_id(pkg.evidence, "FIN_REVENUE_TREND_FY2024")
    assert "2021년부터 2024년까지 4개 연도 연속 증가" in e.statement
    assert e.category == EvidenceCategory.SCALE.value
    assert e.current_value == Decimal(250) and e.comparison_value == Decimal(100)
    assert e.source_fact_ids == [f"FACT_revenue_CFS_{y}QA" for y in (2021, 2022, 2023, 2024)]


def test_two_consecutive_is_not_a_trend(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        annual=[_a(2023, date(2024, 3, 20), revenue=100), _a(2024, date(2025, 3, 19), revenue=150)]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2025, 12, 31), data_dir=data_dir)
    assert not any("TREND" in e.evidence_id for e in pkg.evidence)


# --- 결정성·유일성 -----------------------------------------------------------


def test_determinism_same_input_same_output(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        metrics=[_m("revenue_yoy", 2024, q, 0.1 * q, date(2024, 5 + q, 16)) for q in (1, 2, 3)],
        annual=[
            _a(2022, date(2023, 3, 22), revenue=900, operating_income=-10, net_income=-5),
            _a(2023, date(2024, 3, 20), revenue=1200, operating_income=200, net_income=120),
        ],
    )
    a = build_financial_evidence("00000000", as_of=date(2025, 1, 1), data_dir=data_dir, now=_FIXED)
    b = build_financial_evidence("00000000", as_of=date(2025, 1, 1), data_dir=data_dir, now=_FIXED)
    assert [e.model_dump() for e in a.evidence] == [e.model_dump() for e in b.evidence]
    assert a.model_dump_json() == b.model_dump_json()


def test_evidence_id_uniqueness_guard() -> None:
    dup = FinancialEvidence(
        evidence_id="FIN_X_2024Q1",
        category="GROWTH",
        statement="x",
        current_value=None,
        comparison_value=None,
        change_rate=0.1,
        period="2024Q1",
        comparison_period=None,
        source_fact_ids=[],
        rcept_no="1",
        filing_date="2024-05-16",
        significance_score=0.5,
        fs_scope="CFS",
        available_from="2024-05-17",
    )
    from research_backtest.core.exceptions import DataValidationError

    with pytest.raises(DataValidationError, match="중복"):
        _assert_unique_ids([dup, dup])


def test_all_evidence_ids_unique_on_realistic_mix(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        metrics=[
            _m("operating_margin", 2024, q, 0.1 + 0.01 * q, date(2024, 5 + q, 16))
            for q in (1, 2, 3)
        ],
        quarterly=[
            _q(2024, 2, date(2024, 8, 16), operating_income=-1),
            _q(2023, 2, date(2023, 8, 16), operating_income=5),
        ],
        annual=[
            _a(2022, date(2023, 3, 22), revenue=900, total_liabilities=600),
            _a(2023, date(2024, 3, 20), revenue=1200, total_liabilities=650),
            _a(2024, date(2025, 3, 19), revenue=1500, total_liabilities=700),
        ],
    )
    pkg = build_financial_evidence("00000000", as_of=date(2025, 12, 31), data_dir=data_dir)
    ids = [e.evidence_id for e in pkg.evidence]
    assert len(ids) == len(set(ids))
    assert len(ids) > 0
