"""KRX 거래일 캘린더 단위 테스트 (명세 A3 §4, §7)."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.dates import available_from
from research_backtest.core.exceptions import CalendarRangeError, DataValidationError
from research_backtest.core.market.calendar import KrxTradingCalendar, build_calendar_from_index

# 거래일 8일 — 주말(1/6~7) 갭 + 공휴일 갭(1/9 임의 휴장)
TRADING_DAYS = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 10),
    date(2024, 1, 11),
    date(2024, 1, 12),
]


@pytest.fixture
def calendar() -> KrxTradingCalendar:
    return KrxTradingCalendar(TRADING_DAYS)


def test_is_trading_day_weekend_and_holiday_gap(calendar: KrxTradingCalendar) -> None:
    assert calendar.is_trading_day(date(2024, 1, 2))
    assert not calendar.is_trading_day(date(2024, 1, 6))  # 토요일
    assert not calendar.is_trading_day(date(2024, 1, 9))  # 휴장일(공휴일 갭)


def test_next_trading_day_skips_weekend(calendar: KrxTradingCalendar) -> None:
    assert calendar.next_trading_day(date(2024, 1, 5)) == date(2024, 1, 8)  # 금 → 월


def test_next_trading_day_skips_holiday(calendar: KrxTradingCalendar) -> None:
    assert calendar.next_trading_day(date(2024, 1, 8)) == date(2024, 1, 10)


def test_next_trading_day_is_strictly_after(calendar: KrxTradingCalendar) -> None:
    # 입력이 거래일이어도 그 날이 아니라 다음 거래일이다 (README §4.3)
    assert calendar.next_trading_day(date(2024, 1, 2)) == date(2024, 1, 3)
    assert calendar.next_trading_day(date(2024, 1, 6)) == date(2024, 1, 8)


def test_available_from_uses_krx_calendar(calendar: KrxTradingCalendar) -> None:
    # TradingCalendar 프로토콜 만족 — 금요일 접수 → 다음 월요일 (DoD 4 형태)
    assert available_from(date(2024, 1, 5), calendar) == date(2024, 1, 8)


def test_out_of_coverage_raises(calendar: KrxTradingCalendar) -> None:
    with pytest.raises(CalendarRangeError):
        calendar.is_trading_day(date(2023, 12, 29))
    with pytest.raises(CalendarRangeError):
        calendar.is_trading_day(date(2024, 1, 13))
    with pytest.raises(CalendarRangeError):
        calendar.next_trading_day(date(2023, 12, 29))


def test_next_trading_day_at_coverage_end_raises(calendar: KrxTradingCalendar) -> None:
    # 마지막 거래일의 다음 거래일은 coverage 밖 — 주말 로직 대체 금지 (명세 §4)
    with pytest.raises(CalendarRangeError):
        calendar.next_trading_day(date(2024, 1, 12))


def test_coverage_bounds(calendar: KrxTradingCalendar) -> None:
    assert calendar.coverage == (date(2024, 1, 2), date(2024, 1, 12))


def test_rejects_empty_days() -> None:
    with pytest.raises(ValueError):
        KrxTradingCalendar([])


def test_dedupes_and_sorts_input() -> None:
    calendar = KrxTradingCalendar([date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)])
    assert calendar.coverage == (date(2024, 1, 2), date(2024, 1, 3))
    assert calendar.next_trading_day(date(2024, 1, 2)) == date(2024, 1, 3)


def test_from_parquet_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "krx_trading_days.parquet"
    pd.DataFrame({"date": TRADING_DAYS}).to_parquet(path, engine="pyarrow", index=False)
    calendar = KrxTradingCalendar.from_parquet(path)
    assert calendar.coverage == (date(2024, 1, 2), date(2024, 1, 12))
    assert not calendar.is_trading_day(date(2024, 1, 9))
    assert calendar.next_trading_day(date(2024, 1, 5)) == date(2024, 1, 8)


def test_from_parquet_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError):
        KrxTradingCalendar.from_parquet(tmp_path / "missing.parquet")


def test_build_calendar_from_index_sorts_and_dedupes() -> None:
    frame = pd.DataFrame(
        {"close": [1.0, 2.0, 3.0]},
        index=pd.Index([date(2024, 1, 3), date(2024, 1, 2), date(2024, 1, 3)], name="date"),
    )
    assert build_calendar_from_index(frame) == [date(2024, 1, 2), date(2024, 1, 3)]


def test_build_calendar_from_index_rejects_empty() -> None:
    with pytest.raises(DataValidationError):
        build_calendar_from_index(pd.DataFrame({"close": []}))
