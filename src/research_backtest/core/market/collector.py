"""시장 데이터 수집·캐시·저장 오케스트레이션 (명세 A3 §3, MILESTONES D1).

저장 구조 (명세 §3.1):

- ``{data_dir}/raw/market/pykrx/{stock_code}/ohlcv.parquet`` + ``ohlcv.meta.json``
- ``{data_dir}/raw/market/pykrx/{stock_code}/investor_value.parquet`` + ``investor_value.meta.json``
- ``{data_dir}/raw/market/pykrx/index_{index_code}/ohlcv.parquet`` + ``ohlcv.meta.json``
- ``{data_dir}/normalized/market/{stock_code}/daily.parquet``          # 병합본 (A6 입력)
- ``{data_dir}/normalized/market/index_{index_code}/daily.parquet``
- ``{data_dir}/normalized/market/calendar/krx_trading_days.parquet``   # date 단일 컬럼

캐시 규칙 (명세 §3.2): 히트 = meta 파싱 가능 ∧ 요청 범위 ⊆ [meta.from_date,
meta.to_date] ∧ parquet 존재. 쓰기 순서는 parquet → meta.json이며 **meta가
커밋 마커**다(A2와 동일 규칙) — meta 없이 parquet만 있으면 캐시 미스로
재수집한다. 미스(범위 확장 포함)면 요청·저장 범위의 **합집합** 전체를
재수집해 통째로 덮어쓴다 — 단순·정확 우선, 일 단위 append는 후순위.

부분 수집 모드 (명세 §0·§3.3): KRX 자격증명이 없으면 투자자 수급·지수는
``SKIPPED_NO_AUTH``로 기록하고 계속한다 — 가격(OHLCV)과 일별 병합본은
항상 생산되므로, 자격증명 확보 후 재실행하면 가격 캐시는 유지된 채
나머지만 수집된다.
"""

import importlib.metadata
import itertools
import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel

from research_backtest.core.exceptions import DataValidationError, MarketAuthError
from research_backtest.core.market.calendar import (
    CALENDAR_FILENAME,
    as_date,
    build_calendar_from_index,
)
from research_backtest.core.market.source import (
    INVESTOR_REQUIRED_COLUMNS,
    OHLCV_COLUMNS,
    MarketDataSource,
)

logger = logging.getLogger("r2b.market.collector")

KST = ZoneInfo("Asia/Seoul")
COLLECTION_SOURCE = "PYKRX"  # meta.json의 source 값 (명세 §3.1)

OHLCV_STEM = "ohlcv"
INVESTOR_STEM = "investor_value"
DAILY_FILENAME = "daily.parquet"
DAILY_META_FILENAME = "daily.meta.json"

# 일별 병합본(A6 입력)의 고정 스키마 (명세 §3.3) — investor 없으면 가격 컬럼만
DAILY_MERGED_COLUMNS = [*OHLCV_COLUMNS, *INVESTOR_REQUIRED_COLUMNS]

DatasetName = Literal["OHLCV", "INVESTOR_VALUE", "INDEX", "CALENDAR", "DAILY_MERGED"]
DatasetResult = Literal["FETCHED", "CACHED", "SKIPPED_NO_AUTH", "BUILT"]


def market_raw_stock_dir(data_dir: Path, stock_code: str) -> Path:
    """종목 raw 디렉토리 — ``{data_dir}/raw/market/pykrx/{stock_code}`` (명세 §3.1)."""
    return data_dir / "raw" / "market" / "pykrx" / stock_code


def market_raw_index_dir(data_dir: Path, index_code: str) -> Path:
    """지수 raw 디렉토리 — ``{data_dir}/raw/market/pykrx/index_{index_code}`` (명세 §3.1)."""
    return data_dir / "raw" / "market" / "pykrx" / f"index_{index_code}"


def market_normalized_stock_dir(data_dir: Path, stock_code: str) -> Path:
    """종목 normalized 디렉토리 — ``{data_dir}/normalized/market/{stock_code}``."""
    return data_dir / "normalized" / "market" / stock_code


def market_normalized_index_dir(data_dir: Path, index_code: str) -> Path:
    """지수 normalized 디렉토리 — ``{data_dir}/normalized/market/index_{index_code}``."""
    return data_dir / "normalized" / "market" / f"index_{index_code}"


def market_calendar_path(data_dir: Path) -> Path:
    """거래일 캘린더 parquet 경로 (명세 §3.1, KrxTradingCalendar.from_parquet 입력)."""
    return data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME


