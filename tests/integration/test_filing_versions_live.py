"""filing_versions 실호출 integration 테스트 (명세 B4 §5) — DART_API_KEY·DATA_DIR 없으면 skip.

DATA_NOTES.md B1+B2 실측 ⑤: SK하이닉스(00164779) 2020.12 사업보고서는 원본
(20210322000782)과 [기재정정](20210330000776) 두 접수번호가 모두 존재한다 —
이 테스트는 그 실제 정정 쌍이 버전 체인·PIT 선택으로 재현됨을 확인한다.

빌드 산출물은 tmp_path 계열에 쓴다(실데이터 디렉토리 오염 방지). DATA_DIR은
KRX 거래일 캘린더(A3 산출, 실제 available_from 계산용)를 읽는 데만 쓰인다.
실행: ``DART_API_KEY=... DATA_DIR=/…/data pytest -m integration
tests/integration/test_filing_versions_live.py``.
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.filing_versions import (
    FilingVersionGroup,
    build_and_save,
    current_version,
    load_version_groups,
    visible_version,
)
from research_backtest.core.market.calendar import CALENDAR_FILENAME, KrxTradingCalendar

pytestmark = pytest.mark.integration

SK_HYNIX_CORP_CODE = "00164779"
AS_OF_BUILD = date(2021, 12, 31)
LOOKBACK_YEARS = 2

ORIGINAL_RCEPT_NO = "20210322000782"  # 사업보고서 (2020.12) 원본
CORRECTION_RCEPT_NO = "20210330000776"  # [기재정정]사업보고서 (2020.12)


@pytest.fixture(scope="module")
def dart_client() -> Iterator[DartClient]:
    settings = get_settings()
    if not settings.dart_api_key:
        pytest.skip("DART_API_KEY 미설정 — integration 테스트 생략")
    with DartClient(settings.dart_api_key) as client:
        yield client


@pytest.fixture(scope="module")
def real_calendar() -> KrxTradingCalendar:
    """실제 KRX 거래일 캘린더(A3 산출) — DATA_DIR에 없으면 skip."""
    data_dir = get_settings().data_dir
    calendar_path = data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME
    if not calendar_path.exists():
        pytest.skip(f"DATA_DIR 캘린더 없음 — {calendar_path} (r2b collect-market 선행 필요)")
    return KrxTradingCalendar.from_parquet(calendar_path)


@pytest.fixture(scope="module")
def out_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("filing_versions")


@pytest.fixture(scope="module")
def built_groups(dart_client: DartClient, out_dir: Path) -> list[FilingVersionGroup]:
    """실호출로 00164779 정정공시 버전 그래프를 빌드·저장한다 (명세 §4)."""
    return build_and_save(
        SK_HYNIX_CORP_CODE,
        client=dart_client,
        data_dir=out_dir,
        as_of_date=AS_OF_BUILD,
        lookback_years=LOOKBACK_YEARS,
    )


def _annual_2020_group(groups: list[FilingVersionGroup]) -> FilingVersionGroup:
    return next(
        g
        for g in groups
        if g.report_type == PeriodicReportType.ANNUAL and g.fiscal_period_end == date(2020, 12, 31)
    )


def test_2020_annual_original_and_correction_are_chained(
    built_groups: list[FilingVersionGroup],
) -> None:
    group = _annual_2020_group(built_groups)
    rcept_nos = [v.rcept_no for v in group.versions]
    assert ORIGINAL_RCEPT_NO in rcept_nos
    assert CORRECTION_RCEPT_NO in rcept_nos
    # 원본이 정정보다 앞선다(오름차순 정렬)
    assert rcept_nos.index(ORIGINAL_RCEPT_NO) < rcept_nos.index(CORRECTION_RCEPT_NO)

    original = next(v for v in group.versions if v.rcept_no == ORIGINAL_RCEPT_NO)
    correction = next(v for v in group.versions if v.rcept_no == CORRECTION_RCEPT_NO)

    assert original.original_rcept_no is None
    assert original.revision_type is None
    assert original.is_latest_version is False

    assert correction.original_rcept_no == ORIGINAL_RCEPT_NO
    assert correction.supersedes_rcept_no == ORIGINAL_RCEPT_NO
    assert correction.revision_type == "기재정정"
    assert correction.is_latest_version is True  # 정정이 latest


def test_visible_version_reproduces_pit_selection(
    built_groups: list[FilingVersionGroup], real_calendar: KrxTradingCalendar
) -> None:
    """README §15.3 재현: 3/22(월) 접수→3/23 이용가능, 3/30(화) 접수→3/31 이용가능."""
    group = _annual_2020_group(built_groups)

    before_correction_available = visible_version(
        group, as_of_date=date(2021, 3, 25), calendar=real_calendar
    )
    assert before_correction_available is not None
    assert before_correction_available.rcept_no == ORIGINAL_RCEPT_NO

    after_correction_available = visible_version(
        group, as_of_date=date(2021, 4, 5), calendar=real_calendar
    )
    assert after_correction_available is not None
    assert after_correction_available.rcept_no == CORRECTION_RCEPT_NO

    # as_of가 원본 available_from보다도 이르면 아무 버전도 보이지 않는다
    assert visible_version(group, as_of_date=date(2021, 3, 22), calendar=real_calendar) is None

    # Current View는 as_of와 무관하게 항상 최신(정정) — PIT와 대비
    assert current_version(group).rcept_no == CORRECTION_RCEPT_NO


def test_save_and_load_round_trip(out_dir: Path, built_groups: list[FilingVersionGroup]) -> None:
    loaded = load_version_groups(SK_HYNIX_CORP_CODE, data_dir=out_dir)
    assert loaded == built_groups
    assert _annual_2020_group(loaded) == _annual_2020_group(built_groups)
