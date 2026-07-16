"""실데이터 재무 빌드 integration 테스트 (명세 A4 §9) — jsonl·캘린더 없으면 skip.

실행: ``DATA_DIR=/…/data pytest -m integration tests/integration/test_financials_build.py``
(A4는 API 호출 없음 — DART_API_KEY 불필요, DATA_DIR만 있으면 된다).

빌드 산출은 실데이터 오염을 피해 tmp data_dir에 쓰되, 입력(jsonl·캘린더)은
실데이터를 복사해 쓴다.
"""

import shutil
from collections.abc import Iterator
from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.financials.pipeline import (
    METRICS_FILENAME,
    QUARTERLY_FILENAME,
    build_financial_datasets,
    financials_out_dir,
)
from research_backtest.core.market.calendar import CALENDAR_FILENAME, KrxTradingCalendar

pytestmark = pytest.mark.integration

SK_HYNIX = "00164779"
RAW_JSONL = "financial_api_raw.jsonl"


@pytest.fixture(scope="module")
def real_data_dir() -> Path:
    data_dir = get_settings().data_dir
    jsonl = data_dir / "raw" / "dart" / "financials" / SK_HYNIX / RAW_JSONL
    calendar = data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME
    if not jsonl.exists() or not calendar.exists():
        pytest.skip(f"실데이터 없음(jsonl·캘린더) — DATA_DIR 확인: {data_dir}")
    return data_dir


@pytest.fixture(scope="module")
def build_dir(real_data_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    tmp = tmp_path_factory.mktemp("financials_build")
    raw_dir = tmp / "raw" / "dart" / "financials" / SK_HYNIX
    raw_dir.mkdir(parents=True)
    shutil.copy(
        real_data_dir / "raw" / "dart" / "financials" / SK_HYNIX / RAW_JSONL, raw_dir / RAW_JSONL
    )
    cal_dir = tmp / "normalized" / "market" / "calendar"
    cal_dir.mkdir(parents=True)
    shutil.copy(
        real_data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME,
        cal_dir / CALENDAR_FILENAME,
    )
    build_financial_datasets(SK_HYNIX, data_dir=tmp)  # 검증 통과 시 예외 없음
    yield financials_out_dir(tmp, SK_HYNIX)


def test_all_validations_pass_and_full_coverage(build_dir: Path, real_data_dir: Path) -> None:
    import json

    report = json.loads((build_dir / "build_report.json").read_text(encoding="utf-8"))
    for check in report["validations"]:
        assert check["passed"], f"{check['name']} 위반: {check['violations'][:3]}"
    assert report["coverage"]["annual_required_complete"], report["coverage"][
        "missing_annual_required"
    ]
    assert report["coverage"]["recent_quarters_income_complete"]
    assert report["matching"]["unresolved"] == []
    assert report["derivation_gaps"] == []


def test_annual_5_years_present(build_dir: Path) -> None:
    from research_backtest.core.financials.pipeline import ANNUAL_FILENAME

    df = pd.read_parquet(build_dir / ANNUAL_FILENAME)
    cfs = df[df.fs_scope == "CFS"]
    assert set(cfs["fiscal_year"]) >= {2021, 2022, 2023, 2024, 2025}
    for col in ["revenue", "operating_income", "net_income", "total_assets"]:
        assert cfs[cfs.fiscal_year.isin([2021, 2022, 2023, 2024, 2025])][col].notna().all()


def test_recent_8_quarters_operating_income_yoy(build_dir: Path, real_data_dir: Path) -> None:
    metrics = pd.read_parquet(build_dir / METRICS_FILENAME)
    op = metrics[(metrics.metric_id == "operating_income_yoy") & (metrics.fs_scope == "CFS")]
    recent = op.sort_values(["fiscal_year", "fiscal_quarter"]).tail(8)
    assert len(recent) >= 8
    assert recent["value"].notna().all()

    # available_from은 실제 KRX 거래일이어야 한다 (룩어헤드 방지, 명세 §5)
    calendar = KrxTradingCalendar.from_parquet(
        real_data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME
    )
    for _, row in recent.iterrows():
        assert calendar.is_trading_day(row["available_from"])
        assert row["available_from"] > row["period_end"]


def test_2024_yoy_positive_from_2023_losses(build_dir: Path) -> None:
    # 2023 분기 영업이익 적자 → 2024 YoY가 abs 분모 규약으로 양수 (명세 §6)
    metrics = pd.read_parquet(build_dir / METRICS_FILENAME)
    op_2024 = metrics[
        (metrics.metric_id == "operating_income_yoy")
        & (metrics.fs_scope == "CFS")
        & (metrics.fiscal_year == 2024)
    ]
    assert len(op_2024) == 4
    assert (op_2024["value"] > 0).all()


def test_cf_single_quarter_sums_to_annual(build_dir: Path) -> None:
    # CF 단독분기(차분)의 합 == 연간 (telescoping, DATA_NOTES 후보 검증)
    from research_backtest.core.financials.pipeline import ANNUAL_FILENAME

    quarterly = pd.read_parquet(build_dir / QUARTERLY_FILENAME)
    annual = pd.read_parquet(build_dir / ANNUAL_FILENAME)
    for year in [2021, 2022, 2023, 2024, 2025]:
        q_sum = quarterly[(quarterly.fs_scope == "CFS") & (quarterly.fiscal_year == year)][
            "operating_cash_flow"
        ].sum()
        a_val = annual[(annual.fs_scope == "CFS") & (annual.fiscal_year == year)][
            "operating_cash_flow"
        ].iloc[0]
        assert q_sum == a_val, f"{year}: 분기합 {q_sum} != 연간 {a_val}"
