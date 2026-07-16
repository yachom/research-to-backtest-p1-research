"""시장 데이터 소스 어댑터 — pykrx 경계 (명세 A3 §0·§2, MILESTONES D1 개정).

KRX가 2025년부터 데이터 조회에 로그인을 의무화했다(2026-07-14 실측, 명세 §0):

- ``get_market_ohlcv(adjusted=True)``: 로그인 **불필요** (Naver 경유).
  실측 컬럼: 시가/고가/저가/종가/거래량/등락률 (int64 x5 + float64), index=날짜(datetime64).
- ``get_market_trading_value_by_date``: 로그인 필요 — 미로그인 시 예외가 아니라
  **빈 DataFrame** + stdout 오류 출력.
- ``get_index_ohlcv``: 로그인 필요 — 미로그인 시 KeyError 등 예외 발생 가능.

따라서 빈 결과를 절대 정상으로 취급하지 않는다 — 자격증명 부재면 호출 전에
:class:`MarketAuthError`로 차단하고, 자격증명이 있는데 빈 결과면
:class:`DataValidationError`다.

pykrx 1.2.8은 ``os.getenv("KRX_ID")/os.getenv("KRX_PW")``로 환경변수에서 직접
자격증명을 읽어 자동 로그인하며, **모듈 import 시점**에도 로그인을 시도한다
(pykrx.website.comm.webio가 import 시 ``build_krx_session()`` 호출). 우리
Settings(.env)는 os.environ에 자동 반영되지 않으므로 :class:`PykrxSource`
생성 시 주입하고, pykrx import는 주입 이후로 지연한다(:func:`_pykrx_stock`).
pykrx가 stdout에 찍는 "KRX 로그인 실패…" 경고는 허용된 소음이다 — 우리
로그에는 자격증명 값을 절대 출력하지 않는다(명세 §2).
"""

import os
from datetime import date
from typing import Any, Protocol

import pandas as pd

from research_backtest.core.exceptions import DataValidationError, MarketAuthError

# 어댑터 반환 계약 컬럼 (명세 A3 §2) — 드리프트는 즉시 실패
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
INVESTOR_REQUIRED_COLUMNS = ["foreign_net_buy_value", "institution_net_buy_value"]
INVESTOR_OPTIONAL_COLUMNS = ["individual_net_buy_value"]

# pykrx 원본 컬럼 → 계약 컬럼 (2026-07-14 실측: pykrx 1.2.8)
_OHLCV_RENAME = {"시가": "open", "고가": "high", "저가": "low", "종가": "close", "거래량": "volume"}
_INDEX_TRADING_VALUE_COLUMN = "거래대금"  # 지수 응답에만 존재 — trading_value로 유지 (명세 §2)
_INVESTOR_RENAME = {
    "외국인합계": "foreign_net_buy_value",
    "기관합계": "institution_net_buy_value",
    "개인": "individual_net_buy_value",
}


