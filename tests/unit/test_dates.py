"""README §4.3, §22.1 이용 가능일 규칙 테스트."""

from datetime import date

from research_backtest.core.dates import WeekdayCalendar, available_from


def test_next_trading_day_skips_weekend() -> None:
    cal = WeekdayCalendar()
    # 2025-11-14은 금요일 → 다음 거래일은 월요일
    assert cal.next_trading_day(date(2025, 11, 14)) == date(2025, 11, 17)


def test_next_trading_day_on_weekday() -> None:
    cal = WeekdayCalendar()
    assert cal.next_trading_day(date(2025, 11, 11)) == date(2025, 11, 12)


def test_available_from_matches_readme_example() -> None:
    # README §22.1 예시: 접수일 2025-11-14 → 이용 가능일 2025-11-17
    cal = WeekdayCalendar()
    assert available_from(date(2025, 11, 14), cal) == date(2025, 11, 17)


def test_available_from_is_strictly_after_filing_date() -> None:
    cal = WeekdayCalendar()
    filing = date(2025, 3, 18)
    assert available_from(filing, cal) > filing
