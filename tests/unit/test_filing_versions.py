"""filing_versions 단위 테스트 — 버전 체인 구축·PIT 선택 (README §15, 명세 B4 §5).

합성 DartFiling 목록만 사용한다(오프라인, 네트워크 접근 없음). 첫 케이스는
DATA_NOTES.md B1+B2 실측 ⑤의 실제 접수번호 쌍(2020.12 사업보고서 원본
20210322000782 + [기재정정] 20210330000776)을 재현한다.
"""

import logging
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.filing_versions import (
    FilingVersionGroup,
    build_version_groups,
    current_version,
    filing_versions_path,
    load_version_groups,
    save_version_groups,
    visible_version,
)
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dates import WeekdayCalendar, available_from
from research_backtest.core.exceptions import DataValidationError

LOGGER_NAME = "r2b.dart.filing_versions"


def _filing(
    *,
    rcept_no: str,
    rcept_dt: date,
    report_type: PeriodicReportType | None,
    fiscal_period_end: date | None,
    is_correction: bool = False,
    correction_kind: str | None = None,
    report_nm: str = "사업보고서",
) -> DartFiling:
    return DartFiling(
        corp_code="00164779",
        corp_name="SK하이닉스",
        stock_code="000660",
        report_nm=report_nm,
        rcept_no=rcept_no,
        flr_nm="SK하이닉스",
        rcept_dt=rcept_dt,
        rm=None,
        report_type=report_type,
        fiscal_period_end=fiscal_period_end,
        is_correction=is_correction,
        correction_kind=correction_kind,
    )


def _weekend_gap_chain() -> tuple[DartFiling, DartFiling]:
    """원본(금요일 접수)+정정(월요일 접수) — available_from에 주말이 낀 케이스."""
    original = _filing(
        rcept_no="20240105000001",
        rcept_dt=date(2024, 1, 5),  # 금요일
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2023, 12, 31),
        report_nm="사업보고서 (2023.12)",
    )
    correction = _filing(
        rcept_no="20240115000002",
        rcept_dt=date(2024, 1, 15),  # 월요일
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2023, 12, 31),
        is_correction=True,
        correction_kind="기재정정",
        report_nm="[기재정정]사업보고서 (2023.12)",
    )
    return original, correction


# --- 원본 1 + 정정 2 체인 (DATA_NOTES B1+B2 실측 ⑤ 재현) ----------------------


def test_original_plus_two_corrections_chain_is_linked_correctly() -> None:
    original = _filing(
        rcept_no="20210322000782",
        rcept_dt=date(2021, 3, 22),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2020, 12, 31),
        report_nm="사업보고서 (2020.12)",
    )
    correction1 = _filing(
        rcept_no="20210330000776",
        rcept_dt=date(2021, 3, 30),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2020, 12, 31),
        is_correction=True,
        correction_kind="기재정정",
        report_nm="[기재정정]사업보고서 (2020.12)",
    )
    correction2 = _filing(
        rcept_no="20210407000555",
        rcept_dt=date(2021, 4, 7),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2020, 12, 31),
        is_correction=True,
        correction_kind="첨부정정",
        report_nm="[첨부정정]사업보고서 (2020.12)",
    )

    # 입력 순서를 뒤섞어도 (rcept_dt, rcept_no) 오름차순으로 정렬됨을 함께 확인
    groups = build_version_groups([correction2, original, correction1])

    assert len(groups) == 1
    group = groups[0]
    assert group.report_type == PeriodicReportType.ANNUAL
    assert group.fiscal_period_end == date(2020, 12, 31)
    assert [v.rcept_no for v in group.versions] == [
        "20210322000782",
        "20210330000776",
        "20210407000555",
    ]

    v0, v1, v2 = group.versions
    assert v0.original_rcept_no is None
    assert v0.supersedes_rcept_no is None
    assert v0.revision_type is None
    assert v0.is_latest_version is False

    assert v1.original_rcept_no == "20210322000782"
    assert v1.supersedes_rcept_no == "20210322000782"
    assert v1.revision_type == "기재정정"
    assert v1.is_latest_version is False

    assert v2.original_rcept_no == "20210322000782"
    assert v2.supersedes_rcept_no == "20210330000776"
    assert v2.revision_type == "첨부정정"
    assert v2.is_latest_version is True


