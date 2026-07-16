"""시장 데이터 실호출 integration 테스트 (명세 A3 §7).

- 무로그인 경로(수정주가 OHLCV)는 항상 실행된다 — KRX 로그인 불필요(명세 §0).
- KRX 로그인 경로(투자자 수급·지수·캘린더)는 KRX_ID/KRX_PW 미설정 시 skip.
- data_dir은 항상 tmp_path를 사용한다 — 실 data/ 오염 금지.
- pykrx가 stdout에 찍는 "KRX 로그인 실패…" 경고는 허용된 소음이다.
"""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.market.calendar import KrxTradingCalendar, build_calendar_from_index
from research_backtest.core.market.collector import (
    MarketCollectionSummary,
    collect_market_data,
    market_calendar_path,
    market_normalized_stock_dir,
    market_raw_stock_dir,
)
from research_backtest.core.market.source import (
    INVESTOR_REQUIRED_COLUMNS,
    OHLCV_COLUMNS,
    PykrxSource,
)

pytestmark = pytest.mark.integration

SK_HYNIX = "000660"
KOSPI = "1001"


def _collect_january(source: PykrxSource, data_dir: Path) -> MarketCollectionSummary:
    return collect_market_data(
        source,
        stock_code=SK_HYNIX,
        index_code=KOSPI,
        from_date=date(2024, 1, 2),
        to_date=date(2024, 1, 31),
        data_dir=data_dir,
    )


# --- 무로그인 경로 (항상 실행 — 명세 §7 integration 1) ---------------------------


def test_unauth_ohlcv_live_contract() -> None:
    frame = PykrxSource().fetch_ohlcv(SK_HYNIX, date(2024, 1, 2), date(2024, 1, 31))
    assert 19 <= len(frame) <= 23  # 2024-01월 거래일 수
    assert list(frame.columns) == OHLCV_COLUMNS
    assert frame.index.name == "date"
    assert all(isinstance(value, date) for value in frame.index)
    assert (frame["close"] > 0).all()


def test_collect_partial_mode_and_cache_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """자격증명 없는 부분 수집 end-to-end + 재실행 캐시 (명세 §3.3, DoD 2~3)."""
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    source = PykrxSource()

    summary = _collect_january(source, tmp_path)
    assert {o.dataset: o.result for o in summary.outcomes} == {
        "OHLCV": "FETCHED",
        "INVESTOR_VALUE": "SKIPPED_NO_AUTH",
        "INDEX": "SKIPPED_NO_AUTH",
        "CALENDAR": "SKIPPED_NO_AUTH",
        "DAILY_MERGED": "BUILT",
    }
    daily = pd.read_parquet(market_normalized_stock_dir(tmp_path, SK_HYNIX) / "daily.parquet")
    assert list(daily.columns) == ["date", *OHLCV_COLUMNS]  # 부분 모드 — 가격 컬럼만
    assert not market_calendar_path(tmp_path).exists()

    # 재실행 — 소스 재호출 없이 캐시 히트 (meta mtime 불변으로 검증)
    meta_path = market_raw_stock_dir(tmp_path, SK_HYNIX) / "ohlcv.meta.json"
    mtime_before = meta_path.stat().st_mtime_ns
    second = _collect_january(source, tmp_path)
    ohlcv_again = next(o for o in second.outcomes if o.dataset == "OHLCV")
    assert ohlcv_again.result == "CACHED"
    assert meta_path.stat().st_mtime_ns == mtime_before


# --- KRX 로그인 경로 (KRX_ID/KRX_PW 없으면 skip — 명세 §7 integration 2) ---------


@pytest.fixture
def krx_source() -> PykrxSource:
    settings = get_settings()
    if not (settings.krx_id and settings.krx_pw):
        pytest.skip("KRX_ID/KRX_PW 미설정 — KRX 로그인 integration 생략 (명세 A3 §7)")
    return PykrxSource(krx_id=settings.krx_id, krx_pw=settings.krx_pw)


def test_krx_investor_value_live_contract(krx_source: PykrxSource) -> None:
    frame = krx_source.fetch_investor_value(SK_HYNIX, date(2024, 1, 2), date(2024, 1, 12))
    assert set(INVESTOR_REQUIRED_COLUMNS) <= set(frame.columns)
    assert frame.index.name == "date"
    assert len(frame) >= 5


def test_krx_index_live_and_calendar_excludes_holidays(krx_source: PykrxSource) -> None:
    """지수 실수집 → 캘린더가 2025-01-01(휴장)·주말을 제외하는지 (명세 §7, DoD 4)."""
    frame = krx_source.fetch_index_ohlcv(KOSPI, date(2024, 12, 23), date(2025, 1, 10))
    assert set(OHLCV_COLUMNS) <= set(frame.columns)

    days = build_calendar_from_index(frame)
    assert date(2025, 1, 1) not in days  # 신정 휴장
    assert all(day.weekday() < 5 for day in days)  # 주말 미포함

    calendar = KrxTradingCalendar(days)
    assert not calendar.is_trading_day(date(2025, 1, 1))
