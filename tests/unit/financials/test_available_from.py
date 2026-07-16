"""available_from 부여 단위 테스트 (명세 A4 §5, §9)."""

from datetime import date

import pytest

from research_backtest.core.exceptions import CalendarRangeError
from research_backtest.core.financials.quarterly import REPORTED, Fact, apply_available_from
from research_backtest.core.market.calendar import KrxTradingCalendar


def _fact(contributing: list[str], *, quarter: int = 1) -> Fact:
    return Fact(
        canonical_id="revenue",
        fs_scope="CFS",
        sj_div="CIS",
        fiscal_year=2024,
        fiscal_quarter=quarter,
        period_start=date(2024, 1, 1),
        period_end=date(2024, 3, 31),
        value=100,
        value_type=REPORTED,
        source_account_id="id",
        source_account_nm="nm",
        contributing_rcept_nos=contributing,
    )


def test_rcept_to_available_from_friday_to_monday(fake_calendar: KrxTradingCalendar) -> None:
    # 2024-05-17은 금요일 → 다음 거래일은 월요일 2024-05-20 (가짜 캘린더=평일)
    fact = _fact(["20240517000001"])
    apply_available_from([fact], fake_calendar)
    assert fact.rcept_dt == date(2024, 5, 17)
    assert fact.available_from == date(2024, 5, 20)
    assert fact.rcept_no == "20240517000001"


def test_derived_available_from_is_max_of_inputs(fake_calendar: KrxTradingCalendar) -> None:
    # 파생값: 기여 보고서들의 available_from 중 max (가장 늦게 공개된 입력 기준)
    fact = _fact(["20240516000001", "20250320000001"], quarter=4)
    apply_available_from([fact], fake_calendar)
    assert fact.rcept_no == "20250320000001"  # 더 늦은 보고서
    assert fact.rcept_dt == date(2025, 3, 20)
    assert fact.available_from == fake_calendar.next_trading_day(date(2025, 3, 20))
    assert fact.available_from > fake_calendar.next_trading_day(date(2024, 5, 16))


def test_available_from_always_after_period_end(fake_calendar: KrxTradingCalendar) -> None:
    fact = _fact(["20240516000001"])
    apply_available_from([fact], fake_calendar)
    assert fact.available_from is not None and fact.available_from > fact.period_end


def test_out_of_coverage_raises(fake_calendar: KrxTradingCalendar) -> None:
    # coverage(2020~2027) 밖 → CalendarRangeError (조용한 주말 대체 금지)
    fact = _fact(["20300101000001"])
    with pytest.raises(CalendarRangeError):
        apply_available_from([fact], fake_calendar)