# --- 정정 없음 그룹 ------------------------------------------------------------


def test_group_without_corrections_original_is_latest() -> None:
    only = _filing(
        rcept_no="20210517000100",
        rcept_dt=date(2021, 5, 17),
        report_type=PeriodicReportType.Q1,
        fiscal_period_end=date(2021, 3, 31),
        report_nm="분기보고서 (2021.03)",
    )

    groups = build_version_groups([only])

    assert len(groups) == 1
    [version] = groups[0].versions
    assert version.rcept_no == "20210517000100"
    assert version.is_latest_version is True
    assert version.original_rcept_no is None
    assert version.supersedes_rcept_no is None
    assert version.revision_type is None


# --- 원본 미수집(정정만 존재) --------------------------------------------------


def test_missing_original_group_has_none_original_and_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    correction_a = _filing(
        rcept_no="20220822000111",
        rcept_dt=date(2022, 8, 22),
        report_type=PeriodicReportType.HALF,
        fiscal_period_end=date(2022, 6, 30),
        is_correction=True,
        correction_kind="기재정정",
        report_nm="[기재정정]반기보고서 (2022.06)",
    )
    correction_b = _filing(
        rcept_no="20220825000222",
        rcept_dt=date(2022, 8, 25),
        report_type=PeriodicReportType.HALF,
        fiscal_period_end=date(2022, 6, 30),
        is_correction=True,
        correction_kind="첨부정정",
        report_nm="[첨부정정]반기보고서 (2022.06)",
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        groups = build_version_groups([correction_a, correction_b])

    assert len(groups) == 1
    versions = groups[0].versions
    assert [v.rcept_no for v in versions] == ["20220822000111", "20220825000222"]
    assert all(v.original_rcept_no is None for v in versions)  # 원본 미수집 — 전 버전 None
    assert versions[0].supersedes_rcept_no is None
    assert versions[1].supersedes_rcept_no == "20220822000111"  # 체인 자체는 유지
    assert versions[1].is_latest_version is True
    assert "원본 미수집" in caplog.text  # 명세 §2 "리포트에 표시"


def test_duplicate_original_logs_requires_review_and_processes_by_rcept_dt(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """원본(is_correction=False) 2건 중복 — REQUIRES_REVIEW 로그 + rcept_dt 순 처리(명세 §2)."""
    first_original = _filing(
        rcept_no="20230515000001",
        rcept_dt=date(2023, 5, 15),
        report_type=PeriodicReportType.Q1,
        fiscal_period_end=date(2023, 3, 31),
    )
    duplicate_original = _filing(
        rcept_no="20230516000002",
        rcept_dt=date(2023, 5, 16),
        report_type=PeriodicReportType.Q1,
        fiscal_period_end=date(2023, 3, 31),
    )

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        groups = build_version_groups([first_original, duplicate_original])

    assert len(groups) == 1
    versions = groups[0].versions
    assert [v.rcept_no for v in versions] == ["20230515000001", "20230516000002"]
    assert versions[0].original_rcept_no is None
    assert versions[1].original_rcept_no == "20230515000001"
    assert versions[1].supersedes_rcept_no == "20230515000001"
    assert versions[1].is_latest_version is True
    assert "REQUIRES_REVIEW" in caplog.text


# --- visible_version: as_of 전/사이/후 (주말 낀 available_from) ----------------


def test_visible_version_before_between_and_after_availability() -> None:
    original, correction = _weekend_gap_chain()
    [group] = build_version_groups([original, correction])
    calendar = WeekdayCalendar()

    # 금요일 접수 → 다음 거래일은 월요일 (주말 스킵 확인)
    assert available_from(date(2024, 1, 5), calendar) == date(2024, 1, 8)
    assert available_from(date(2024, 1, 15), calendar) == date(2024, 1, 16)

    # 원본 available_from(1/8) 이전 — 이용 가능한 버전 없음
    assert visible_version(group, as_of_date=date(2024, 1, 7), calendar=calendar) is None

    # 원본과 정정 사이 — 원본만 보임
    before_correction = visible_version(group, as_of_date=date(2024, 1, 10), calendar=calendar)
    assert before_correction is not None
    assert before_correction.rcept_no == "20240105000001"

    # 정정 available_from(1/16) 이후 — 정정이 보임
    after_correction = visible_version(group, as_of_date=date(2024, 1, 20), calendar=calendar)
    assert after_correction is not None
    assert after_correction.rcept_no == "20240115000002"


def test_visible_version_returns_none_when_group_empty_of_available_versions() -> None:
    original, correction = _weekend_gap_chain()
    [group] = build_version_groups([original, correction])
    calendar = WeekdayCalendar()

    assert visible_version(group, as_of_date=date(2023, 12, 31), calendar=calendar) is None


# --- current_version ≠ visible_version 사례 고정 -------------------------------


def test_current_version_differs_from_visible_version_mid_chain() -> None:
    original, correction = _weekend_gap_chain()
    [group] = build_version_groups([original, correction])
    calendar = WeekdayCalendar()

    current = current_version(group)
    assert current.rcept_no == "20240115000002"  # Current View — 항상 최신(정정)

    pit = visible_version(group, as_of_date=date(2024, 1, 10), calendar=calendar)
    assert pit is not None
    assert pit.rcept_no == "20240105000001"  # PIT — 그 시점엔 원본만 이용 가능

    assert current.rcept_no != pit.rcept_no


# --- report_type None filing 제외 처리 -----------------------------------------


def test_filings_without_report_type_or_period_end_are_excluded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    valid = _filing(
        rcept_no="20240320000001",
        rcept_dt=date(2024, 3, 20),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2023, 12, 31),
    )
    non_periodic = _filing(
        rcept_no="20240115000099",
        rcept_dt=date(2024, 1, 15),
        report_type=None,
        fiscal_period_end=None,
        report_nm="주요사항보고서(유상증자결정)",
    )
    unclassified_quarter = _filing(
        rcept_no="20240215000088",
        rcept_dt=date(2024, 2, 15),
        report_type=None,
        fiscal_period_end=date(2024, 2, 29),  # 비12월결산 분기말 — 분류 불가(report_type None)
        report_nm="분기보고서 (2024.02)",
    )

    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        groups = build_version_groups([valid, non_periodic, unclassified_quarter])

    assert len(groups) == 1
    assert groups[0].versions[0].rcept_no == "20240320000001"
    assert "제외" in caplog.text
    assert "2건" in caplog.text


# --- 저장·로드 (오프라인 왕복 — integration의 실데이터 케이스를 보완) ------------


def test_save_and_load_round_trip(tmp_path: Path) -> None:
    original = _filing(
        rcept_no="20210322000782",
        rcept_dt=date(2021, 3, 22),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2020, 12, 31),
        report_nm="사업보고서 (2020.12)",
    )
    correction = _filing(
        rcept_no="20210330000776",
        rcept_dt=date(2021, 3, 30),
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2020, 12, 31),
        is_correction=True,
        correction_kind="기재정정",
        report_nm="[기재정정]사업보고서 (2020.12)",
    )
    groups = build_version_groups([original, correction])

    path = save_version_groups("00164779", groups, data_dir=tmp_path)

    assert path == filing_versions_path(tmp_path, "00164779")
    assert path.exists()
    assert path.read_text(encoding="utf-8").strip().startswith("[")

    loaded = load_version_groups("00164779", data_dir=tmp_path)
    assert loaded == groups
    assert isinstance(loaded[0], FilingVersionGroup)


def test_load_missing_file_raises_data_validation_error(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError):
        load_version_groups("00000000", data_dir=tmp_path)
