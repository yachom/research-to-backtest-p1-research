"""pipeline.py 단위 테스트 — 산출 스키마 계약 + 검증 (명세 A4 §7~§8, §9).

A6가 이 스키마로 병렬 개발되므로 컬럼·dtype을 계약으로 고정한다.
"""

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
import pytest

from research_backtest.core.constants import FsDiv
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.financials.pipeline import (
    ANNUAL_FILENAME,
    BUILD_REPORT_FILENAME,
    METRICS_FILENAME,
    NORMALIZED_FACTS_FILENAME,
    QUARTERLY_FILENAME,
    FinancialBuildReport,
    build_financial_datasets,
    financials_out_dir,
)
from research_backtest.core.market.calendar import KrxTradingCalendar

CORP = "00000000"

ACCOUNT_COLUMNS = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_equity",
    "cash_and_cash_equivalents",
    "operating_cash_flow",
    "purchase_of_ppe",
    "inventories",
    "trade_receivables",
]
NORMALIZED_FACTS_COLUMNS = [
    "canonical_id",
    "fs_scope",
    "sj_div",
    "fiscal_year",
    "fiscal_quarter",
    "period_start",
    "period_end",
    "value",
    "value_type",
    "rcept_no",
    "rcept_dt",
    "available_from",
    "source_account_id",
    "source_account_nm",
]
METRICS_COLUMNS = [
    "metric_id",
    "fs_scope",
    "fiscal_year",
    "fiscal_quarter",
    "period_end",
    "value",
    "rcept_no",
    "rcept_dt",
    "available_from",
    "inputs_derived",
]
_DATE_COLUMNS = {"period_start", "period_end", "rcept_dt", "available_from"}


@pytest.fixture
def built(
    write_synthetic_dataset: Callable[..., Path], fake_calendar: KrxTradingCalendar
) -> tuple[Path, FinancialBuildReport]:
    data_dir = write_synthetic_dataset(corp_code=CORP)
    report = build_financial_datasets(
        CORP, data_dir=data_dir, calendar=fake_calendar, scopes=_cfs_only()
    )
    return financials_out_dir(data_dir, CORP), report


def _cfs_only() -> tuple[FsDiv, ...]:
    return (FsDiv.CFS,)


def _assert_dates_are_date32(path: Path, columns: set[str]) -> None:
    # pyarrow 24 배포 타입에 read_schema 시그니처가 아직 없다 (부분 py.typed).
    schema = pq.read_schema(path)  # type: ignore[no-untyped-call]
    for field in schema:
        if field.name in columns:
            assert str(field.type) == "date32[day]", f"{path.name}.{field.name}={field.type}"


# --- 스키마 계약 (명세 §7) ---------------------------------------------------


