"""승인 게이트 (docs/AI_ROLE_BOUNDARY.md §3의 코드화).

미승인 가설의 전략 변환, 승인 기록 없는 백테스트 실행, 필요한 상태에
도달하지 못한 진행을 예외로 강제한다 — 조용히 건너뛰지 않는다.
"""

from research_backtest.core.exceptions import ApprovalGateError
from research_backtest.core.hitl.models import (
    HumanInvestmentHypothesis,
    HypothesisStatus,
    StrategyReview,
)
from research_backtest.core.hitl.states import FORWARD_ORDER, PipelineState, RunState


def ensure_hypothesis_approved(hypothesis: HumanInvestmentHypothesis) -> None:
    """승인되지 않은 가설을 전략 변환·백테스트로 넘기지 못하게 막는다 (원문 §6, §18).

    ``HumanInvestmentHypothesis``는 모델 validator로 이미
    ``status==APPROVED ⇒ approved_by·approved_at 필수``를 강제하지만, 이
    게이트는 워크플로 진입점(전략 변환·백테스트 호출부)에서 동일 조건을
    다시 명시적으로 검사하는 방어선이다.
    """
    if (
        hypothesis.status != HypothesisStatus.APPROVED
        or not hypothesis.approved_by
        or not hypothesis.approved_by.strip()
        or not hypothesis.approved_at
        or not hypothesis.approved_at.strip()
    ):
        raise ApprovalGateError(
            "승인되지 않은 가설은 전략 변환·백테스트에 전달할 수 없습니다. "
            "HumanInvestmentHypothesis.status를 APPROVED로 승인하고 "
            "approved_by·approved_at을 기록한 뒤 다시 시도하세요."
        )


def ensure_strategy_approved(review: StrategyReview | None) -> None:
    """사용자 승인 기록(StrategyReview) 없이는 백테스트를 실행하지 못하게 막는다 (원문 §9, §18)."""
    if review is None or not review.approved_by or not review.approved_by.strip():
        raise ApprovalGateError(
            "승인된 StrategyReview 없이는 백테스트를 실행할 수 없습니다. "
            "전략 초안을 검토·수정하고 approved_by를 기록한 StrategyReview를 "
            "먼저 생성하세요(approve-strategy)."
        )


def ensure_state_at_least(run_state: RunState, required: PipelineState) -> None:
    """``run_state``가 ``required`` 이상으로 전진했는지 확인한다 (건너뛰기 방지)."""
    current_idx = FORWARD_ORDER.index(run_state.current_state)
    required_idx = FORWARD_ORDER.index(required)
    if current_idx < required_idx:
        raise ApprovalGateError(
            f"현재 상태({run_state.current_state.value})가 필요한 최소 상태"
            f"({required.value})에 도달하지 않았습니다. 이전 단계를 먼저 완료하세요"
            "(docs/HUMAN_IN_THE_LOOP.md §3)."
        )


__all__ = [
    "ensure_hypothesis_approved",
    "ensure_state_at_least",
    "ensure_strategy_approved",
]
