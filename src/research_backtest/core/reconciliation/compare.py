"""값 비교·상태 분류 — ReconciliationResult (명세 B3 §4, README §16.3·§16.4).

선택된 XBRL fact(:mod:`.xbrl_select`)와 API 값(A4 정규화 값)을 비교해
:class:`ReconciliationResult`로 분류한다. 비교는 float를 거치지 않는다 —
XBRL ``raw_value``와 API 정수를 모두 :class:`~decimal.Decimal`로 올려
정밀 비교한다(README §9.6 금지 사항, 명세 §4). 큰 KRW 값(자산총계 ~1.8e14)도
Decimal이면 오염이 없다.

허용 오차(README §16.3): 절대 1e6 KRW **또는** 상대 0.1%, **``<=`` 판정**
(DATA_NOTES A4-④의 정확 -1,000,000 KRW 경계 사례를 ROUNDING으로 흡수).
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel

from research_backtest.core.reconciliation.xbrl_select import FactSelection, SelectionStage

# 허용 오차 (README §16.3) — Decimal 상수로 float 오염 차단.
ABSOLUTE_TOLERANCE = Decimal(1_000_000)
RELATIVE_TOLERANCE = Decimal("0.001")


class ReconciliationStatus(StrEnum):
    """대조 상태 (README §16.4 상태 목록 그대로)."""

    MATCH = "MATCH"
    ROUNDING_DIFFERENCE = "ROUNDING_DIFFERENCE"
    CONTEXT_MISMATCH = "CONTEXT_MISMATCH"
    SCOPE_MISMATCH = "SCOPE_MISMATCH"
    ACCOUNT_MAPPING_MISMATCH = "ACCOUNT_MAPPING_MISMATCH"
    MISSING_IN_API = "MISSING_IN_API"
    MISSING_IN_XBRL = "MISSING_IN_XBRL"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"


#: MATCH·ROUNDING 이외 = 실패(=failures.csv 대상, README §19.7).
PASSING_STATUSES = frozenset({ReconciliationStatus.MATCH, ReconciliationStatus.ROUNDING_DIFFERENCE})


class ReconciliationResult(BaseModel):
    """API-XBRL 교차검증 결과 1건 (README §16.4 스키마 그대로).

    ``major_account_value``는 이번 범위(주요계정 API 3원 대조)에서 항상 None이다
    (명세 §0 비범위, §4). ``relative_difference``만 float이고(비율 표시용),
    금액·절대차는 Decimal로 보존한다.
    """

    canonical_account_id: str
    period_end: str
    fs_scope: str

    api_value: Decimal | None
    xbrl_value: Decimal | None
    major_account_value: Decimal | None

    absolute_difference: Decimal | None
    relative_difference: float | None

    status: str
    reason: str | None


def classify(
    selection: FactSelection,
    *,
    api_value: Decimal | None,
    canonical_account_id: str,
    period_end: str,
    fs_scope: str,
) -> ReconciliationResult:
    """선택 결과 + API 값 → :class:`ReconciliationResult` (명세 §4 분류표).

    분류 우선순위: API 값 부재(MISSING_IN_API) → 선택 단계별
    (MISSING_IN_XBRL / CONTEXT_MISMATCH / SCOPE_MISMATCH) → 후보 수
    (2+ 중복은 REQUIRES_REVIEW) → 값 비교(MATCH / ROUNDING / REQUIRES_REVIEW).
    """

    def result(
        *,
        status: str,
        reason: str | None,
        api: Decimal | None = api_value,
        xbrl: Decimal | None = None,
        absolute: Decimal | None = None,
        relative: float | None = None,
    ) -> ReconciliationResult:
        return ReconciliationResult(
            canonical_account_id=canonical_account_id,
            period_end=period_end,
            fs_scope=fs_scope,
            api_value=api,
            xbrl_value=xbrl,
            major_account_value=None,
            absolute_difference=absolute,
            relative_difference=relative,
            status=status,
            reason=reason,
        )

    if api_value is None:
        return result(
            status=ReconciliationStatus.MISSING_IN_API,
            reason="A4 정규화에 해당 (계정·기간·scope) REPORTED 값이 없습니다.",
        )

    if selection.stage is SelectionStage.NO_CONCEPT:
        return result(
            status=ReconciliationStatus.MISSING_IN_XBRL,
            reason="XBRL에 concept 매칭 fact가 없습니다.",
        )
    if selection.stage is SelectionStage.NO_PERIOD:
        return result(
            status=ReconciliationStatus.CONTEXT_MISMATCH,
            reason=f"concept {selection.concept_count}건 있으나 기간 일치 context가 없습니다.",
        )
    if selection.stage is SelectionStage.NO_SCOPE:
        return result(
            status=ReconciliationStatus.SCOPE_MISMATCH,
            reason=(
                f"기간 일치 {selection.period_count}건 있으나 연결/별도 축 단독 context가 없습니다."
            ),
        )

    # stage == SELECTED — 후보 1개 이상.
    if len(selection.candidates) > 1:
        return result(
            status=ReconciliationStatus.REQUIRES_REVIEW,
            reason=_duplicate_reason(selection),
        )

    raw = selection.candidates[0].raw_value
    if raw is None:
        return result(
            status=ReconciliationStatus.MISSING_IN_XBRL,
            reason="XBRL fact 값이 비어 있습니다(nil).",
        )

    xbrl_value = Decimal(raw)
    absolute_difference = abs(api_value - xbrl_value)
    denom = max(abs(api_value), abs(xbrl_value))
    relative = (absolute_difference / denom) if denom > 0 else Decimal(0)

    if absolute_difference == 0:
        status, reason = ReconciliationStatus.MATCH, None
    elif absolute_difference <= ABSOLUTE_TOLERANCE or relative <= RELATIVE_TOLERANCE:
        status = ReconciliationStatus.ROUNDING_DIFFERENCE
        reason = f"허용 오차 이내 차이 {absolute_difference} (상대 {relative})."
    else:
        status = ReconciliationStatus.REQUIRES_REVIEW
        reason = (
            f"허용 오차 초과 — API {api_value} vs XBRL {xbrl_value} "
            f"(절대 {absolute_difference}, 상대 {relative})."
        )
    return result(
        status=status,
        reason=reason,
        xbrl=xbrl_value,
        absolute=absolute_difference,
        relative=float(relative),
    )


def _duplicate_reason(selection: FactSelection) -> str:
    """후보 2개 이상 REQUIRES_REVIEW의 reason — 후보 수·동일/상이값 명시 (명세 §4).

    Q1 보고서는 3개월(FQQ)·누적(FQA) context가 같은 (start,end)로 공존해 동일값
    후보 2개가 나온다(benign) — '동일값'을 명시해 리뷰 부담을 낮춘다.
    """
    raws = [c.raw_value for c in selection.candidates]
    distinct = sorted({r for r in raws if r is not None})
    if len(distinct) == 1:
        return (
            f"후보 {len(raws)}개 중복 — 동일값 {distinct[0]} "
            "(Q1 3개월·누적 context 공존 등, 값 일치)."
        )
    return f"후보 {len(raws)}개 중복 — 상이값 {distinct}."
