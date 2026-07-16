"""파이프라인 상태 머신 (docs/HUMAN_IN_THE_LOOP.md §3, 원문 §13).

12개 상태는 선언 순서 그대로 전진 순서다. 허용되지 않은 전이(건너뛰기·명시되지
않은 회귀)는 :class:`~research_backtest.core.exceptions.ApprovalGateError`로
차단한다 — 승인되지 않은 단계를 조용히 건너뛰지 않는다(원문 §13).
"""

import re
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from research_backtest.core.exceptions import ApprovalGateError
from research_backtest.core.hitl.models import now_kst_iso


class PipelineState(StrEnum):
    """파이프라인 실행 상태 12종 (docs/HUMAN_IN_THE_LOOP.md §3). 선언 순서 = 전진 순서."""

    DATA_READY = "DATA_READY"
    CANDIDATE_ANALYSIS_READY = "CANDIDATE_ANALYSIS_READY"
    AWAITING_ANALYST_VIEW = "AWAITING_ANALYST_VIEW"
    ANALYST_VIEW_APPROVED = "ANALYST_VIEW_APPROVED"
    HYPOTHESIS_DRAFT = "HYPOTHESIS_DRAFT"
    HYPOTHESIS_APPROVED = "HYPOTHESIS_APPROVED"
    STRATEGY_DRAFT_READY = "STRATEGY_DRAFT_READY"
    AWAITING_STRATEGY_REVIEW = "AWAITING_STRATEGY_REVIEW"
    STRATEGY_APPROVED = "STRATEGY_APPROVED"
    BACKTEST_COMPLETE = "BACKTEST_COMPLETE"
    AWAITING_INTERPRETATION = "AWAITING_INTERPRETATION"
    COMPLETE = "COMPLETE"


#: 선언 순서 = 정상 전진 순서 (states.py §3 "선언 순서상 바로 다음 상태로 전진").
FORWARD_ORDER: tuple[PipelineState, ...] = tuple(PipelineState)

#: 명시적 회귀 에지 4종 (원문 §13 "수정 시 되돌림 전이는 허용", H1 §3).
ALLOWED_REGRESSIONS: dict[PipelineState, PipelineState] = {
    PipelineState.ANALYST_VIEW_APPROVED: PipelineState.AWAITING_ANALYST_VIEW,
    PipelineState.HYPOTHESIS_APPROVED: PipelineState.HYPOTHESIS_DRAFT,
    PipelineState.STRATEGY_APPROVED: PipelineState.AWAITING_STRATEGY_REVIEW,
    PipelineState.COMPLETE: PipelineState.AWAITING_INTERPRETATION,
}


class StateTransition(BaseModel):
    """상태 전이 이력 1건."""

    model_config = ConfigDict(extra="forbid")

    from_state: PipelineState | None  # 최초 진입은 None
    to_state: PipelineState
    actor: str  # "user" | "system" | "test-fixture" 등
    at: str
    auto_approved: bool = False  # 테스트 플래그 사용 시 True (원문 §13)
    note: str | None = None


class RunState(BaseModel):
    """실행 1건의 현재 상태 + 전이 이력."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    company: str
    as_of_date: str
    current_state: PipelineState
    transitions: list[StateTransition]


def _is_allowed_transition(from_state: PipelineState | None, to_state: PipelineState) -> bool:
    """건너뛰기 없는 정상 전진 또는 허용된 회귀 에지인지 판정한다."""
    if from_state is None:
        return to_state == FORWARD_ORDER[0]
    if ALLOWED_REGRESSIONS.get(from_state) == to_state:
        return True
    idx = FORWARD_ORDER.index(from_state)
    next_idx = idx + 1
    return next_idx < len(FORWARD_ORDER) and FORWARD_ORDER[next_idx] == to_state


def advance(
    run_state: RunState,
    to_state: PipelineState,
    *,
    actor: str,
    auto_approved: bool = False,
    note: str | None = None,
) -> RunState:
    """``run_state``를 ``to_state``로 전진·회귀시키고 이력을 append한 새 RunState를 반환한다.

    허용되지 않은 전이(건너뛰기, 명시되지 않은 회귀)는 :class:`ApprovalGateError`.
    원본 ``run_state``는 변경하지 않는다.
    """
    if not _is_allowed_transition(run_state.current_state, to_state):
        raise ApprovalGateError(
            f"허용되지 않은 상태 전이입니다: {run_state.current_state.value} → {to_state.value}. "
            "건너뛰기 없이 순차 전진하거나 명시적으로 허용된 회귀 전이만 사용할 수 있습니다"
            "(docs/HUMAN_IN_THE_LOOP.md §3)."
        )
    transition = StateTransition(
        from_state=run_state.current_state,
        to_state=to_state,
        actor=actor,
        at=now_kst_iso(),
        auto_approved=auto_approved,
        note=note,
    )
    return run_state.model_copy(
        update={
            "current_state": to_state,
            "transitions": [*run_state.transitions, transition],
        }
    )


def create_run_state(
    run_id: str,
    company: str,
    as_of_date: str,
    *,
    actor: str,
    auto_approved: bool = False,
    note: str | None = None,
) -> RunState:
    """최초 RunState를 생성한다 — 최초 진입 전이(from_state=None)를 이력에 남긴다.

    H1 §3 코드 스텁의 ``from_state: PipelineState | None  # 최초 진입은 None`` 을
    실제로 발생시키는 진입점이다. 명세에 별도 함수명이 명시되지 않아 추가한
    헬퍼이며(구현 보강), CLI·store.py가 실행을 처음 등록할 때 이 함수로
    ``DATA_READY`` 상태의 RunState를 만든다.
    """
    initial_state = FORWARD_ORDER[0]
    transition = StateTransition(
        from_state=None,
        to_state=initial_state,
        actor=actor,
        at=now_kst_iso(),
        auto_approved=auto_approved,
        note=note,
    )
    return RunState(
        run_id=run_id,
        company=company,
        as_of_date=as_of_date,
        current_state=initial_state,
        transitions=[transition],
    )


_SLUG_KEEP_RE = re.compile(r"[^0-9A-Za-z가-힣]+")


def generate_run_id(company: str, now: datetime) -> str:
    """``YYYYMMDD_HHMMSS_{회사명 슬러그}`` 형식의 run_id를 만든다 (README §29).

    슬러그는 회사명에서 영숫자·한글만 남기고 나머지는 ``_``로 접자, 앞뒤
    ``_``는 제거한 뒤 대문자화한다. DART corp_eng_name 조회로 만드는 정식
    영문 티커 매핑(예: "SK하이닉스" → "SKHYNIX")은 core/dart의 책임이라 H1은
    의존하지 않는다 — 이 함수는 ``company`` 문자열만으로 결정적 슬러그를
    만드는 순수 함수다. 슬러그가 비면 "COMPANY"로 대체한다.
    """
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    slug = _SLUG_KEEP_RE.sub("_", company.strip()).strip("_").upper()
    if not slug:
        slug = "COMPANY"
    return f"{timestamp}_{slug}"


__all__ = [
    "ALLOWED_REGRESSIONS",
    "FORWARD_ORDER",
    "PipelineState",
    "RunState",
    "StateTransition",
    "advance",
    "create_run_state",
    "generate_run_id",
]
