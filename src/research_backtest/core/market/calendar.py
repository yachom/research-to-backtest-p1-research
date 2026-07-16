"""KRX 거래일 캘린더 — KOSPI 지수 거래일에서 구축 (README §4.3, 명세 A3 §4).

지수(KOSPI) 데이터의 거래일이 시장 캘린더다 — 종목별 거래 정지·상장일에
영향받지 않는다. ``core.dates.TradingCalendar`` 프로토콜을 만족하며,
``WeekdayCalendar``는 이제 테스트 전용이고 프로덕션 available_from 계산은
:class:`KrxTradingCalendar`를 쓴다(명세 A3 §4, DoD 5).
"""

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from research_backtest.core.exceptions import CalendarRangeError, DataValidationError

CALENDAR_FILENAME = "krx_trading_days.parquet"


def as_date(value: object) -> date:
    """parquet·pandas에서 읽힌 날짜 값을 datetime.date로 강제한다.

    pyarrow date32는 datetime.date로, timestamp는 pd.Timestamp/datetime으로
    읽힐 수 있어 왕복 일관성(명세 A3 §9)을 위해 한 곳에서 흡수한다.
    """
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    raise DataValidationError(f"날짜로 해석할 수 없는 값입니다: {value!r} ({type(value).__name__})")


def build_calendar_from_index(index_daily: pd.DataFrame) -> list[date]:
    """지수 일별 데이터(index=date)에서 거래일 목록을 추출한다 (명세 A3 §4).

    정렬·중복 제거된 오름차순 목록을 반환하며, collector가 이를
    ``data/normalized/market/calendar/krx_trading_days.parquet``로 저장한다.
    """
    if index_daily.empty:
        raise DataValidationError("지수 데이터가 비어 있어 캘린더를 만들 수 없습니다 (명세 A3 §4).")
    return sorted({as_date(value) for value in index_daily.index})


class KrxTradingCalendar:
    """KRX 거래일 캘린더 — KOSPI 지수 거래일에서 구축 (README §4.3의 실캘린더).

    core.dates.TradingCalendar 프로토콜을 만족한다. WeekdayCalendar는 이제
    테스트 전용이며 프로덕션 available_from 계산은 이 클래스를 쓴다.

    coverage 밖 조회는 :class:`CalendarRangeError`로 즉시 실패한다 — 주말
    로직으로 조용히 대체하면 룩어헤드·오정렬의 싹이 된다(명세 A3 §4).
    """

    def __init__(self, trading_days: Sequence[date]) -> None:
        """정렬·중복 제거해 보관한다 — 빈 목록은 ValueError로 거부."""
        if not trading_days:
            raise ValueError("거래일 목록이 비어 있어 캘린더를 만들 수 없습니다 (명세 A3 §4).")
        self._days: list[date] = sorted(set(trading_days))

    @property
    def coverage(self) -> tuple[date, date]:
        """캘린더가 아는 (첫 거래일, 마지막 거래일)."""
        return self._days[0], self._days[-1]

    def is_trading_day(self, d: date) -> bool:
        """d가 거래일인지 — coverage 밖이면 CalendarRangeError (명세 A3 §4)."""
        self._ensure_in_coverage(d)
        i = bisect_left(self._days, d)
        return i < len(self._days) and self._days[i] == d

    def next_trading_day(self, d: date) -> date:
        """d 이후 첫 거래일(strictly after) — README §4.3 available_from의 기준.

        d가 coverage 밖이거나 d 이후 거래일이 coverage 안에 없으면
        CalendarRangeError. bisect 사용 — 백테스트 루프에서 반복 호출된다.
        """
        self._ensure_in_coverage(d)
        i = bisect_right(self._days, d)
        if i >= len(self._days):
            raise CalendarRangeError(
                f"{d.isoformat()} 이후 거래일이 캘린더 coverage"
                f"({self._format_coverage()}) 밖입니다 — 지수 데이터를 더 수집하세요."
            )
        return self._days[i]

    @classmethod
    def from_parquet(cls, path: Path) -> "KrxTradingCalendar":
        """collector가 저장한 krx_trading_days.parquet(date 단일 컬럼)에서 복원한다."""
        if not path.exists():
            raise DataValidationError(
                f"거래일 캘린더 parquet이 없습니다: {path} "
                "(r2b collect-market으로 지수·캘린더를 먼저 수집 — KRX 로그인 필요)"
            )
        # engine 기본값 auto — pyarrow가 설치된 환경에서는 pyarrow로 읽는다 (명세 §9)
        frame = pd.read_parquet(path)
        if "date" not in frame.columns:
            raise DataValidationError(f"캘린더 parquet에 date 컬럼이 없습니다: {path}")
        return cls([as_date(value) for value in frame["date"]])

    # --- 내부 구현 ---------------------------------------------------------

    def _ensure_in_coverage(self, d: date) -> None:
        if d < self._days[0] or d > self._days[-1]:
            raise CalendarRangeError(
                f"{d.isoformat()}은 캘린더 coverage({self._format_coverage()}) 밖입니다 "
                "— 주말 로직으로 대체하지 않는다 (명세 A3 §4)."
            )

    def _format_coverage(self) -> str:
        first, last = self.coverage
        return f"{first.isoformat()}~{last.isoformat()}"
