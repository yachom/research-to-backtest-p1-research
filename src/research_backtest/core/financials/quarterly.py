"""단독분기 역산 + available_from 부여 (명세 A4 §4~§5, README §10.2·§11·§22.1).

관측치(:class:`ReportObservation`)를 단독분기·연간 :class:`Fact`로 변환한다.
12월 결산 가정. 계정 계열별로 API 필드 의미가 다르다 —

**손익(IS·CIS, period_flow)** — 전체 재무제표 API 의미론(README §843):
분·반기 손익의 ``thstrm_amount``=해당 3개월, ``thstrm_add_amount``=누적.
따라서 Q2/Q3 단독은 ``thstrm_amount``(있으면 REPORTED), 없으면
``누적 - 직전 누적``(DERIVED_QUARTER). Q4는 항상 ``연간 - 3Q누적``.

**현금흐름표(CF, cumulative_flow)** — **A4 실측(SK하이닉스 00164779)**:
CF 행에는 ``thstrm_add_amount`` 필드가 **아예 없고** ``thstrm_amount``가
**누적(YTD)**이다(Q1=3M, 반기=6M, 3Q=9M, 연간=12M로 단조 증가 확인).
그래서 단독분기는 인접 누적의 차분으로만 얻으며 Q2/Q3/Q4가 전부
DERIVED_QUARTER다. 차분은 telescoping이라 4개 단독합 = 연간이 항상 성립한다.

**재무상태표(BS, instant)** — ``thstrm_amount``가 기말잔액. 각 분기는 해당
분기말 잔액, Q4·연간은 연말 잔액(= 사업보고서 값)이라 전부 REPORTED.

역산 전제(README §11.2, 동일 계정·scope·단위) 위반(입력 결측) 시 해당 값을
**결측 처리**하고 gap으로 기록한다(조용한 오답 금지, 명세 §4).

available_from(명세 §5, README §22.1): ``rcept_dt = date(rcept_no[:8])``,
``available_from = calendar.next_trading_day(rcept_dt)``. 파생값의
available_from은 **기여 보고서들의 available_from 중 max**다(파생값은 모든
입력이 공개된 뒤에만 알 수 있다). coverage 밖이면 예외 전파(조용한 대체 금지).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from research_backtest.core.constants import ReprtCode
from research_backtest.core.dates import TradingCalendar
from research_backtest.core.financials.normalizer import NormalizationResult, ReportObservation
from research_backtest.core.financials.registry import CanonicalAccount

REPORTED = "REPORTED"
DERIVED_QUARTER = "DERIVED_QUARTER"

# 분기 → (period_start month/day, period_end month/day) — 12월 결산 가정
_QUARTER_BOUNDS: dict[int, tuple[tuple[int, int], tuple[int, int]]] = {
    1: ((1, 1), (3, 31)),
    2: ((4, 1), (6, 30)),
    3: ((7, 1), (9, 30)),
    4: ((10, 1), (12, 31)),
}


@dataclass
class Fact:
    """단독분기 또는 연간 정규화 fact — normalized_facts.parquet 1행 (명세 §7-①).

    ``value``는 항상 계산 가능한 정수만 담는다(결측은 fact를 만들지 않고 gap
    기록). ``rcept_no``·``rcept_dt``·``available_from``은
    :func:`apply_available_from`가 채운다.
    """

    canonical_id: str
    fs_scope: str
    sj_div: str
    fiscal_year: int
    fiscal_quarter: int | None  # None = 연간
    period_start: date | None  # instant(BS)은 None
    period_end: date
    value: int
    value_type: str
    source_account_id: str
    source_account_nm: str
    contributing_rcept_nos: list[str]
    rcept_no: str = ""
    rcept_dt: date | None = None
    available_from: date | None = None


@dataclass
class DerivationGap:
    """역산/보고에 필요한 입력이 결측되어 값을 만들지 못한 (계정·기간) (명세 §4)."""

    canonical_id: str
    fs_scope: str
    fiscal_year: int
    fiscal_quarter: int | None
    reason: str


@dataclass
class QuarterlyResult:
    facts: list[Fact] = field(default_factory=list)
    gaps: list[DerivationGap] = field(default_factory=list)


def rcept_to_date(rcept_no: str) -> date:
    """접수번호 앞 8자리(YYYYMMDD)를 접수일로 해석한다 (명세 §5, README §22.1)."""
    head = rcept_no[:8]
    if len(head) != 8 or not head.isdigit():
        raise ValueError(f"rcept_no에서 접수일(YYYYMMDD)을 얻을 수 없습니다: {rcept_no!r}")
    return date(int(head[:4]), int(head[4:6]), int(head[6:8]))


def period_bounds(fiscal_year: int, fiscal_quarter: int | None) -> tuple[date, date]:
    """(연도·분기)의 (period_start, period_end) — 분기 None이면 연간(1/1~12/31)."""
    if fiscal_quarter is None:
        return date(fiscal_year, 1, 1), date(fiscal_year, 12, 31)
    (sm, sd), (em, ed) = _QUARTER_BOUNDS[fiscal_quarter]
    return date(fiscal_year, sm, sd), date(fiscal_year, em, ed)


def derive_facts(
    normalization: NormalizationResult, registry: dict[str, CanonicalAccount]
) -> QuarterlyResult:
    """관측치를 단독분기·연간 fact로 변환한다 (명세 §4).

    (canonical, scope, year)마다 4개 보고서 관측치를 모아 계정 계열에 맞는
    역산을 적용한다. 결측 입력은 :class:`DerivationGap`으로 기록한다.
    """
    result = QuarterlyResult()
    keys = sorted({(k.canonical_id, k.fs_scope, k.fiscal_year) for k in normalization.observations})
    for canonical_id, fs_scope, year in keys:
        account = registry[canonical_id]
        obs = _ReportSet(
            q1=normalization.get(canonical_id, fs_scope, year, ReprtCode.Q1.value),
            half=normalization.get(canonical_id, fs_scope, year, ReprtCode.HALF.value),
            q3=normalization.get(canonical_id, fs_scope, year, ReprtCode.Q3.value),
            annual=normalization.get(canonical_id, fs_scope, year, ReprtCode.ANNUAL.value),
        )
        if account.period_type == "instant":
            _derive_instant(account, fs_scope, year, obs, result)
        elif account.is_cumulative_flow():
            _derive_cumulative_flow(account, fs_scope, year, obs, result)
        else:
            _derive_period_flow(account, fs_scope, year, obs, result)
    result.facts.sort(key=_fact_sort_key)
    return result


def apply_available_from(facts: Sequence[Fact], calendar: TradingCalendar) -> None:
    """각 fact에 rcept_no·rcept_dt·available_from을 부여한다 (명세 §5, 제자리 갱신).

    available_from = 기여 보고서들의 available_from 중 **max**. rcept_no·rcept_dt는
    그 max를 만든 보고서 기준으로 맞춘다. coverage 밖이면 CalendarRangeError 전파.
    """
    for fact in facts:
        best_rcept = ""
        best_dt: date | None = None
        best_af: date | None = None
        for rcept_no in fact.contributing_rcept_nos:
            dt = rcept_to_date(rcept_no)
            af = calendar.next_trading_day(dt)
            if best_af is None or af > best_af:
                best_af, best_dt, best_rcept = af, dt, rcept_no
        if best_af is None or best_dt is None:
            raise ValueError(
                f"fact에 기여 rcept_no가 없습니다: {fact.canonical_id} {fact.fiscal_year}"
            )
        fact.rcept_no = best_rcept
        fact.rcept_dt = best_dt
        fact.available_from = best_af


# --- 내부 구현 ---------------------------------------------------------------


@dataclass
class _ReportSet:
    """한 (canonical, scope, year)의 4개 보고서 관측치."""

    q1: ReportObservation | None
    half: ReportObservation | None
    q3: ReportObservation | None
    annual: ReportObservation | None


def _derive_instant(
    account: CanonicalAccount, fs_scope: str, year: int, obs: _ReportSet, result: QuarterlyResult
) -> None:
    """BS(instant): 각 분기말·연말 잔액을 그대로 REPORTED로 emit (period_start=None)."""
    plan: list[tuple[int | None, ReportObservation | None]] = [
        (1, obs.q1),
        (2, obs.half),
        (3, obs.q3),
        (4, obs.annual),  # Q4 잔액 = 연말 잔액 = 사업보고서 값
        (None, obs.annual),  # 연간
    ]
    for quarter, source in plan:
        if source is None or source.thstrm_amount is None:
            _add_gap(result, account, fs_scope, year, quarter, "BS 잔액 관측치 결측")
            continue
        _emit(
            result,
            account,
            fs_scope,
            year,
            quarter,
            source.thstrm_amount,
            REPORTED,
            source,
            [source],
        )


def _derive_period_flow(
    account: CanonicalAccount, fs_scope: str, year: int, obs: _ReportSet, result: QuarterlyResult
) -> None:
    """손익(IS/CIS): thstrm=3개월/add=누적. Q2/Q3는 thstrm 우선, Q4는 연간-3Q누적."""
    singles: dict[int, int] = {}

    # 연간 = 사업보고서 thstrm
    if obs.annual is not None and obs.annual.thstrm_amount is not None:
        _emit(
            result,
            account,
            fs_scope,
            year,
            None,
            obs.annual.thstrm_amount,
            REPORTED,
            obs.annual,
            [obs.annual],
        )
    else:
        _add_gap(result, account, fs_scope, year, None, "연간 손익 관측치 결측")

    # Q1 = Q1보고서 thstrm (= 1분기 누적)
    if obs.q1 is not None and obs.q1.thstrm_amount is not None:
        singles[1] = obs.q1.thstrm_amount
        _emit(result, account, fs_scope, year, 1, obs.q1.thstrm_amount, REPORTED, obs.q1, [obs.q1])
    else:
        _add_gap(result, account, fs_scope, year, 1, "Q1 손익 관측치 결측")

    # Q2 = 반기보고서 thstrm(3개월) 우선, 없으면 반기누적 - Q1
    q2 = _period_single(obs.half, obs.q1, result, account, fs_scope, year, 2)
    if q2 is not None:
        singles[2] = q2[0]

    # Q3 = 3Q보고서 thstrm 우선, 없으면 3Q누적 - 반기누적
    q3 = _period_single_q3(obs.q3, obs.half, result, account, fs_scope, year)
    if q3 is not None:
        singles[3] = q3[0]

    # Q4 = 연간 - 3Q누적(11014 add), 없으면 연간 - (Q1+Q2+Q3 단독합) — 항상 DERIVED
    _derive_income_q4(account, fs_scope, year, obs, singles, result)


def _period_single(
    half: ReportObservation | None,
    q1: ReportObservation | None,
    result: QuarterlyResult,
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
    quarter: int,
) -> tuple[int, ReportObservation] | None:
    """Q2 단독: thstrm(3개월) 우선(REPORTED), 없으면 반기누적 - Q1(DERIVED)."""
    if half is not None and half.thstrm_amount is not None:
        _emit(result, account, fs_scope, year, quarter, half.thstrm_amount, REPORTED, half, [half])
        return half.thstrm_amount, half
    if (
        half is not None
        and half.thstrm_add_amount is not None
        and q1 is not None
        and q1.thstrm_amount is not None
    ):
        value = half.thstrm_add_amount - q1.thstrm_amount
        _emit(result, account, fs_scope, year, quarter, value, DERIVED_QUARTER, half, [half, q1])
        return value, half
    _add_gap(result, account, fs_scope, year, quarter, "Q2 thstrm·반기누적/Q1 결측")
    return None


def _period_single_q3(
    q3: ReportObservation | None,
    half: ReportObservation | None,
    result: QuarterlyResult,
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
) -> tuple[int, ReportObservation] | None:
    """Q3 단독: thstrm(3개월) 우선(REPORTED), 없으면 3Q누적 - 반기누적(DERIVED)."""
    if q3 is not None and q3.thstrm_amount is not None:
        _emit(result, account, fs_scope, year, 3, q3.thstrm_amount, REPORTED, q3, [q3])
        return q3.thstrm_amount, q3
    if (
        q3 is not None
        and q3.thstrm_add_amount is not None
        and half is not None
        and half.thstrm_add_amount is not None
    ):
        value = q3.thstrm_add_amount - half.thstrm_add_amount
        _emit(result, account, fs_scope, year, 3, value, DERIVED_QUARTER, q3, [q3, half])
        return value, q3
    _add_gap(result, account, fs_scope, year, 3, "Q3 thstrm·3Q누적/반기누적 결측")
    return None


def _derive_income_q4(
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
    obs: _ReportSet,
    singles: dict[int, int],
    result: QuarterlyResult,
) -> None:
    """Q4 = 연간 - 3Q누적(11014 add). 없으면 연간 - (Q1+Q2+Q3 단독합) — 항상 DERIVED."""
    if obs.annual is None or obs.annual.thstrm_amount is None:
        _add_gap(result, account, fs_scope, year, 4, "Q4 역산용 연간 관측치 결측")
        return
    annual_value = obs.annual.thstrm_amount
    if obs.q3 is not None and obs.q3.thstrm_add_amount is not None:
        value = annual_value - obs.q3.thstrm_add_amount
        contributing = [obs.annual, obs.q3]
        _emit(result, account, fs_scope, year, 4, value, DERIVED_QUARTER, obs.annual, contributing)
        return
    if {1, 2, 3} <= singles.keys():
        value = annual_value - (singles[1] + singles[2] + singles[3])
        contributing = [o for o in (obs.annual, obs.q1, obs.half, obs.q3) if o is not None]
        _emit(result, account, fs_scope, year, 4, value, DERIVED_QUARTER, obs.annual, contributing)
        return
    _add_gap(result, account, fs_scope, year, 4, "Q4 역산용 3Q누적·단독합 결측")


def _derive_cumulative_flow(
    account: CanonicalAccount, fs_scope: str, year: int, obs: _ReportSet, result: QuarterlyResult
) -> None:
    """CF: thstrm=누적(YTD). 단독분기는 인접 누적 차분(Q2~Q4 전부 DERIVED)."""
    # 연간 = 사업보고서 thstrm(12개월 누적)
    if obs.annual is not None and obs.annual.thstrm_amount is not None:
        _emit(
            result,
            account,
            fs_scope,
            year,
            None,
            obs.annual.thstrm_amount,
            REPORTED,
            obs.annual,
            [obs.annual],
        )
    else:
        _add_gap(result, account, fs_scope, year, None, "연간 CF 관측치 결측")

    # Q1 단독 = Q1 누적 (REPORTED)
    if obs.q1 is not None and obs.q1.thstrm_amount is not None:
        _emit(result, account, fs_scope, year, 1, obs.q1.thstrm_amount, REPORTED, obs.q1, [obs.q1])
    else:
        _add_gap(result, account, fs_scope, year, 1, "Q1 CF 누적 결측")

    # Q2~Q4 = 인접 누적 차분 (DERIVED)
    _diff_flow(result, account, fs_scope, year, 2, obs.half, obs.q1)
    _diff_flow(result, account, fs_scope, year, 3, obs.q3, obs.half)
    _diff_flow(result, account, fs_scope, year, 4, obs.annual, obs.q3)


def _diff_flow(
    result: QuarterlyResult,
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
    quarter: int,
    later: ReportObservation | None,
    earlier: ReportObservation | None,
) -> None:
    """CF 단독분기 = later 누적 - earlier 누적 (DERIVED_QUARTER)."""
    if (
        later is not None
        and later.thstrm_amount is not None
        and earlier is not None
        and earlier.thstrm_amount is not None
    ):
        value = later.thstrm_amount - earlier.thstrm_amount
        _emit(
            result,
            account,
            fs_scope,
            year,
            quarter,
            value,
            DERIVED_QUARTER,
            later,
            [later, earlier],
        )
    else:
        _add_gap(result, account, fs_scope, year, quarter, "CF 인접 누적 차분 입력 결측")


def _emit(
    result: QuarterlyResult,
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
    quarter: int | None,
    value: int,
    value_type: str,
    primary: ReportObservation,
    contributing: Sequence[ReportObservation],
) -> None:
    """Fact 1개를 추가한다 — instant는 period_start=None(기말잔액)."""
    start, end = period_bounds(year, quarter)
    period_start = None if account.period_type == "instant" else start
    result.facts.append(
        Fact(
            canonical_id=account.canonical_id,
            fs_scope=fs_scope,
            sj_div=primary.sj_div,
            fiscal_year=year,
            fiscal_quarter=quarter,
            period_start=period_start,
            period_end=end,
            value=value,
            value_type=value_type,
            source_account_id=primary.source_account_id,
            source_account_nm=primary.source_account_nm,
            contributing_rcept_nos=[o.rcept_no for o in contributing],
        )
    )


def _add_gap(
    result: QuarterlyResult,
    account: CanonicalAccount,
    fs_scope: str,
    year: int,
    quarter: int | None,
    reason: str,
) -> None:
    result.gaps.append(
        DerivationGap(
            canonical_id=account.canonical_id,
            fs_scope=fs_scope,
            fiscal_year=year,
            fiscal_quarter=quarter,
            reason=reason,
        )
    )


def _fact_sort_key(fact: Fact) -> tuple[str, int, int, str]:
    # 결정적 정렬: scope → year → quarter(연간=0) → canonical
    return (fact.fs_scope, fact.fiscal_year, fact.fiscal_quarter or 0, fact.canonical_id)
