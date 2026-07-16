"""PykrxSource 어댑터 단위 테스트 — pykrx import·호출 금지 (명세 A3 §2, §7).

pykrx 경계는 :func:`source._pykrx_stock`을 monkeypatch해 검증한다 —
자격증명 부재 경로는 폭탄(호출 시 AssertionError)으로 "pykrx 호출 자체가
없음"을 보장하고, 정규화 로직은 한국어 컬럼 원본을 돌려주는 stub으로
검증한다.
"""

import os
from datetime import date
from typing import Any

import pandas as pd
import pytest

from research_backtest.core.exceptions import DataValidationError, MarketAuthError
from research_backtest.core.market import source as source_module
from research_backtest.core.market.source import OHLCV_COLUMNS, PykrxSource


@pytest.fixture(autouse=True)
def clean_krx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """개발 환경의 KRX 자격증명이 테스트에 스미지 않게 제거한다."""
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)


def _bomb_stock() -> Any:
    raise AssertionError("pykrx가 import/호출되면 안 됩니다 (명세 A3 §7)")


def _raw_ohlcv() -> pd.DataFrame:
    """pykrx 무로그인 OHLCV 원본 형태 (2026-07-14 실측 컬럼·dtype)."""
    return pd.DataFrame(
        {
            "시가": [100, 101],
            "고가": [110, 111],
            "저가": [90, 91],
            "종가": [105, 106],
            "거래량": [1000, 1100],
            "등락률": [0.5, 0.6],
        },
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="날짜"),
    )


def _raw_investor() -> pd.DataFrame:
    """pykrx 투자자별 순매수 원본 형태 (pykrx 1.2.8 docstring 실측 컬럼)."""
    return pd.DataFrame(
        {
            "기관합계": [-10, 20],
            "기타법인": [1, 2],
            "개인": [5, -3],
            "외국인합계": [4, -19],
            "전체": [0, 0],
        },
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="날짜"),
    )


def _raw_index() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가": [2500.0, 2510.0],
            "고가": [2550.0, 2560.0],
            "저가": [2450.0, 2460.0],
            "종가": [2520.0, 2530.0],
            "거래량": [100_000, 110_000],
            "거래대금": [5.0e12, 6.0e12],
        },
        index=pd.DatetimeIndex(["2024-01-02", "2024-01-03"], name="날짜"),
    )


class _StubStock:
    """pykrx.stock 대역 — 원본(한국어 컬럼) DataFrame을 그대로 돌려준다."""

    def __init__(
        self,
        *,
        ohlcv: pd.DataFrame | None = None,
        investor: pd.DataFrame | None = None,
        index: pd.DataFrame | None = None,
    ) -> None:
        self._ohlcv = ohlcv
        self._investor = investor
        self._index = index
        self.calls: list[tuple[str, ...]] = []

    def get_market_ohlcv(
        self, fromdate: str, todate: str, ticker: str, adjusted: bool = True
    ) -> pd.DataFrame:
        assert adjusted is True, "수정주가 사용은 설계 결정이다 (명세 A3 §2)"
        self.calls.append(("ohlcv", fromdate, todate, ticker))
        assert self._ohlcv is not None
        return self._ohlcv

    def get_market_trading_value_by_date(
        self, fromdate: str, todate: str, ticker: str
    ) -> pd.DataFrame:
        self.calls.append(("investor", fromdate, todate, ticker))
        assert self._investor is not None
        return self._investor

    def get_index_ohlcv(self, fromdate: str, todate: str, ticker: str) -> pd.DataFrame:
        self.calls.append(("index", fromdate, todate, ticker))
        assert self._index is not None
        return self._index


def _patch_stub(monkeypatch: pytest.MonkeyPatch, stub: _StubStock) -> None:
    monkeypatch.setattr(source_module, "_pykrx_stock", lambda: stub)


def _cell(frame: pd.DataFrame, day: date, column: str) -> Any:
    """pandas-stubs가 .loc[date, str]을 거부해 dict 경유로 셀을 읽는다."""
    return frame[column].to_dict()[day]


# --- 환경변수 주입 (명세 §2) --------------------------------------------------


def test_init_injects_credentials_into_environ() -> None:
    source = PykrxSource(krx_id="user", krx_pw="secret")
    assert os.environ["KRX_ID"] == "user"
    assert os.environ["KRX_PW"] == "secret"
    assert source.has_krx_credentials


def test_init_respects_existing_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KRX_ID", "existing-id")
    monkeypatch.setenv("KRX_PW", "existing-pw")
    PykrxSource(krx_id="other-id", krx_pw="other-pw")
    assert os.environ["KRX_ID"] == "existing-id"
    assert os.environ["KRX_PW"] == "existing-pw"


