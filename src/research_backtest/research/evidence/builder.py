"""Evidence 빌더 — A4 parquet에서 결정적 Python 계산으로 Evidence를 생성한다 (명세 W3a-E1 §3.3).

입력은 A4(core.financials.pipeline)가 만든 세 parquet이다:

- ``financial_metrics.parquet`` — YoY 3종·영업이익률(지표 evidence의 소스)
- ``quarterly_financials.parquet`` / ``annual_financials.parquet`` — 11계정 wide
  (계정 수준 evidence의 소스)

**Point-in-Time(절대 규칙 #1)**: 세 프레임 모두 ``available_from <= as_of``인 행만
사용한다. as_of 이후 접수된 공시의 수치는 어떤 evidence에도 포함되지 않는다 —
필터는 모든 생성 경로의 진입점(:func:`_pit_filter`)에 강제된다.

**경계(재구현 금지)**:

- 계정 *매칭*은 A4가 이미 끝냈다 — E1은 wide 컬럼(canonical_id)을 소비할 뿐
  :mod:`core.financials.registry`의 매칭 규칙을 다시 돌리지 않는다.
- 분기 단독 YoY(revenue·operating_income·net_income)는 A4 지표가 소유한다 —
  계정 evidence는 이를 **중복 생성하지 않고**(명세 §3.3-2②) 연간 증감률·부호
  전환·추세 등 A4에 없는 파생만 만든다.
- DSL 지표(:mod:`quant.strategy.registry`·indicators)는 백테스트 신호용이다 —
  Evidence 파생은 분석 서술용이며 그 지표를 재구현하지 않는다.

Evidence는 전부 결정적이다(LLM·난수 없음). ``statement``는 한국어 템플릿 문장이고
``significance_score``는 문서화된 결정적 수식(:func:`_significance`)이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import SupportsFloat, SupportsInt, cast
from zoneinfo import ZoneInfo

import pandas as pd

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.financials.pipeline import (
    ANNUAL_FILENAME,
    METRICS_FILENAME,
    QUARTERLY_FILENAME,
    financials_out_dir,
)
from research_backtest.research.evidence.models import (
    ACCOUNT_CATEGORY,
    ACCOUNT_CODE,
    ACCOUNT_DISPLAY,
    METRIC_CATEGORY,
    METRIC_CODE,
    METRIC_DISPLAY,
    SIGNABLE_ACCOUNTS,
    EvidencePackage,
    FinancialEvidence,
)

KST = ZoneInfo("Asia/Seoul")

# significance_score 가중치 (합=1 → 결과가 자연히 [0,1]). 명세 §3.3.
_W_MAGNITUDE = 0.5  # |change_rate| 크기
_W_SIGN = 0.2  # 부호 전환 가점
_W_RECENCY = 0.3  # as_of 근접도
# |change_rate| 반포화 상수 — magnitude = |r| / (|r| + c). c에서 magnitude=0.5.
# YoY(수백 %)와 이익률 변화(수 %p)의 스케일 차이를 흡수한다.
_MAGNITUDE_HALF = 0.5


def build_financial_evidence(
    corp_code: str,
    *,
    as_of: date,
    data_dir: Path,
    lookback_years: int = 5,
    fs_scope: str = "CFS",
    now: datetime | None = None,
) -> EvidencePackage:
    """A4 parquet에서 ``corp_code``의 Evidence 패키지를 생성한다 (명세 §3.3).

    - ``as_of``: Point-in-Time 기준일. ``available_from <= as_of``인 행만 쓴다.
    - ``lookback_years``: ``period_end``가 ``as_of - lookback_years``년 이후인 행만.
    - ``fs_scope``: MVP 기본 "CFS"(연결). 그 scope 행만 사용한다.
    - ``now``: ``generated_at`` 주입(테스트 결정성용). 미지정 시 KST 현재 시각.

    parquet 부재 시 :class:`FileNotFoundError`(build-financials 안내 포함),
    필터 후 세 프레임이 모두 0행이면 :class:`DataValidationError`.
    """
    out_dir = financials_out_dir(data_dir, corp_code)
    metrics = _pit_filter(
        _load_frame(out_dir, METRICS_FILENAME, corp_code),
        as_of=as_of,
        fs_scope=fs_scope,
        lookback_years=lookback_years,
    )
    quarterly = _pit_filter(
        _load_frame(out_dir, QUARTERLY_FILENAME, corp_code),
        as_of=as_of,
        fs_scope=fs_scope,
        lookback_years=lookback_years,
    )
    annual = _pit_filter(
        _load_frame(out_dir, ANNUAL_FILENAME, corp_code),
        as_of=as_of,
        fs_scope=fs_scope,
        lookback_years=lookback_years,
    )

    if len(metrics) == 0 and len(quarterly) == 0 and len(annual) == 0:
        raise DataValidationError(
            f"as_of={as_of.isoformat()} 시점(fs_scope={fs_scope}, lookback={lookback_years}년)에 "
            f"사용 가능한 재무 행이 없습니다 (corp_code={corp_code}). 기준일이 첫 공시 접수일보다 "
            "이르거나, 데이터 수집·빌드(build-financials)가 필요할 수 있습니다."
        )

    evidence: list[FinancialEvidence] = []
    evidence += _metric_evidence(
        metrics, fs_scope=fs_scope, as_of=as_of, lookback_years=lookback_years
    )
    evidence += _account_evidence(
        annual=annual,
        quarterly=quarterly,
        fs_scope=fs_scope,
        as_of=as_of,
        lookback_years=lookback_years,
    )

    _assert_unique_ids(evidence)
    # 결정적 정렬: 유의도 내림차순, 동률은 evidence_id 오름차순(유일 키).
    evidence.sort(key=lambda e: (-e.significance_score, e.evidence_id))

    generated_at = (now if now is not None else datetime.now(KST)).isoformat()
    return EvidencePackage(
        corp_code=corp_code,
        as_of_date=as_of.isoformat(),
        lookback_years=lookback_years,
        fs_scope=fs_scope,
        generated_at=generated_at,
        evidence=evidence,
    )


# --- 로드·필터 --------------------------------------------------------------


def _load_frame(out_dir: Path, filename: str, corp_code: str) -> pd.DataFrame:
    path = out_dir / filename
    if not path.exists():
        raise FileNotFoundError(
            f"재무 데이터셋이 없습니다: {path}. 먼저 `r2b build-financials {corp_code}`로 "
            f"{filename}을(를) 생성하세요."
        )
    return pd.read_parquet(path)


def _pit_filter(
    df: pd.DataFrame, *, as_of: date, fs_scope: str, lookback_years: int
) -> pd.DataFrame:
    """Point-in-Time 필터(명세 §3.3, 순서 고정) — ① fs_scope ② available_from<=as_of ③ lookback.

    세 조건의 논리곱이므로 적용 순서와 무관하게 동일한 결과를 낸다. PIT 위반
    (``available_from > as_of``)은 이 함수를 통과할 수 없다.
    """
    cutoff = _lookback_cutoff(as_of, lookback_years)
    mask = (
        (df["fs_scope"] == fs_scope)
        & (df["available_from"] <= as_of)
        & (df["period_end"] >= cutoff)
    )
    return df[mask].copy()


def _lookback_cutoff(as_of: date, lookback_years: int) -> date:
    """``as_of``에서 ``lookback_years``년을 뺀 하한 period_end (윤년 2/29는 2/28로)."""
    try:
        return date(as_of.year - lookback_years, as_of.month, as_of.day)
    except ValueError:
        return date(as_of.year - lookback_years, as_of.month, 28)


# --- 지표 evidence (financial_metrics.parquet) -------------------------------


def _metric_evidence(
    metrics: pd.DataFrame, *, fs_scope: str, as_of: date, lookback_years: int
) -> list[FinancialEvidence]:
    """PIT 필터된 지표 각 행 → evidence 1건 (명세 §3.3-1).

    YoY류는 change_rate=지표값, operating_margin은 current=지표값·비교=직전 동분기값.
    비교값은 같은 PIT 필터 집합에서만 찾는다(비교도 PIT 안전).
    """
    rows = _records(metrics)
    margin_by_period: dict[tuple[int, int], float] = {}
    for r in rows:
        if _as_str(r["metric_id"]) == "operating_margin":
            year = _req_int(r["fiscal_year"])
            quarter = _req_int(r["fiscal_quarter"])
            value = _as_float(r["value"])
            if value is not None:
                margin_by_period[(year, quarter)] = value

    out: list[FinancialEvidence] = []
    for r in rows:
        metric_id = _as_str(r["metric_id"])
        code = METRIC_CODE.get(metric_id)
        if code is None:
            continue  # 알 수 없는 지표는 조용히 건너뛰지 않고 스킵(스키마 드리프트 방어)
        year = _req_int(r["fiscal_year"])
        quarter = _req_int(r["fiscal_quarter"])
        value = _as_float(r["value"])
        if value is None:
            continue
        period = f"{year}Q{quarter}"
        period_end = _as_date(r["period_end"])
        rcept_no = _as_str(r["rcept_no"])
        filing_date = _as_date(r["rcept_dt"]).isoformat()
        available_from = _as_date(r["available_from"]).isoformat()
        fact_id = _fact_id(metric_id, fs_scope, year, quarter)

        if metric_id == "operating_margin":
            comp = margin_by_period.get((year - 1, quarter))
            change = (value - comp) if comp is not None else None
            source = [fact_id]
            comparison_period: str | None = None
            if comp is not None:
                source.append(_fact_id(metric_id, fs_scope, year - 1, quarter))
                comparison_period = f"{year - 1}Q{quarter}"
            evidence = FinancialEvidence(
                evidence_id=f"FIN_{code}_{period}",
                category=METRIC_CATEGORY[metric_id].value,
                statement=_margin_statement(year, quarter, value, comp),
                current_value=_dec(value),
                comparison_value=_dec(comp),
                change_rate=change,
                period=period,
                comparison_period=comparison_period,
                source_fact_ids=source,
                rcept_no=rcept_no,
                filing_date=filing_date,
                significance_score=_significance(
                    change,
                    sign_change=False,
                    period_end=period_end,
                    as_of=as_of,
                    lookback_years=lookback_years,
                ),
                fs_scope=fs_scope,
                available_from=available_from,
            )
        else:
            evidence = FinancialEvidence(
                evidence_id=f"FIN_{code}_{period}",
                category=METRIC_CATEGORY[metric_id].value,
                statement=_yoy_statement(METRIC_DISPLAY[metric_id], year, quarter, value),
                current_value=None,
                comparison_value=None,
                change_rate=value,
                period=period,
                comparison_period=f"{year - 1}Q{quarter}",
                source_fact_ids=[fact_id],
                rcept_no=rcept_no,
                filing_date=filing_date,
                significance_score=_significance(
                    value,
                    sign_change=False,
                    period_end=period_end,
                    as_of=as_of,
                    lookback_years=lookback_years,
                ),
                fs_scope=fs_scope,
                available_from=available_from,
            )
        out.append(evidence)
    return out


# --- 계정 evidence (wide parquet) -------------------------------------------


@dataclass(frozen=True)
class _Point:
    """한 계정의 한 기간 관측치 (계정 evidence 파생용)."""

    year: int
    quarter: int | None  # None = 연간
    value: int
    period_end: date
    rcept_no: str
    filing_date: str
    available_from: str


def _account_evidence(
    *,
    annual: pd.DataFrame,
    quarterly: pd.DataFrame,
    fs_scope: str,
    as_of: date,
    lookback_years: int,
) -> list[FinancialEvidence]:
    """계정 wide 프레임에서 연간 증감률·부호 전환·추세 evidence를 만든다 (명세 §3.3-2)."""
    out: list[FinancialEvidence] = []
    for account in ACCOUNT_CODE:
        annual_series = _series(annual, account, annual=True)
        quarterly_series = _series(quarterly, account, annual=False)

        out += _annual_yoy_evidence(annual_series, account, fs_scope, as_of, lookback_years)
        if account in SIGNABLE_ACCOUNTS:
            out += _sign_change_evidence(
                annual_series, account, fs_scope, as_of, lookback_years, annual=True
            )
            out += _sign_change_evidence(
                quarterly_series, account, fs_scope, as_of, lookback_years, annual=False
            )
        trend = _trend_evidence(annual_series, account, fs_scope, as_of, lookback_years)
        if trend is not None:
            out.append(trend)
    return out


def _series(df: pd.DataFrame, account: str, *, annual: bool) -> list[_Point]:
    """wide 프레임에서 한 계정의 (값이 있는) 관측치를 연·분기 순으로 정렬해 반환한다."""
    if account not in df.columns:
        return []
    points: list[_Point] = []
    for r in _records(df):
        value = _as_int(r[account])
        if value is None:
            continue
        quarter = None if annual else _req_int(r["fiscal_quarter"])
        points.append(
            _Point(
                year=_req_int(r["fiscal_year"]),
                quarter=quarter,
                value=value,
                period_end=_as_date(r["period_end"]),
                rcept_no=_as_str(r["rcept_no"]),
                filing_date=_as_date(r["rcept_dt"]).isoformat(),
                available_from=_as_date(r["available_from"]).isoformat(),
            )
        )
    points.sort(key=lambda p: (p.year, p.quarter or 0))
    return points


def _annual_yoy_evidence(
    series: list[_Point], account: str, fs_scope: str, as_of: date, lookback_years: int
) -> list[FinancialEvidence]:
    """연속 연도(전년 대비) 증감률 evidence — A4 분기 YoY와 중복되지 않는 연간 파생."""
    out: list[FinancialEvidence] = []
    by_year = {p.year: p for p in series}
    for cur in series:
        prev = by_year.get(cur.year - 1)
        if prev is None or prev.value == 0:
            continue
        yoy = (cur.value - prev.value) / abs(prev.value)
        out.append(
            FinancialEvidence(
                evidence_id=f"FIN_{ACCOUNT_CODE[account]}_YOY_FY{cur.year}",
                category=ACCOUNT_CATEGORY[account].value,
                statement=_annual_yoy_statement(ACCOUNT_DISPLAY[account], cur.year, yoy),
                current_value=_dec(cur.value),
                comparison_value=_dec(prev.value),
                change_rate=yoy,
                period=f"FY{cur.year}",
                comparison_period=f"FY{cur.year - 1}",
                source_fact_ids=[
                    _fact_id(account, fs_scope, cur.year, None),
                    _fact_id(account, fs_scope, prev.year, None),
                ],
                rcept_no=cur.rcept_no,
                filing_date=cur.filing_date,
                significance_score=_significance(
                    yoy,
                    sign_change=False,
                    period_end=cur.period_end,
                    as_of=as_of,
                    lookback_years=lookback_years,
                ),
                fs_scope=fs_scope,
                available_from=cur.available_from,
            )
        )
    return out


def _sign_change_evidence(
    series: list[_Point],
    account: str,
    fs_scope: str,
    as_of: date,
    lookback_years: int,
    *,
    annual: bool,
) -> list[FinancialEvidence]:
    """흑자↔적자(부호) 전환 evidence — 전년(동기) 대비 부호가 뒤집힌 기간 (명세 §3.3-2①)."""
    out: list[FinancialEvidence] = []
    index = {(p.year, p.quarter): p for p in series}
    for cur in series:
        prev = index.get((cur.year - 1, cur.quarter))
        if prev is None or not _sign_flips(prev.value, cur.value):
            continue
        change = (cur.value - prev.value) / abs(prev.value) if prev.value != 0 else None
        period = f"FY{cur.year}" if annual else f"{cur.year}Q{cur.quarter}"
        comp_period = f"FY{cur.year - 1}" if annual else f"{cur.year - 1}Q{cur.quarter}"
        out.append(
            FinancialEvidence(
                evidence_id=f"FIN_{ACCOUNT_CODE[account]}_TURN_{period}",
                category=ACCOUNT_CATEGORY[account].value,
                statement=_turn_statement(
                    ACCOUNT_DISPLAY[account], cur.year, cur.quarter, prev.value, cur.value
                ),
                current_value=_dec(cur.value),
                comparison_value=_dec(prev.value),
                change_rate=change,
                period=period,
                comparison_period=comp_period,
                source_fact_ids=[
                    _fact_id(account, fs_scope, cur.year, cur.quarter),
                    _fact_id(account, fs_scope, prev.year, prev.quarter),
                ],
                rcept_no=cur.rcept_no,
                filing_date=cur.filing_date,
                significance_score=_significance(
                    change,
                    sign_change=True,
                    period_end=cur.period_end,
                    as_of=as_of,
                    lookback_years=lookback_years,
                ),
                fs_scope=fs_scope,
                available_from=cur.available_from,
            )
        )
    return out


def _trend_evidence(
    series: list[_Point], account: str, fs_scope: str, as_of: date, lookback_years: int
) -> FinancialEvidence | None:
    """최근 연간 3기 이상 연속 증가/감소 추세 evidence (명세 §3.3-2③).

    최신 연도에서 과거로 걸어가며 연속 연도(gap 없음)·단조 방향이 유지되는 최장
    구간을 찾는다. 길이 3 미만이면 evidence 없음.
    """
    if len(series) < 3:
        return None
    run: list[_Point] = [series[-1]]
    direction: str | None = None
    for i in range(len(series) - 2, -1, -1):
        cur, prev = series[i + 1], series[i]
        if prev.year != cur.year - 1:
            break
        if cur.value > prev.value:
            step = "up"
        elif cur.value < prev.value:
            step = "down"
        else:
            break  # 동일값은 추세를 끊는다
        if direction is None:
            direction = step
        elif step != direction:
            break
        run.append(prev)
    if len(run) < 3 or direction is None:
        return None
    run.reverse()  # 과거 → 최신
    start, end = run[0], run[-1]
    change = (end.value - start.value) / abs(start.value) if start.value != 0 else None
    return FinancialEvidence(
        evidence_id=f"FIN_{ACCOUNT_CODE[account]}_TREND_FY{end.year}",
        category=ACCOUNT_CATEGORY[account].value,
        statement=_trend_statement(
            ACCOUNT_DISPLAY[account], start.year, end.year, len(run), direction
        ),
        current_value=_dec(end.value),
        comparison_value=_dec(start.value),
        change_rate=change,
        period=f"FY{end.year}",
        comparison_period=f"FY{start.year}",
        source_fact_ids=[_fact_id(account, fs_scope, p.year, None) for p in run],
        rcept_no=end.rcept_no,
        filing_date=end.filing_date,
        significance_score=_significance(
            change,
            sign_change=False,
            period_end=end.period_end,
            as_of=as_of,
            lookback_years=lookback_years,
        ),
        fs_scope=fs_scope,
        available_from=end.available_from,
    )


# --- significance ------------------------------------------------------------


def _significance(
    change_rate: float | None,
    *,
    sign_change: bool,
    period_end: date,
    as_of: date,
    lookback_years: int,
) -> float:
    """결정적 유의도 점수 ∈ [0,1] (명세 §3.3).

    ``score = 0.5·magnitude + 0.2·sign + 0.3·recency``:

    - ``magnitude = |change_rate| / (|change_rate| + 0.5)`` — |change_rate|에 단조
      증가, 스케일이 다른 YoY(수백 %)와 이익률 변화(수 %p)를 [0,1)로 압축한다.
      change_rate None이면 0.
    - ``sign`` — 부호 전환이면 1(가점 0.2), 아니면 0.
    - ``recency = 1 - (as_of - period_end)/lookback 창`` — as_of에 가까울수록 ↑,
      [0,1] 클램프.

    가중치 합이 1이라 결과는 자연히 [0,1]이며 최종 클램프로 보증한다.
    """
    magnitude = 0.0
    if change_rate is not None:
        m = abs(change_rate)
        magnitude = m / (m + _MAGNITUDE_HALF)
    sign = 1.0 if sign_change else 0.0
    span_days = max(1, lookback_years * 365)
    recency = 1.0 - (as_of - period_end).days / span_days
    recency = min(1.0, max(0.0, recency))
    score = _W_MAGNITUDE * magnitude + _W_SIGN * sign + _W_RECENCY * recency
    return min(1.0, max(0.0, score))


# --- statement 템플릿 (한국어, 결정적) --------------------------------------


def _yoy_statement(display: str, year: int, quarter: int, value: float) -> str:
    direction = "증가" if value >= 0 else "감소"
    return (
        f"{year}년 {quarter}분기 {display}{_josa(display, '이', '가')} 전년 동기 대비 "
        f"{abs(value) * 100:.1f}% {direction}했다."
    )


def _margin_statement(year: int, quarter: int, value: float, comp: float | None) -> str:
    cur = f"{value * 100:.1f}%"
    if comp is None:
        return f"{year}년 {quarter}분기 영업이익률은 {cur}이다."
    direction = "개선" if value >= comp else "악화"
    return (
        f"{year}년 {quarter}분기 영업이익률이 {cur}로 전년 동기({comp * 100:.1f}%) 대비 "
        f"{abs(value - comp) * 100:.1f}%p {direction}되었다."
    )


def _annual_yoy_statement(display: str, year: int, yoy: float) -> str:
    direction = "증가" if yoy >= 0 else "감소"
    return (
        f"{year}년 {display}{_josa(display, '이', '가')} 전년 대비 "
        f"{abs(yoy) * 100:.1f}% {direction}했다."
    )


def _turn_statement(display: str, year: int, quarter: int | None, prev: int, cur: int) -> str:
    period = f"{year}년" if quarter is None else f"{year}년 {quarter}분기"
    compare = "전년 대비" if quarter is None else "전년 동기 대비"
    turn = "적자에서 흑자로" if prev < 0 <= cur else "흑자에서 적자로"
    return f"{period} {display}{_josa(display, '이', '가')} {compare} {turn} 전환되었다."


def _trend_statement(
    display: str, start_year: int, end_year: int, count: int, direction: str
) -> str:
    word = "증가" if direction == "up" else "감소"
    return (
        f"{display}{_josa(display, '이', '가')} {start_year}년부터 {end_year}년까지 "
        f"{count}개 연도 연속 {word}했다."
    )


def _josa(word: str, with_batchim: str, without_batchim: str) -> str:
    """한글 종성 유무로 조사를 고른다(이/가·은/는 등). 비한글은 종성 있음으로 처리."""
    last = word[-1]
    code = ord(last) - 0xAC00
    if 0 <= code <= 11171:
        return with_batchim if code % 28 != 0 else without_batchim
    return with_batchim


# --- 저수준 헬퍼 -------------------------------------------------------------


def _sign_flips(prev: int, cur: int) -> bool:
    """전기가 적자(음수)이고 당기가 흑자(0 이상)이거나 그 반대(0을 실제로 교차)."""
    return (prev < 0 <= cur) or (cur < 0 <= prev)


def _fact_id(name: str, fs_scope: str, year: int, quarter: int | None) -> str:
    """source_fact_id 결정적 생성 — ``FACT_{name}_{scope}_{year}Q{quarter|A}`` (명세 §3.3)."""
    q_label = "A" if quarter is None else str(quarter)
    return f"FACT_{name}_{fs_scope}_{year}Q{q_label}"


def _assert_unique_ids(evidence: list[FinancialEvidence]) -> None:
    """evidence_id 중복은 생성 규칙 버그이므로 즉시 실패한다 (명세 §3.3)."""
    seen: set[str] = set()
    dups: list[str] = []
    for e in evidence:
        if e.evidence_id in seen:
            dups.append(e.evidence_id)
        seen.add(e.evidence_id)
    if dups:
        raise DataValidationError(f"evidence_id 중복: {sorted(set(dups))}")


def _records(df: pd.DataFrame) -> list[dict[str, object]]:
    return cast("list[dict[str, object]]", df.to_dict(orient="records"))


def _missing(v: object) -> bool:
    return (
        v is None or v is pd.NA or v is pd.NaT or (isinstance(v, float) and v != v)  # NaN
    )


def _as_int(v: object) -> int | None:
    if _missing(v):
        return None
    return int(cast(SupportsInt, v))


def _req_int(v: object) -> int:
    value = _as_int(v)
    if value is None:
        raise DataValidationError("정수 컬럼에 결측값이 있습니다.")
    return value


def _as_float(v: object) -> float | None:
    if _missing(v):
        return None
    return float(cast(SupportsFloat, v))


def _as_date(v: object) -> date:
    if isinstance(v, datetime):  # datetime·Timestamp은 date의 하위형
        return v.date()
    if isinstance(v, date):
        return v
    raise DataValidationError(f"날짜 컬럼에 예상치 못한 값: {v!r}")


def _as_str(v: object) -> str:
    return "" if _missing(v) else str(v)


def _dec(v: int | float | None) -> Decimal | None:
    if v is None:
        return None
    if isinstance(v, int):
        return Decimal(v)
    return Decimal(str(v))


__all__ = ["build_financial_evidence"]
