"""Point-in-Time·필터 경계 테스트 (명세 W3a-E1 §3.3 필터, DoD PIT 경계·lookback).

절대 규칙 #1: ``available_from <= as_of``인 행만 evidence가 된다. as_of 이후
접수 공시는 어떤 evidence에도 새지 않아야 한다.
"""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.research.evidence import build_financial_evidence

WriteDatasets = Callable[..., Path]


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


def test_pit_boundary_available_from_equals_as_of_included(
    write_datasets: WriteDatasets,
) -> None:
    # available_from == as_of는 포함, available_from == as_of + 1일은 제외
    data_dir = write_datasets(
        metrics=[
            _m("revenue_yoy", 2024, 2, 0.30, date(2024, 8, 16)),  # == as_of
            _m("revenue_yoy", 2024, 3, 0.40, date(2024, 11, 15)),  # > as_of
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2024, 8, 16), data_dir=data_dir)
    ids = {e.evidence_id for e in pkg.evidence}
    assert "FIN_REVENUE_YOY_2024Q2" in ids  # 경계 포함
    assert "FIN_REVENUE_YOY_2024Q3" not in ids  # as_of 이후 접수 → 제외
    assert all(e.available_from <= "2024-08-16" for e in pkg.evidence)


def test_future_disclosure_never_leaks(write_datasets: WriteDatasets) -> None:
    # 미래(as_of 이후) 공시가 여럿 있어도 evidence는 0건 유출
    data_dir = write_datasets(
        annual=[
            _a(2022, date(2023, 3, 22), revenue=900, operating_income=100),
            _a(2023, date(2024, 3, 20), revenue=1200, operating_income=200),
            _a(2024, date(2025, 3, 19), revenue=1500, operating_income=300),
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2023, 12, 31), data_dir=data_dir)
    # 2024 사업보고서는 2025-03 접수 → 2023-12-31 시점엔 미가용
    assert all(e.available_from <= "2023-12-31" for e in pkg.evidence)
    assert not any("FY2024" in e.evidence_id for e in pkg.evidence)


def test_lookback_truncation(write_datasets: WriteDatasets) -> None:
    # lookback_years=3, as_of=2024-06-30 → cutoff period_end 2021-06-30
    # period_end 2020-12-31은 절단되어 FY2021 YoY의 전년(2020) base가 사라진다.
    data_dir = write_datasets(
        annual=[_a(y, date(y + 1, 3, 20), revenue=100 + y) for y in (2020, 2021, 2022, 2023)]
    )
    pkg = build_financial_evidence(
        "00000000", as_of=date(2024, 6, 30), data_dir=data_dir, lookback_years=3
    )
    ids = {e.evidence_id for e in pkg.evidence}
    assert "FIN_REVENUE_YOY_FY2021" not in ids  # 2020 base가 lookback으로 절단
    assert "FIN_REVENUE_YOY_FY2022" in ids
    assert "FIN_REVENUE_YOY_FY2023" in ids


def test_fs_scope_filter_excludes_other_scope(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(
        metrics=[
            {**_m("revenue_yoy", 2024, 2, 0.30, date(2024, 8, 16)), "fs_scope": "CFS"},
            {**_m("revenue_yoy", 2024, 2, 0.99, date(2024, 8, 16)), "fs_scope": "OFS"},
        ]
    )
    pkg = build_financial_evidence("00000000", as_of=date(2024, 12, 31), data_dir=data_dir)
    assert all(e.fs_scope == "CFS" for e in pkg.evidence)
    assert all(e.change_rate != 0.99 for e in pkg.evidence if e.change_rate is not None)


def test_empty_after_filter_raises(write_datasets: WriteDatasets) -> None:
    data_dir = write_datasets(metrics=[_m("revenue_yoy", 2024, 2, 0.30, date(2024, 8, 16))])
    # as_of가 첫 공시보다 이르면 세 프레임 모두 0행 → DataValidationError
    with pytest.raises(DataValidationError):
        build_financial_evidence("00000000", as_of=date(2019, 1, 1), data_dir=data_dir)


def test_missing_parquet_raises_filenotfound(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build-financials"):
        build_financial_evidence("00000000", as_of=date(2024, 1, 1), data_dir=tmp_path / "data")