class DatasetOutcome(BaseModel):
    """데이터셋 1종의 수집 결과 (명세 §3.3)."""

    dataset: DatasetName
    result: DatasetResult
    row_count: int = 0
    date_min: date | None = None
    date_max: date | None = None


class MarketCollectionSummary(BaseModel):
    """수집 실행 1회의 요약 (명세 §3.3)."""

    stock_code: str
    index_code: str
    outcomes: list[DatasetOutcome]

    def has_skipped_no_auth(self) -> bool:
        """부분 수집 모드였는지 — CLI의 노란 안내 경고 트리거 (명세 §6)."""
        return any(outcome.result == "SKIPPED_NO_AUTH" for outcome in self.outcomes)


def collect_market_data(
    source: MarketDataSource,
    *,
    stock_code: str,
    index_code: str,
    from_date: date,
    to_date: date,
    data_dir: Path,
    force: bool = False,
    min_interval_seconds: float = 0.3,
    sleep: Callable[[float], None] = time.sleep,
) -> MarketCollectionSummary:
    """종목 1개의 시장 데이터 전체를 수집한다 (명세 §3.3).

    순서: ① 수정주가 OHLCV(무로그인 — 실패 시 전체 중단) → ② 투자자
    순매수 · ③ 지수(자격증명 없으면 :class:`MarketAuthError`를 받아
    ``SKIPPED_NO_AUTH``로 기록하고 계속 — 부분 수집 모드) → ④ 거래일
    캘린더(**지수 데이터가 있을 때만** — 지수 거래일이 시장 캘린더다,
    종목 날짜로 대체하지 않는다) → ⑤ normalized daily 병합.

    - 캐시 히트면 소스를 호출하지 않고, ``force=True``면 무시하고 재수집한다.
      재수집 범위는 항상 요청·저장 범위의 합집합(meta 파싱 가능 시)이다 —
      좁은 요청이 기존 커버리지를 축소하지 않는다.
    - **실제 소스 호출 사이에만** ``min_interval_seconds``를 대기한다
      (configs/market.yaml, 기본 0.3초). ``sleep``은 테스트 주입용.
    - 검증 위반(빈 OHLCV, date 중복·비단조, high<low)은
      :class:`DataValidationError`로 전파해 실행을 중단한다.

    ``from_date > to_date``면 :class:`ValueError`.
    """
    if from_date > to_date:
        raise ValueError(f"from_date({from_date})가 to_date({to_date})보다 큽니다.")

    stock_dir = market_raw_stock_dir(data_dir, stock_code)
    index_dir = market_raw_index_dir(data_dir, index_code)
    pacer = _SourcePacer(min_interval_seconds, sleep)
    outcomes: list[DatasetOutcome] = []

    # ① 수정주가 OHLCV — 무로그인 경로, 실패는 전파(전체 중단)
    outcomes.append(
        _collect_dataset(
            "OHLCV",
            stock_dir,
            OHLCV_STEM,
            fetch=lambda f, t: source.fetch_ohlcv(stock_code, f, t),
            params={"stock_code": stock_code},
            from_date=from_date,
            to_date=to_date,
            force=force,
            pacer=pacer,
            check_high_low=True,
        )
    )

    # ②·③ 투자자 순매수·지수 — 자격증명 없으면 SKIPPED_NO_AUTH (부분 수집 모드)
    outcomes.append(
        _collect_optional_dataset(
            "INVESTOR_VALUE",
            stock_dir,
            INVESTOR_STEM,
            fetch=lambda f, t: source.fetch_investor_value(stock_code, f, t),
            params={"stock_code": stock_code},
            from_date=from_date,
            to_date=to_date,
            force=force,
            pacer=pacer,
            check_high_low=False,
        )
    )
    index_outcome = _collect_optional_dataset(
        "INDEX",
        index_dir,
        OHLCV_STEM,
        fetch=lambda f, t: source.fetch_index_ohlcv(index_code, f, t),
        params={"index_code": index_code},
        from_date=from_date,
        to_date=to_date,
        force=force,
        pacer=pacer,
        check_high_low=True,
    )
    outcomes.append(index_outcome)

    # ④ 거래일 캘린더 — 지수 데이터가 있을 때만 (지수 거래일 = 시장 캘린더, 명세 §3.3)
    if index_outcome.result in ("FETCHED", "CACHED"):
        outcomes.append(_build_calendar_and_index_daily(data_dir, index_dir, index_code))
    else:
        logger.info("거래일 캘린더 미생성 — 지수 데이터 없음(자격증명 필요, 명세 §3.3)")
        outcomes.append(DatasetOutcome(dataset="CALENDAR", result="SKIPPED_NO_AUTH"))

    # ⑤ normalized daily 병합 (A6 입력)
    outcomes.append(_build_daily_merged(data_dir, stock_dir, stock_code))

    summary = MarketCollectionSummary(
        stock_code=stock_code, index_code=index_code, outcomes=outcomes
    )
    logger.info(
        "시장 데이터 수집 완료 stock_code=%s index_code=%s 결과=%s",
        stock_code,
        index_code,
        {outcome.dataset: outcome.result for outcome in outcomes},
    )
    return summary


