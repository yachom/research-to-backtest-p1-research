"""E1 Evidence 빌더 unit 테스트 공용 픽스처 — A4 산출 parquet을 직접 합성한다.

E1의 입력은 A4 파이프라인의 **출력**(financial_metrics·quarterly/annual wide
parquet)이다. 전체 A4 파이프라인을 돌리는 대신 그 출력 스키마를 소형으로 직접
써서 PIT·부호 전환·유의도 로직을 결정적으로 검증한다(스키마는
core.financials.pipeline의 컬럼 정의와 정렬).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from research_backtest.core.financials.pipeline import (
    ANNUAL_FILENAME,
    METRICS_FILENAME,
    QUARTERLY_FILENAME,
    financials_out_dir,
)

# 11 registry 계정 (wide 컬럼)
ACCOUNTS = [
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
_Q_END = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}


def _quarter_end(year: int, quarter: int | None) -> date:
    if quarter is None:
        return date(year, 12, 31)
    month, day = _Q_END[quarter]
    return date(year, month, day)


def _rcept_no(rcept_dt: date) -> str:
    return f"{rcept_dt:%Y%m%d}000001"


def metric(
    metric_id: str,
    year: int,
    quarter: int,
    value: float,
    available_from: date,
    *,
    fs_scope: str = "CFS",
    period_end: date | None = None,
    rcept_dt: date | None = None,
    inputs_derived: bool = False,
) -> dict[str, Any]:
    """financial_metrics.parquet 1행 dict (테스트 편의 기본값 채움)."""
    rd = rcept_dt if rcept_dt is not None else available_from
    return {
        "metric_id": metric_id,
        "fs_scope": fs_scope,
        "fiscal_year": year,
        "fiscal_quarter": quarter,
        "period_end": period_end if period_end is not None else _quarter_end(year, quarter),
        "value": value,
        "rcept_no": _rcept_no(rd),
        "rcept_dt": rd,
        "available_from": available_from,
        "inputs_derived": inputs_derived,
    }


def wide(
    year: int,
    quarter: int | None,
    available_from: date,
    *,
    fs_scope: str = "CFS",
    period_end: date | None = None,
    rcept_dt: date | None = None,
    **accounts: int | None,
) -> dict[str, Any]:
    """quarterly/annual wide parquet 1행 dict. accounts 키는 canonical_id."""
    rd = rcept_dt if rcept_dt is not None else available_from
    row: dict[str, Any] = {
        "fs_scope": fs_scope,
        "fiscal_year": year,
        "period_start": date(year, 1, 1),
        "period_end": period_end if period_end is not None else _quarter_end(year, quarter),
        "rcept_no": _rcept_no(rd),
        "rcept_dt": rd,
        "available_from": available_from,
    }
    if quarter is not None:
        row["fiscal_quarter"] = quarter
    for name in ACCOUNTS:
        row[name] = accounts.get(name)
    return row


def _metrics_frame(rows: Sequence[dict[str, Any]]) -> pd.DataFrame:
    cols = [
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
    df = pd.DataFrame(list(rows), columns=cols)
    return df.astype(
        {
            "metric_id": "string",
            "fs_scope": "string",
            "fiscal_year": "int64",
            "fiscal_quarter": "Int64",
            "value": "float64",
            "rcept_no": "string",
            "inputs_derived": "bool",
        }
    )


def _wide_frame(rows: Sequence[dict[str, Any]], *, annual: bool) -> pd.DataFrame:
    lead = [
        "fs_scope",
        "fiscal_year",
        "period_start",
        "period_end",
        "rcept_no",
        "rcept_dt",
        "available_from",
    ]
    if not annual:
        lead.insert(2, "fiscal_quarter")
    df = pd.DataFrame(list(rows), columns=lead + ACCOUNTS)
    dtypes: dict[str, str] = {"fs_scope": "string", "fiscal_year": "int64", "rcept_no": "string"}
    if not annual:
        dtypes["fiscal_quarter"] = "Int64"
    for name in ACCOUNTS:
        dtypes[name] = "Int64"
    return df.astype(dtypes)


@pytest.fixture
def write_datasets(tmp_path: Path) -> Callable[..., Path]:
    """metrics·quarterly·annual parquet을 tmp data_dir에 써주는 팩토리 — data_dir 반환.

    입력은 **친화적 dict**다(테스트가 conftest 함수를 import하지 않도록 — 레포 관례).
    각 dict의 키는 :func:`metric`/:func:`wide`의 인자명과 같다:

    - ``metrics``: ``{"metric_id","year","quarter","value","available_from", ...}``
    - ``quarterly``: ``{"year","quarter","available_from", <account>=..., ...}``
    - ``annual``: ``{"year","available_from", <account>=..., ...}`` (quarter는 자동 None)
    """

    def _write(
        *,
        corp_code: str = "00000000",
        metrics: Sequence[dict[str, Any]] = (),
        quarterly: Sequence[dict[str, Any]] = (),
        annual: Sequence[dict[str, Any]] = (),
    ) -> Path:
        data_dir = tmp_path / "data"
        out = financials_out_dir(data_dir, corp_code)
        out.mkdir(parents=True, exist_ok=True)
        metric_rows = [metric(**d) for d in metrics]
        quarterly_rows = [wide(**d) for d in quarterly]
        annual_rows = [wide(quarter=None, **d) for d in annual]
        _metrics_frame(metric_rows).to_parquet(
            out / METRICS_FILENAME, engine="pyarrow", index=False
        )
        _wide_frame(quarterly_rows, annual=False).to_parquet(
            out / QUARTERLY_FILENAME, engine="pyarrow", index=False
        )
        _wide_frame(annual_rows, annual=True).to_parquet(
            out / ANNUAL_FILENAME, engine="pyarrow", index=False
        )
        return data_dir

    return _write