class MarketDataSource(Protocol):
    """시장 데이터 소스 인터페이스 (명세 A3 §2, MILESTONES D1 "어댑터만 교체").

    세 메서드 모두 index=date(datetime.date)·계약 컬럼으로 정규화된
    DataFrame을 반환한다. 자격증명이 필요한 데이터셋은 자격증명 부재 시
    :class:`MarketAuthError`를 던진다 — collector가 이를 받아 부분 수집
    모드(SKIPPED_NO_AUTH)로 계속한다(명세 §3.3).
    """

    def fetch_ohlcv(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame: ...

    def fetch_investor_value(
        self, stock_code: str, from_date: date, to_date: date
    ) -> pd.DataFrame: ...

    def fetch_index_ohlcv(
        self, index_code: str, from_date: date, to_date: date
    ) -> pd.DataFrame: ...


def _pykrx_stock() -> Any:
    """pykrx.stock 모듈 lazy import — 자격증명 주입(os.environ) 이후로 지연한다.

    pykrx는 import 시점에 KRX 로그인을 시도하므로(모듈 docstring), 반드시
    :class:`PykrxSource` 생성(환경변수 주입) 뒤에 import되어야 한다.
    unit 테스트는 이 함수를 monkeypatch해 pykrx 없이 어댑터를 검증한다.
    """
    from pykrx import stock

    return stock


class PykrxSource:
    """pykrx 1.2.8 기반 :class:`MarketDataSource` 구현 (명세 A3 §2)."""

    def __init__(self, *, krx_id: str = "", krx_pw: str = "") -> None:
        """KRX 자격증명을 os.environ에 주입한다 — 이미 있으면 존중(덮어쓰지 않음).

        pykrx가 환경변수로 자동 로그인하기 때문이다(명세 §0). 값은 로깅하지
        않는다.
        """
        if krx_id and not os.environ.get("KRX_ID"):
            os.environ["KRX_ID"] = krx_id
        if krx_pw and not os.environ.get("KRX_PW"):
            os.environ["KRX_PW"] = krx_pw

    @property
    def has_krx_credentials(self) -> bool:
        """KRX 로그인 자격증명이 (주입 결과 포함) 환경변수에 있는지."""
        return bool(os.environ.get("KRX_ID") and os.environ.get("KRX_PW"))

    def fetch_ohlcv(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        """수정주가 OHLCV — 로그인 불필요 경로 (명세 §0·§2).

        **설계 결정 — 수정주가(adjusted=True) 사용**: 액면분할·배당 등을 소급
        수정한 가격 계열로, 신호 계산과 체결 시뮬레이션 모두 수정주가 기준으로
        통일한다(MVP 단순화, README §21.2·§22 취지). 소급 수정 방식이므로
        과거 시점의 표시 가격과는 다르며, **거래량은 미수정일 수 있다**.
        원주가 계열은 KRX 로그인 필요(adjusted=False) — Phase B 이후 후보.

        반환: index=date(datetime.date), columns=open,high,low,close,volume.
        원본 등락률 컬럼은 버린다. 빈 결과는 DataValidationError — 종목코드·
        기간을 확인하라(빈 결과를 정상으로 취급하지 않는다, 명세 §0).
        """
        raw = _pykrx_stock().get_market_ohlcv(
            _yyyymmdd(from_date), _yyyymmdd(to_date), stock_code, adjusted=True
        )
        return _normalize_price_frame(
            raw,
            what=f"수정주가 OHLCV({stock_code})",
            empty_hint="종목코드·기간 확인",
            keep_trading_value=False,
        )

    def fetch_investor_value(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        """투자자별 순매수 거래대금 — KRX 로그인 필요 (명세 §0·§2).

        2026-07-14 실측(pykrx 1.2.8) 원본 컬럼: 기관합계/기타법인/개인/
        외국인합계/전체 (on="순매수" 기본, index=날짜). 외국인합계·기관합계
        부재 시 DataValidationError — 컬럼 드리프트를 즉시 잡는다.

        반환: index=date, columns=foreign_net_buy_value,
        institution_net_buy_value,individual_net_buy_value(개인 존재 시).
        기타법인·전체는 버린다.
        """
        self._require_credentials("투자자별 순매수")
        raw = _pykrx_stock().get_market_trading_value_by_date(
            _yyyymmdd(from_date), _yyyymmdd(to_date), stock_code
        )
        return _normalize_investor_frame(raw, what=f"투자자별 순매수({stock_code})")

    def fetch_index_ohlcv(self, index_code: str, from_date: date, to_date: date) -> pd.DataFrame:
        """지수 OHLCV(벤치마크 KOSPI=1001) — KRX 로그인 필요 (명세 §0·§2).

        원본 컬럼(2026-07-14 실측): 시가/고가/저가/종가/거래량/거래대금.
        반환: index=date, columns=open,high,low,close,volume,trading_value
        (거래대금이 있으면 유지 — 명세 §2 선택 사항).
        """
        self._require_credentials("지수 OHLCV")
        raw = _pykrx_stock().get_index_ohlcv(_yyyymmdd(from_date), _yyyymmdd(to_date), index_code)
        return _normalize_price_frame(
            raw,
            what=f"지수 OHLCV({index_code})",
            empty_hint="자격증명·기간 확인",
            keep_trading_value=True,
        )

    def _require_credentials(self, what: str) -> None:
        if not self.has_krx_credentials:
            raise MarketAuthError(
                f"{what}은(는) KRX 로그인이 필요합니다 — .env에 KRX_ID/KRX_PW를 "
                "설정하세요 (data.krx.co.kr 무료 계정, MILESTONES D1 개정)."
            )


# --- pykrx 반환 정규화 (타입 경계 — 명세 §2, pyproject mypy override 주석) ----


def _yyyymmdd(d: date) -> str:
    """pykrx 호출은 날짜를 YYYYMMDD 문자열로 받는다 — 어댑터 경계에서만 변환 (명세 §2)."""
    return d.strftime("%Y%m%d")


def _ensure_dataframe(raw: Any, *, what: str) -> pd.DataFrame:
    """pykrx 반환값(무타입)을 pd.DataFrame으로 고정한다 (명세 §9)."""
    if not isinstance(raw, pd.DataFrame):
        raise DataValidationError(f"{what} 응답이 DataFrame이 아닙니다: {type(raw).__name__}")
    return raw


def _require_nonempty(frame: pd.DataFrame, *, what: str, hint: str) -> None:
    """빈 DataFrame을 정상으로 취급하지 않는다 (명세 §0) — 미로그인 실패의 위장 형태."""
    if frame.empty:
        raise DataValidationError(f"KRX 응답이 비어 있음 — {hint} ({what})")


def _with_date_index(frame: pd.DataFrame, *, what: str) -> pd.DataFrame:
    """index를 datetime.date 값의 'date' index로 정규화한다 (명세 §2)."""
    try:
        dates = pd.DatetimeIndex(frame.index).date
    except (TypeError, ValueError) as err:
        raise DataValidationError(f"{what} 응답 index를 날짜로 해석할 수 없습니다: {err}") from err
    normalized = frame.copy()
    normalized.index = pd.Index(dates, name="date")
    return normalized


def _normalize_price_frame(
    raw: Any, *, what: str, empty_hint: str, keep_trading_value: bool
) -> pd.DataFrame:
    """가격 계열(종목·지수 공용)을 계약 스키마로 정규화한다 — 드리프트 즉시 실패."""
    frame = _ensure_dataframe(raw, what=what)
    _require_nonempty(frame, what=what, hint=empty_hint)
    missing = [column for column in _OHLCV_RENAME if column not in frame.columns]
    if missing:
        raise DataValidationError(
            f"{what} 응답 컬럼 드리프트 — 기대 컬럼 {missing} 부재, "
            f"실제: {list(frame.columns)} (명세 A3 §2)"
        )
    rename = dict(_OHLCV_RENAME)
    keep = list(OHLCV_COLUMNS)
    if keep_trading_value and _INDEX_TRADING_VALUE_COLUMN in frame.columns:
        rename[_INDEX_TRADING_VALUE_COLUMN] = "trading_value"
        keep.append("trading_value")
    return _with_date_index(frame.rename(columns=rename)[keep], what=what)


def _normalize_investor_frame(raw: Any, *, what: str) -> pd.DataFrame:
    """투자자 순매수를 계약 스키마로 정규화한다 — 외국인·기관 컬럼 부재는 즉시 실패."""
    frame = _ensure_dataframe(raw, what=what)
    _require_nonempty(frame, what=what, hint="자격증명·기간 확인")
    missing = [
        column
        for column, renamed in _INVESTOR_RENAME.items()
        if renamed in INVESTOR_REQUIRED_COLUMNS and column not in frame.columns
    ]
    if missing:
        raise DataValidationError(
            f"{what} 응답 컬럼 드리프트 — 기대 컬럼 {missing} 부재, "
            f"실제: {list(frame.columns)} (명세 A3 §2)"
        )
    keep = [
        renamed
        for column, renamed in _INVESTOR_RENAME.items()
        if renamed in INVESTOR_REQUIRED_COLUMNS or column in frame.columns
    ]
    return _with_date_index(frame.rename(columns=_INVESTOR_RENAME)[keep], what=what)