def read_raw_frame(path: Path) -> pd.DataFrame:
    """raw parquet을 date index DataFrame으로 로드한다 — 저장의 역변환 (명세 §9).

    저장 시 date index를 컬럼으로 reset해 명시 스키마(date 컬럼)로 쓰므로,
    로드 시 date 컬럼을 datetime.date index로 되돌려 왕복 일관성을 보장한다.
    """
    # engine 기본값 auto — pyarrow가 설치된 환경에서는 pyarrow로 읽는다 (명세 §9)
    frame = pd.read_parquet(path)
    if "date" not in frame.columns:
        raise DataValidationError(f"raw parquet에 date 컬럼이 없습니다: {path}")
    dates = [as_date(value) for value in frame["date"]]
    loaded = frame.drop(columns=["date"])
    loaded.index = pd.Index(dates, name="date")
    return loaded


# --- 내부 구현 ---------------------------------------------------------------


class _SourcePacer:
    """실제 소스 호출 사이에만 min_interval_seconds 대기한다 (명세 §3.2).

    캐시 히트는 호출이 없으므로 대기하지 않는다.
    """

    def __init__(self, interval_seconds: float, sleep: Callable[[float], None]) -> None:
        self._interval_seconds = interval_seconds
        self._sleep = sleep
        self._called = False

    def run(self, fetch: Callable[[], pd.DataFrame]) -> pd.DataFrame:
        if self._called:
            self._sleep(self._interval_seconds)
        frame = fetch()
        self._called = True
        return frame


def _collect_dataset(
    dataset: DatasetName,
    out_dir: Path,
    stem: str,
    *,
    fetch: Callable[[date, date], pd.DataFrame],
    params: dict[str, str],
    from_date: date,
    to_date: date,
    force: bool,
    pacer: _SourcePacer,
    check_high_low: bool,
) -> DatasetOutcome:
    """raw 데이터셋 1종을 캐시 규칙(명세 §3.2)에 따라 수집·저장한다."""
    meta = _load_meta(out_dir / f"{stem}.meta.json")
    stored = _stored_range(meta)
    parquet_path = out_dir / f"{stem}.parquet"
    if (
        not force
        and meta is not None
        and stored is not None
        and parquet_path.exists()
        and stored[0] <= from_date
        and to_date <= stored[1]
    ):
        return DatasetOutcome(
            dataset=dataset,
            result="CACHED",
            row_count=int(meta.get("row_count") or 0),
            date_min=_optional_date(meta.get("date_min")),
            date_max=_optional_date(meta.get("date_max")),
        )

    fetch_from = min(from_date, stored[0]) if stored else from_date
    fetch_to = max(to_date, stored[1]) if stored else to_date
    frame = pacer.run(lambda: fetch(fetch_from, fetch_to))
    _validate_frame(frame, what=dataset, check_high_low=check_high_low)
    written = _write_raw(
        out_dir, stem, frame, params=params, from_date=fetch_from, to_date=fetch_to
    )
    return DatasetOutcome(
        dataset=dataset,
        result="FETCHED",
        row_count=int(written["row_count"]),
        date_min=_optional_date(written["date_min"]),
        date_max=_optional_date(written["date_max"]),
    )