def test_init_without_credentials_sets_nothing() -> None:
    source = PykrxSource()
    assert "KRX_ID" not in os.environ
    assert "KRX_PW" not in os.environ
    assert not source.has_krx_credentials


def test_environ_only_credentials_count(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings가 비어도 이미 환경변수가 있으면 로그인 가능으로 판정한다
    monkeypatch.setenv("KRX_ID", "env-id")
    monkeypatch.setenv("KRX_PW", "env-pw")
    assert PykrxSource().has_krx_credentials


# --- 자격증명 부재 차단 (명세 §0·§2 — pykrx 호출 전에 실패) -------------------


def test_investor_without_credentials_raises_before_pykrx(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_module, "_pykrx_stock", _bomb_stock)
    with pytest.raises(MarketAuthError):
        PykrxSource().fetch_investor_value("000660", date(2024, 1, 2), date(2024, 1, 5))


def test_index_without_credentials_raises_before_pykrx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(source_module, "_pykrx_stock", _bomb_stock)
    with pytest.raises(MarketAuthError):
        PykrxSource().fetch_index_ohlcv("1001", date(2024, 1, 2), date(2024, 1, 5))


# --- 정규화 계약 (명세 §2) ----------------------------------------------------


def test_fetch_ohlcv_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubStock(ohlcv=_raw_ohlcv())
    _patch_stub(monkeypatch, stub)
    frame = PykrxSource().fetch_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))

    # 날짜는 어댑터 경계에서만 YYYYMMDD 문자열로 변환된다
    assert stub.calls == [("ohlcv", "20240102", "20240103", "000660")]
    assert list(frame.columns) == OHLCV_COLUMNS  # 등락률은 버린다
    assert frame.index.name == "date"
    assert list(frame.index) == [date(2024, 1, 2), date(2024, 1, 3)]
    assert _cell(frame, date(2024, 1, 2), "close") == 105


def test_fetch_ohlcv_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stub(monkeypatch, _StubStock(ohlcv=pd.DataFrame()))
    with pytest.raises(DataValidationError):
        PykrxSource().fetch_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))


def test_fetch_ohlcv_column_drift_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stub(monkeypatch, _StubStock(ohlcv=_raw_ohlcv().drop(columns=["종가"])))
    with pytest.raises(DataValidationError):
        PykrxSource().fetch_ohlcv("000660", date(2024, 1, 2), date(2024, 1, 3))


def test_fetch_investor_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubStock(investor=_raw_investor())
    _patch_stub(monkeypatch, stub)
    source = PykrxSource(krx_id="user", krx_pw="secret")
    frame = source.fetch_investor_value("000660", date(2024, 1, 2), date(2024, 1, 3))

    assert stub.calls == [("investor", "20240102", "20240103", "000660")]
    # 외국인·기관 필수 + 개인은 존재 시 유지, 기타법인·전체는 버린다
    assert list(frame.columns) == [
        "foreign_net_buy_value",
        "institution_net_buy_value",
        "individual_net_buy_value",
    ]
    assert frame.index.name == "date"
    assert _cell(frame, date(2024, 1, 2), "foreign_net_buy_value") == 4


def test_fetch_investor_missing_required_column_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_stub(monkeypatch, _StubStock(investor=_raw_investor().drop(columns=["외국인합계"])))
    source = PykrxSource(krx_id="user", krx_pw="secret")
    with pytest.raises(DataValidationError):
        source.fetch_investor_value("000660", date(2024, 1, 2), date(2024, 1, 3))


def test_fetch_investor_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # 미로그인 실패의 위장 형태(빈 DF)를 정상으로 취급하지 않는다 (명세 §0)
    _patch_stub(monkeypatch, _StubStock(investor=pd.DataFrame()))
    source = PykrxSource(krx_id="user", krx_pw="secret")
    with pytest.raises(DataValidationError, match="비어 있음"):
        source.fetch_investor_value("000660", date(2024, 1, 2), date(2024, 1, 3))


def test_fetch_index_contract_keeps_trading_value(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _StubStock(index=_raw_index())
    _patch_stub(monkeypatch, stub)
    source = PykrxSource(krx_id="user", krx_pw="secret")
    frame = source.fetch_index_ohlcv("1001", date(2024, 1, 2), date(2024, 1, 3))

    assert stub.calls == [("index", "20240102", "20240103", "1001")]
    assert list(frame.columns) == [*OHLCV_COLUMNS, "trading_value"]
    assert frame.index.name == "date"


def test_fetch_index_empty_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_stub(monkeypatch, _StubStock(index=pd.DataFrame()))
    source = PykrxSource(krx_id="user", krx_pw="secret")
    with pytest.raises(DataValidationError, match="비어 있음"):
        source.fetch_index_ohlcv("1001", date(2024, 1, 2), date(2024, 1, 3))
