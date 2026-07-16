"""재무 지표 계산 — 단독분기 YoY·영업이익률 (명세 A4 §6, README §17.1·§21.1).

지표는 **단독분기** fact에서만 계산한다. metric_id는 README §21.1과 완전
동일하다(A5 DSL이 같은 이름을 컬럼으로 참조) — ``revenue_yoy``,
``operating_income_yoy``, ``net_income_yoy``, ``operating_margin``.

**YoY 부호 규약(중요, 명세 §6)**: ``yoy = (cur - prev) / abs(prev)``. 분모에
절댓값을 써 전년 동기가 적자(음수)일 때 개선이 **양수**로 나오게 한다 —
SK하이닉스 2023 분기 영업이익이 음수라 2024 신호에 직결된다. ``prev``가
None·0이면 지표는 None(행 미생성). ``operating_margin`` = 단독분기 영업이익 /
단독분기 매출(매출 None·0이면 None).

지표의 ``available_from``은 입력 fact들의 available_from 중 **max**이고,
``inputs_derived``는 입력 중 하나라도 DERIVED_QUARTER면 True다(A6는 이
파일을 available_from으로 as-of join한다, 명세 §7-④).
"""

from dataclasses import dataclass
from datetime import date

from research_backtest.core.financials.quarterly import DERIVED_QUARTER, Fact

# metric_id 매핑 (README §21.1과 동일) — canonical → YoY metric_id
YOY_METRICS: dict[str, str] = {
    "revenue": "revenue_yoy",
    "operating_income": "operating_income_yoy",
    "net_income": "net_income_yoy",
}
OPERATING_MARGIN = "operating_margin"


@dataclass
class Metric:
    """financial_metrics.parquet 1행 (명세 §7-④)."""

    metric_id: str
    fs_scope: str
    fiscal_year: int
    fiscal_quarter: int
    period_end: date
    value: float
    rcept_no: str
    rcept_dt: date
    available_from: date
    inputs_derived: bool


def compute_metrics(facts: list[Fact]) -> list[Metric]:
    """단독분기 fact들로 YoY·영업이익률 지표를 계산한다 (명세 §6).

    입력 fact는 available_from이 이미 부여돼 있어야 한다
    (:func:`~research_backtest.core.financials.quarterly.apply_available_from`).
    """
    index: dict[tuple[str, str, int, int], Fact] = {}
    for fact in facts:
        if fact.fiscal_quarter is None:
            continue  # 지표는 단독분기 기준
        index[(fact.canonical_id, fact.fs_scope, fact.fiscal_year, fact.fiscal_quarter)] = fact

    metrics: list[Metric] = []
    for (canonical_id, fs_scope, year, quarter), cur in index.items():
        # --- YoY (전년 동기 대비) ---
        metric_id = YOY_METRICS.get(canonical_id)
        if metric_id is not None:
            prev = index.get((canonical_id, fs_scope, year - 1, quarter))
            yoy = _yoy(cur.value, prev.value if prev is not None else None)
            if yoy is not None and prev is not None:
                metrics.append(_metric(metric_id, cur, quarter, yoy, [cur, prev]))

        # --- operating_margin (영업이익 / 매출, 동일 분기) ---
        if canonical_id == "operating_income":
            revenue = index.get(("revenue", fs_scope, year, quarter))
            if revenue is not None and revenue.value != 0:
                margin = cur.value / revenue.value
                metrics.append(_metric(OPERATING_MARGIN, cur, quarter, margin, [cur, revenue]))

    metrics.sort(key=lambda m: (m.metric_id, m.fs_scope, m.fiscal_year, m.fiscal_quarter))
    return metrics


def _yoy(cur: int, prev: int | None) -> float | None:
    """yoy = (cur - prev) / abs(prev) — prev None·0이면 None (명세 §6 부호 규약)."""
    if prev is None or prev == 0:
        return None
    return (cur - prev) / abs(prev)


def _metric(metric_id: str, anchor: Fact, quarter: int, value: float, inputs: list[Fact]) -> Metric:
    """입력 fact들의 timing(rcept·available_from)을 결합해 Metric을 만든다.

    available_from = max(입력 available_from), rcept_no·rcept_dt는 그 max를
    만든 fact 기준. inputs_derived = 입력 중 하나라도 DERIVED_QUARTER.
    """
    latest = max(inputs, key=lambda f: _require_af(f))
    return Metric(
        metric_id=metric_id,
        fs_scope=anchor.fs_scope,
        fiscal_year=anchor.fiscal_year,
        fiscal_quarter=quarter,
        period_end=anchor.period_end,
        value=value,
        rcept_no=latest.rcept_no,
        rcept_dt=_require_dt(latest),
        available_from=_require_af(latest),
        inputs_derived=any(f.value_type == DERIVED_QUARTER for f in inputs),
    )


def _require_af(fact: Fact) -> date:
    if fact.available_from is None:
        raise ValueError(f"available_from 미부여 fact: {fact.canonical_id} {fact.fiscal_year}")
    return fact.available_from


def _require_dt(fact: Fact) -> date:
    if fact.rcept_dt is None:
        raise ValueError(f"rcept_dt 미부여 fact: {fact.canonical_id} {fact.fiscal_year}")
    return fact.rcept_dt