def _collect_optional_dataset(
    dataset: DatasetName,
    out_dir: Path,
    stem: str,
    *,
    fetch: Callable[[date, date], pd.DataFrame],
    params: dict[str, str],
    from_date: date,
    to_date: date,
    force: bool,
    pacer: _SourcePacer,
    check_high_low: bool,
) -> DatasetOutcome:
    """자격증명 필요 데이터셋 — MarketAuthError면 SKIPPED_NO_AUTH로 계속 (명세 §3.3).

    캐시 히트는 자격증명 검사보다 먼저이므로, 과거에 수집된 데이터는
    자격증명이 사라져도 CACHED로 계속 쓸 수 있다.
    """
    try:
        return _collect_dataset(
            dataset,
            out_dir,
            stem,
            fetch=fetch,
            params=params,
            from_date=from_date,
            to_date=to_date,
            force=force,
            pacer=pacer,
            check_high_low=check_high_low,
        )
    except MarketAuthError as err:
        logger.info("%s 수집 생략(부분 수집 모드): %s", dataset, err)
        return DatasetOutcome(dataset=dataset, result="SKIPPED_NO_AUTH")


def _build_calendar_and_index_daily(
    data_dir: Path, index_dir: Path, index_code: str
) -> DatasetOutcome:
    """지수 raw에서 거래일 캘린더와 normalized 지수 daily를 발행한다 (명세 §3.1·§4)."""
    index_frame = read_raw_frame(index_dir / f"{OHLCV_STEM}.parquet")
    _validate_frame(index_frame, what="INDEX(raw)", check_high_low=True)
    days = build_calendar_from_index(index_frame)

    calendar_path = market_calendar_path(data_dir)
    calendar_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"date": days}).to_parquet(calendar_path, engine="pyarrow", index=False)

    index_daily_dir = market_normalized_index_dir(data_dir, index_code)
    index_daily_dir.mkdir(parents=True, exist_ok=True)
    index_frame.reset_index().to_parquet(
        index_daily_dir / DAILY_FILENAME, engine="pyarrow", index=False
    )
    return DatasetOutcome(
        dataset="CALENDAR",
        result="BUILT",
        row_count=len(days),
        date_min=days[0],
        date_max=days[-1],
    )


