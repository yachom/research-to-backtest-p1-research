"""날짜 유틸리티 및 거래일 캘린더 인터페이스 (README §4)."""

from datetime import date, timedelta
from typing import Protocol


class TradingCalendar(Protocol):
    """거래일 캘린더 인터페이스.

    available_from(공시 접수일 다음 거래일, README §4.3) 계산의 기준.
    KRX 휴장일을 반영한 실제 구현은
    :class:`core.market.calendar.KrxTradingCalendar`다(명세 A3 §4).
    """

    def is_trading_day(self, d: date) -> bool: ...

    def next_trading_day(self, d: date) -> date: ...


class WeekdayCalendar:
    """주말만 제외하는 캘린더 — KRX 공휴일 미반영, **테스트 전용**.

    프로덕션 available_from 계산은 KrxTradingCalendar를 쓴다(명세 A3 §4,
    DoD 5). 실데이터 백테스트에 사용하지 않는다.
    """

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5

    def next_trading_day(self, d: date) -> date:
        nxt = d + timedelta(days=1)
        while not self.is_trading_day(nxt):
            nxt += timedelta(days=1)
        return nxt


def available_from(filing_date: date, calendar: TradingCalendar) -> date:
    """공시 정보 이용 가능일 = 접수일 다음 거래일 (README §4.3, §22.1)."""
    return calendar.next_trading_day(filing_date)
