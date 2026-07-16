"""collector 캐시·부분 수집·병합 단위 테스트 — FakeSource로 pykrx 미호출 (명세 A3 §3, §7)."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.exceptions import DataValidationError, MarketAuthError
from research_backtest.core.market.collector import (
    DAILY_MERGED_COLUMNS,
    MarketCollectionSummary,
    collect_market_data,
    market_calendar_path,
    market_normalized_index_dir,
    market_normalized_stock_dir,
    market_raw_index_dir,
    market_raw_stock_dir,
)
from research_backtest.core.market.source import OHLCV_COLUMNS

STOCK = "000660"
INDEX = "1001"

# 거래일 8일, 주말(1/6~7) 낀 범위 (명세 §7 FakeSource)
TRADING_DAYS = [
    date(2024, 1, 2),
    date(2024, 1, 3),
    date(2024, 1, 4),
    date(2024, 1, 5),
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
    date(2024, 1, 11),
]
FROM = TRADING_DAYS[0]
TO = TRADING_DAYS[-1]


def _ohlcv_frame(days: list[date]) -> pd.DataFrame:
    n = len(days)
    return pd.DataFrame(
        {
            "open": [100.0 + i for i in range(n)],
            "high": [110.0 + i for i in range(n)],
            "low": [90.0 + i for i in range(n)],
            "close": [105.0 + i for i in range(n)],
            "volume": [1000 + i for i in range(n)],
        },
        index=pd.Index(days, name="date"),
    )


def _investor_frame(days: list[date]) -> pd.DataFrame:
    n = len(days)
    return pd.DataFrame(
        {
            "foreign_net_buy_value": [10_000 * (i + 1) for i in range(n)],
            "institution_net_buy_value": [-5_000 * (i + 1) for i in range(n)],
            "individual_net_buy_value": [-5_000 * i for i in range(n)],
        },
        index=pd.Index(days, name="date"),
    )


def _slice(frame: pd.DataFrame, from_date: date, to_date: date) -> pd.DataFrame:
    mask = [from_date <= day <= to_date for day in frame.index]
    return frame.loc[mask].copy()


class FakeSource:
    """명세 §7의 FakeSource — 작은 고정 DataFrame 반환, 호출 기록.

    authorized=False면 investor·index에서 :class:`MarketAuthError`를 던져
    자격증명 없는 PykrxSource와 같은 행동을 흉내낸다.
    """

    def __init__(
        self,
        *,
        authorized: bool = True,
        ohlcv: pd.DataFrame | None = None,
        investor: pd.DataFrame | None = None,
        index: pd.DataFrame | None = None,
    ) -> None:
        self.authorized = authorized
        self._ohlcv = _ohlcv_frame(TRADING_DAYS) if ohlcv is None else ohlcv
        # 기본 investor는 앞 6거래일만 — left-join의 NaN 경로를 자연 검증
        self._investor = _investor_frame(TRADING_DAYS[:6]) if investor is None else investor
        self._index = _ohlcv_frame(TRADING_DAYS) if index is None else index
        self.ohlcv_calls: list[tuple[str, date, date]] = []
        self.investor_calls: list[tuple[str, date, date]] = []
        self.index_calls: list[tuple[str, date, date]] = []

    def fetch_ohlcv(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        self.ohlcv_calls.append((stock_code, from_date, to_date))
        return _slice(self._ohlcv, from_date, to_date)

    def fetch_investor_value(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        if not self.authorized:
            raise MarketAuthError("KRX 로그인 필요 (FakeSource)")
        self.investor_calls.append((stock_code, from_date, to_date))
        return _slice(self._investor, from_date, to_date)

    def fetch_index_ohlcv(self, index_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        if not self.authorized:
            raise MarketAuthError("KRX 로그인 필요 (FakeSource)")
        self.index_calls.append((index_code, from_date, to_date))
        return _slice(self._index, from_date, to_date)


def _collect(
    source: FakeSource,
    data_dir: Path,
    *,
    from_date: date = FROM,
    to_date: date = TO,
    force: bool = False,
    sleeps: list[float] | None = None,
) -> MarketCollectionSummary:
    return collect_market_data(
        source,
        stock_code=STOCK,
        index_code=INDEX,
        from_date=from_date,
        to_date=to_date,
        data_dir=data_dir,
        force=force,
        min_interval_seconds=0.3,
        sleep=(sleeps.append if sleeps is not None else lambda _s: None),
    )


def _results(summary: MarketCollectionSummary) -> dict[str, str]:
    return {outcome.dataset: outcome.result for outcome in summary.outcomes}


# --- 첫 수집·저장 레이아웃 (명세 §3.1·§3.3) -----------------------------------


def test_first_collect_fetches_all_and_writes_layout(tmp_path: Path) -> None:
    source = FakeSource()
    summary = _collect(source, tmp_path)

    assert _results(summary) == {
        "OHLCV": "FETCHED",
        "INVESTOR_VALUE": "FETCHED",
        "INDEX": "FETCHED",
        "CALENDAR": "BUILT",
        "DAILY_MERGED": "BUILT",
    }
    stock_dir = market_raw_stock_dir(tmp_path, STOCK)
    index_dir = market_raw_index_dir(tmp_path, INDEX)
    assert (stock_dir / "ohlcv.parquet").exists()
    assert (stock_dir / "ohlcv.meta.json").exists()
    assert (stock_dir / "investor_value.parquet").exists()
    assert (stock_dir / "investor_value.meta.json").exists()
    assert (index_dir / "ohlcv.parquet").exists()
    assert (index_dir / "ohlcv.meta.json").exists()
    assert (market_normalized_stock_dir(tmp_path, STOCK) / "daily.parquet").exists()
    assert (market_normalized_index_dir(tmp_path, INDEX) / "daily.parquet").exists()
    assert market_calendar_path(tmp_path).exists()

    ohlcv = next(o for o in summary.outcomes if o.dataset == "OHLCV")
    assert ohlcv.row_count == 8
    assert (ohlcv.date_min, ohlcv.date_max) == (FROM, TO)
    calendar = next(o for o in summary.outcomes if o.dataset == "CALENDAR")
    assert calendar.row_count == 8


# --- 캐시 규칙 (명세 §3.2·§7) --------------------------------------------------


def test_second_collect_same_range_is_cached_without_source_calls(tmp_path: Path) -> None:
    source = FakeSource()
    _collect(source, tmp_path)
    summary = _collect(source, tmp_path)

    assert _results(summary) == {
        "OHLCV": "CACHED",
        "INVESTOR_VALUE": "CACHED",
        "INDEX": "CACHED",
        "CALENDAR": "BUILT",
        "DAILY_MERGED": "BUILT",
    }
    assert len(source.ohlcv_calls) == 1
    assert len(source.investor_calls) == 1
    assert len(source.index_calls) == 1


def test_narrower_request_is_cache_hit(tmp_path: Path) -> None:
    source = FakeSource()
    _collect(source, tmp_path)
    summary = _collect(source, tmp_path, from_date=date(2024, 1, 4), to_date=date(2024, 1, 8))
    assert _results(summary)["OHLCV"] == "CACHED"
    assert len(source.ohlcv_calls) == 1


def test_range_extension_refetches_union_of_request_and_stored(tmp_path: Path) -> None:
    source = FakeSource()
    _collect(source, tmp_path, from_date=date(2024, 1, 2), to_date=date(2024, 1, 5))
    summary = _collect(source, tmp_path, from_date=date(2024, 1, 4), to_date=date(2024, 1, 11))

    assert _results(summary)["OHLCV"] == "FETCHED"
    # 재수집 범위 = 요청·저장 범위의 합집합 (명세 §3.2)
    assert source.ohlcv_calls[-1] == (STOCK, date(2024, 1, 2), date(2024, 1, 11))
    ohlcv = next(o for o in summary.outcomes if o.dataset == "OHLCV")
    assert ohlcv.row_count == 8


def test_force_download_refetches_despite_cache(tmp_path: Path) -> None:
    source = FakeSource()
    _collect(source, tmp_path)
    summary = _collect(source, tmp_path, force=True)
    assert _results(summary)["OHLCV"] == "FETCHED"
    assert len(source.ohlcv_calls) == 2


def test_parquet_without_meta_is_cache_miss(tmp_path: Path) -> None:
    # meta가 커밋 마커 — parquet만 있으면 재수집한다 (명세 §3.2)
    source = FakeSource()
    _collect(source, tmp_path)
    (market_raw_stock_dir(tmp_path, STOCK) / "ohlcv.meta.json").unlink()
    summary = _collect(source, tmp_path)
    assert _results(summary)["OHLCV"] == "FETCHED"
    assert len(source.ohlcv_calls) == 2


def test_sleep_only_between_actual_source_calls(tmp_path: Path) -> None:
    source = FakeSource()
    sleeps: list[float] = []
    _collect(source, tmp_path, sleeps=sleeps)
    assert sleeps == [0.3, 0.3]  # 3회 호출 사이 2회 대기 (명세 §3.2)

    sleeps.clear()
    _collect(source, tmp_path, sleeps=sleeps)
    assert sleeps == []  # 전부 캐시 히트 — 대기 없음


# --- 부분 수집 모드 (명세 §0·§3.3) ---------------------------------------------


def test_partial_mode_without_credentials(tmp_path: Path) -> None:
    source = FakeSource(authorized=False)
    summary = _collect(source, tmp_path)

    assert _results(summary) == {
        "OHLCV": "FETCHED",
        "INVESTOR_VALUE": "SKIPPED_NO_AUTH",
        "INDEX": "SKIPPED_NO_AUTH",
        "CALENDAR": "SKIPPED_NO_AUTH",
        "DAILY_MERGED": "BUILT",
    }
    assert summary.has_skipped_no_auth()
    stock_dir = market_raw_stock_dir(tmp_path, STOCK)
    assert not (stock_dir / "investor_value.parquet").exists()
    assert not market_calendar_path(tmp_path).exists()  # 캘린더 미생성
    assert not market_raw_index_dir(tmp_path, INDEX).exists()

    # daily는 가격 컬럼만 + meta에 has_investor_flows=false (명세 §3.3)
    normalized_dir = market_normalized_stock_dir(tmp_path, STOCK)
    daily = pd.read_parquet(normalized_dir / "daily.parquet")
    assert list(daily.columns) == ["date", *OHLCV_COLUMNS]
    meta = (normalized_dir / "daily.meta.json").read_text(encoding="utf-8")
    assert '"has_investor_flows": false' in meta


def test_cached_datasets_survive_credential_loss(tmp_path: Path) -> None:
    # 캐시 히트는 자격증명 검사보다 먼저다 — 과거 수집분은 계속 쓴다 (명세 §3.2)
    _collect(FakeSource(authorized=True), tmp_path)
    summary = _collect(FakeSource(authorized=False), tmp_path)
    assert _results(summary) == {
        "OHLCV": "CACHED",
        "INVESTOR_VALUE": "CACHED",
        "INDEX": "CACHED",
        "CALENDAR": "BUILT",
        "DAILY_MERGED": "BUILT",
    }


# --- normalized daily 병합 (명세 §3.3) ------------------------------------------


def test_daily_merged_schema_and_left_join(tmp_path: Path) -> None:
    source = FakeSource()
    _collect(source, tmp_path)
    daily = pd.read_parquet(market_normalized_stock_dir(tmp_path, STOCK) / "daily.parquet")

    assert list(daily.columns) == ["date", *DAILY_MERGED_COLUMNS]  # 고정 스키마
    assert len(daily) == 8  # ohlcv 기준 left-join
    by_date = daily.set_index("date")
    # 수급이 있는 날은 값, 없는 날(뒤 2거래일)은 NaN
    foreign = by_date["foreign_net_buy_value"].to_dict()
    institution = by_date["institution_net_buy_value"].to_dict()
    assert foreign[date(2024, 1, 2)] == 10_000
    assert pd.isna(foreign[date(2024, 1, 10)])
    assert pd.isna(institution[date(2024, 1, 11)])

    meta = (market_normalized_stock_dir(tmp_path, STOCK) / "daily.meta.json").read_text(
        encoding="utf-8"
    )
    assert '"has_investor_flows": true' in meta


def test_daily_merge_drops_investor_only_dates(tmp_path: Path) -> None:
    # ohlcv에 없는 날의 수급은 병합본에 나타나지 않는다 (ohlcv 기준 left-join)
    ohlcv_days = [d for d in TRADING_DAYS if d != date(2024, 1, 9)]
    source = FakeSource(ohlcv=_ohlcv_frame(ohlcv_days), investor=_investor_frame(TRADING_DAYS))
    _collect(source, tmp_path)
    daily = pd.read_parquet(market_normalized_stock_dir(tmp_path, STOCK) / "daily.parquet")
    assert len(daily) == 7
    assert date(2024, 1, 9) not in set(daily["date"])


def test_duplicate_dates_raise_validation_error(tmp_path: Path) -> None:
    duplicated = _ohlcv_frame([date(2024, 1, 2), date(2024, 1, 3), date(2024, 1, 3)])
    with pytest.raises(DataValidationError, match="중복"):
        _collect(FakeSource(ohlcv=duplicated), tmp_path)


def test_high_below_low_raises_validation_error(tmp_path: Path) -> None:
    broken = _ohlcv_frame(TRADING_DAYS)
    broken.loc[date(2024, 1, 4), "high"] = 0.0  # low(92.0)보다 작은 값
    with pytest.raises(DataValidationError, match="high < low"):
        _collect(FakeSource(ohlcv=broken), tmp_path)


def test_empty_ohlcv_raises_validation_error(tmp_path: Path) -> None:
    # 요청 범위에 데이터가 전혀 없으면 실패 — 빈 결과를 정상으로 취급하지 않는다 (명세 §0)
    with pytest.raises(DataValidationError, match="비어"):
        _collect(FakeSource(), tmp_path, from_date=date(2024, 2, 1), to_date=date(2024, 2, 2))


def test_from_date_after_to_date_raises_value_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        _collect(FakeSource(), tmp_path, from_date=TO, to_date=FROM)