def _build_daily_merged(data_dir: Path, stock_dir: Path, stock_code: str) -> DatasetOutcome:
    """ohlcv 기준 left-join으로 normalized daily를 만든다 — A6 입력 (명세 §3.3).

    디스크의 raw parquet 전체 범위로 매 실행 결정적으로 재생성한다(멱등,
    A2의 jsonl 재생성과 동일 취지). investor raw가 없으면(부분 수집 모드)
    가격 컬럼만으로 생성하고 meta에 ``has_investor_flows: false``를 기록한다.
    """
    ohlcv = read_raw_frame(stock_dir / f"{OHLCV_STEM}.parquet")
    _require_columns(ohlcv, OHLCV_COLUMNS, what="OHLCV(raw)")
    _validate_frame(ohlcv, what="OHLCV(raw)", check_high_low=True)

    investor = _load_committed_investor(stock_dir)
    if investor is not None:
        _require_columns(investor, INVESTOR_REQUIRED_COLUMNS, what="INVESTOR_VALUE(raw)")
        _validate_frame(investor, what="INVESTOR_VALUE(raw)", check_high_low=False)
        _log_date_coverage(ohlcv, investor, stock_code)
        merged = ohlcv[OHLCV_COLUMNS].join(investor[INVESTOR_REQUIRED_COLUMNS], how="left")
        merged = merged[DAILY_MERGED_COLUMNS]
    else:
        merged = ohlcv[OHLCV_COLUMNS].copy()

    zero_volume = int((merged["volume"] == 0).sum())
    if zero_volume:
        logger.info(
            "volume=0 행 %d개 (%s) — 거래정지일 가능성, 실패 아님 (명세 §3.3)",
            zero_volume,
            stock_code,
        )

    out_dir = market_normalized_stock_dir(data_dir, stock_code)
    out_dir.mkdir(parents=True, exist_ok=True)
    merged.reset_index().to_parquet(out_dir / DAILY_FILENAME, engine="pyarrow", index=False)
    days = [as_date(value) for value in merged.index]
    meta = {
        "params": {"stock_code": stock_code},
        "row_count": len(merged),
        "date_min": days[0].isoformat(),
        "date_max": days[-1].isoformat(),
        "has_investor_flows": investor is not None,
        "built_at": datetime.now(KST).isoformat(),
        "source": COLLECTION_SOURCE,
    }
    (out_dir / DAILY_META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return DatasetOutcome(
        dataset="DAILY_MERGED",
        result="BUILT",
        row_count=len(merged),
        date_min=days[0],
        date_max=days[-1],
    )


def _load_committed_investor(stock_dir: Path) -> pd.DataFrame | None:
    """investor raw가 커밋(meta 존재)되어 있으면 로드한다 — 없으면 None (부분 수집 모드)."""
    meta = _load_meta(stock_dir / f"{INVESTOR_STEM}.meta.json")
    parquet_path = stock_dir / f"{INVESTOR_STEM}.parquet"
    if meta is None or not parquet_path.exists():
        return None
    return read_raw_frame(parquet_path)


def _log_date_coverage(ohlcv: pd.DataFrame, investor: pd.DataFrame, stock_code: str) -> None:
    """investor와 ohlcv의 날짜 차집합 개수를 로그로 남긴다 — 실패 아님 (명세 §3.3)."""
    ohlcv_days = set(ohlcv.index)
    investor_days = set(investor.index)
    only_ohlcv = len(ohlcv_days - investor_days)
    only_investor = len(investor_days - ohlcv_days)
    if only_ohlcv or only_investor:
        logger.info(
            "날짜 차집합 (%s): ohlcv에만 %d일, investor에만 %d일",
            stock_code,
            only_ohlcv,
            only_investor,
        )


def _validate_frame(frame: pd.DataFrame, *, what: str, check_high_low: bool) -> None:
    """수집 데이터 검증 — 위반 시 DataValidationError (명세 §3.3)."""
    if frame.empty:
        raise DataValidationError(f"{what} 결과가 비어 있습니다 (명세 A3 §3.3).")
    days = [as_date(value) for value in frame.index]
    if len(set(days)) != len(days):
        raise DataValidationError(f"{what}에 중복 date가 있습니다 (명세 A3 §3.3).")
    if any(prev >= nxt for prev, nxt in itertools.pairwise(days)):
        raise DataValidationError(f"{what}의 date가 오름차순이 아닙니다 (명세 A3 §3.3).")
    if check_high_low:
        bad_rows = int((frame["high"] < frame["low"]).sum())
        if bad_rows:
            raise DataValidationError(
                f"{what}에 high < low 행이 {bad_rows}개 있습니다 (명세 A3 §3.3)."
            )


def _require_columns(frame: pd.DataFrame, columns: list[str], *, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise DataValidationError(
            f"{what}에 기대 컬럼 {missing}이 없습니다 — --force-download로 재수집하세요."
        )


def _write_raw(
    out_dir: Path,
    stem: str,
    frame: pd.DataFrame,
    *,
    params: dict[str, str],
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    """raw parquet + meta 저장 — 쓰기 순서: parquet → meta (meta가 커밋 마커, 명세 §3.1)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    frame.reset_index().to_parquet(out_dir / f"{stem}.parquet", engine="pyarrow", index=False)
    days = [as_date(value) for value in frame.index]
    meta: dict[str, Any] = {
        "params": params,
        "from_date": from_date.isoformat(),
        "to_date": to_date.isoformat(),
        "row_count": len(frame),
        "date_min": min(days).isoformat(),
        "date_max": max(days).isoformat(),
        "fetched_at": datetime.now(KST).isoformat(),
        "source": COLLECTION_SOURCE,
        "pykrx_version": _pykrx_version(),
    }
    (out_dir / f"{stem}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    logger.debug("raw 저장 완료: %s/%s (%d행)", out_dir.name, stem, len(frame))
    return meta


def _pykrx_version() -> str:
    """meta.json 기록용 pykrx 버전 — 모듈 import 없이 조회한다.

    pykrx는 import 시점에 KRX 로그인을 시도하므로(source 모듈 docstring)
    버전 조회는 importlib.metadata로 우회한다.
    """
    try:
        return importlib.metadata.version("pykrx")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _load_meta(meta_path: Path) -> dict[str, Any] | None:
    """meta.json을 읽는다 — 없거나 파싱 불가면 None(캐시 미스, A2와 동일 규칙)."""
    if not meta_path.exists():
        return None
    try:
        loaded: Any = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _stored_range(meta: dict[str, Any] | None) -> tuple[date, date] | None:
    """meta의 [from_date, to_date] — 파싱 불가면 None (명세 §3.2)."""
    if meta is None:
        return None
    try:
        return (
            date.fromisoformat(str(meta.get("from_date"))),
            date.fromisoformat(str(meta.get("to_date"))),
        )
    except ValueError:
        return None


def _optional_date(value: Any) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None