def test_normalized_facts_schema(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    path = out_dir / NORMALIZED_FACTS_FILENAME
    df = pd.read_parquet(path)
    assert list(df.columns) == NORMALIZED_FACTS_COLUMNS
    assert str(df["fiscal_year"].dtype) == "int64"
    assert str(df["fiscal_quarter"].dtype) == "Int64"
    assert str(df["value"].dtype) == "Int64"
    assert {"REPORTED", "DERIVED_QUARTER"} >= set(df["value_type"].dropna().unique())
    _assert_dates_are_date32(path, _DATE_COLUMNS)
    # 연간 fact는 fiscal_quarter=NA
    annual = df[df["fiscal_quarter"].isna()]
    assert not annual.empty


def test_quarterly_schema(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    path = out_dir / QUARTERLY_FILENAME
    df = pd.read_parquet(path)
    expected = [
        "fs_scope",
        "fiscal_year",
        "fiscal_quarter",
        "period_start",
        "period_end",
        "rcept_no",
        "rcept_dt",
        "available_from",
        *ACCOUNT_COLUMNS,
    ]
    assert list(df.columns) == expected
    for col in ACCOUNT_COLUMNS:
        assert str(df[col].dtype) == "Int64", col
    assert str(df["fiscal_quarter"].dtype) == "Int64"
    _assert_dates_are_date32(path, _DATE_COLUMNS)
    # 2 scope 없이 CFS만: 5년? 아니 2년 x 4분기 = 8행
    assert len(df) == 8


def test_annual_schema_has_no_quarter(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    path = out_dir / ANNUAL_FILENAME
    df = pd.read_parquet(path)
    expected = [
        "fs_scope",
        "fiscal_year",
        "period_start",
        "period_end",
        "rcept_no",
        "rcept_dt",
        "available_from",
        *ACCOUNT_COLUMNS,
    ]
    assert list(df.columns) == expected
    assert "fiscal_quarter" not in df.columns
    assert len(df) == 2  # 2022, 2023


def test_metrics_schema(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    path = out_dir / METRICS_FILENAME
    df = pd.read_parquet(path)
    assert list(df.columns) == METRICS_COLUMNS
    assert str(df["value"].dtype) == "float64"
    assert str(df["inputs_derived"].dtype) == "bool"
    _assert_dates_are_date32(path, {"period_end", "rcept_dt", "available_from"})
    assert set(df["metric_id"].unique()) <= {
        "revenue_yoy",
        "operating_income_yoy",
        "net_income_yoy",
        "operating_margin",
    }


# --- 값·검증 -----------------------------------------------------------------


def test_negative_base_yoy_in_output(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    df = pd.read_parquet(out_dir / METRICS_FILENAME)
    # 2022 영업이익 Q1=-10, 2023 Q1=20 → yoy=(20-(-10))/10=3.0
    row = df[
        (df.metric_id == "operating_income_yoy")
        & (df.fiscal_year == 2023)
        & (df.fiscal_quarter == 1)
    ]
    assert len(row) == 1
    assert float(row.iloc[0]["value"]) == 3.0


def test_cf_single_quarter_in_quarterly(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    df = pd.read_parquet(out_dir / QUARTERLY_FILENAME)
    # CF 2022: 누적 50/110/180/260 → 단독 50/60/70/80
    q = df[(df.fiscal_year == 2022)].sort_values("fiscal_quarter")
    assert list(q["operating_cash_flow"]) == [50, 60, 70, 80]


def test_validations_pass_on_clean_data(built: tuple[Path, FinancialBuildReport]) -> None:
    _, report = built
    checks = {v.name: v for v in report.validations}
    assert checks["accounting_identity"].passed
    assert checks["cross_source_consistency"].passed
    assert checks["available_from_gt_period_end"].passed
    assert report.matching.unresolved == []
    assert report.coverage.recent_quarters_income_complete


def test_build_report_written(built: tuple[Path, FinancialBuildReport]) -> None:
    out_dir, _ = built
    report = json.loads((out_dir / BUILD_REPORT_FILENAME).read_text(encoding="utf-8"))
    assert report["corp_code"] == CORP
    assert report["matching"]["per_account_matched_rows"]["revenue"] > 0
    assert report["files"]["financial_metrics_rows"] > 0


def test_accounting_identity_violation_raises(
    write_synthetic_dataset: Callable[..., Path], fake_calendar: KrxTradingCalendar
) -> None:
    data_dir = write_synthetic_dataset(corp_code=CORP, broken_identity=True)
    with pytest.raises(DataValidationError, match="accounting_identity"):
        build_financial_datasets(
            CORP, data_dir=data_dir, calendar=fake_calendar, scopes=_cfs_only()
        )
    # 산출물은 남긴다(실패 은폐 아님) — build_report에 위반 기록
    report = json.loads(
        (financials_out_dir(data_dir, CORP) / BUILD_REPORT_FILENAME).read_text(encoding="utf-8")
    )
    identity = next(v for v in report["validations"] if v["name"] == "accounting_identity")
    assert not identity["passed"] and identity["violations"]
