"""승인 게이트 테스트 (docs/AI_ROLE_BOUNDARY.md §3, 원문 §18).

3종 게이트가 전부 예외로 강제됨을 증명한다 — H1 DoD 3. 역할 분리 테스트(원문
§18) 중 "승인되지 않은 가설이 전략 생성기로 전달되면 실패"·"승인되지 않은
전략이 백테스트로 전달되면 실패"·"사용자 승인 기록 없이 전략이 실행되지
않는지 확인"도 여기 포함한다.
"""

import pytest

from research_backtest.core.exceptions import ApprovalGateError
from research_backtest.core.hitl.gates import (
    ensure_hypothesis_approved,
    ensure_state_at_least,
    ensure_strategy_approved,
)
from research_backtest.core.hitl.models import (
    HumanInvestmentHypothesis,
    HypothesisStatus,
    StrategyModification,
    StrategyReview,
)
from research_backtest.core.hitl.states import PipelineState, advance, create_run_state


def _hypothesis(**overrides: object) -> HumanInvestmentHypothesis:
    payload: dict[str, object] = {
        "hypothesis_id": "HYP-1",
        "view_id": "VIEW-1",
        "author": "홍길동",
        "thesis": "t",
        "economic_rationale": "r",
        "expected_mechanism": "m",
        "selected_variables": ["operating_income_yoy"],
        "expected_direction": "up",
        "investment_horizon_days": 90,
        "evidence_ids": ["EVID-001"],
        "falsification_conditions": ["조건"],
        "limitations": [],
        "status": HypothesisStatus.DRAFT,
        "created_at": "2026-07-14T00:00:00+09:00",
        "updated_at": "2026-07-14T00:00:00+09:00",
    }
    payload.update(overrides)
    return HumanInvestmentHypothesis.model_validate(payload)


def _strategy_review(**overrides: object) -> StrategyReview:
    payload: dict[str, object] = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": {"a": 1},
        "final_strategy": {"a": 2},
        "modifications": [
            StrategyModification(
                field_path="a", draft_value=1, final_value=2, reason="x", modified_by="user"
            )
        ],
        "approval_reason": "ok",
        "approved_by": "user",
        "approved_at": "2026-07-14T00:00:00+09:00",
    }
    payload.update(overrides)
    return StrategyReview.model_validate(payload)


# ---------------------------------------------------------------------------
# ensure_hypothesis_approved
# ---------------------------------------------------------------------------


def test_ensure_hypothesis_approved_rejects_draft_status() -> None:
    with pytest.raises(ApprovalGateError):
        ensure_hypothesis_approved(_hypothesis(status=HypothesisStatus.DRAFT))


def test_ensure_hypothesis_approved_rejects_rejected_status() -> None:
    with pytest.raises(ApprovalGateError):
        ensure_hypothesis_approved(_hypothesis(status=HypothesisStatus.REJECTED))


def test_ensure_hypothesis_approved_passes_when_approved() -> None:
    hypothesis = _hypothesis(
        status=HypothesisStatus.APPROVED,
        approved_by="user",
        approved_at="2026-07-14T00:00:00+09:00",
    )
    ensure_hypothesis_approved(hypothesis)  # 예외 없이 통과해야 한다.


def test_unapproved_hypothesis_cannot_reach_strategy_conversion() -> None:
    """원문 §18: 승인되지 않은 가설이 전략 생성기로 전달되면 실패해야 한다."""

    def convert_to_strategy_draft(hypothesis: HumanInvestmentHypothesis) -> dict[str, object]:
        ensure_hypothesis_approved(hypothesis)
        return {"strategy_name": hypothesis.thesis}

    with pytest.raises(ApprovalGateError):
        convert_to_strategy_draft(_hypothesis(status=HypothesisStatus.DRAFT))


# ---------------------------------------------------------------------------
# ensure_strategy_approved
# ---------------------------------------------------------------------------


def test_ensure_strategy_approved_rejects_none() -> None:
    with pytest.raises(ApprovalGateError):
        ensure_strategy_approved(None)


def test_ensure_strategy_approved_rejects_blank_approved_by() -> None:
    """StrategyReview는 모델 validator가 이미 빈 approved_by를 거부하므로,
    model_construct로 그 우회 경로를 흉내 내 게이트의 방어선을 검사한다."""
    valid = _strategy_review()
    bypassed = StrategyReview.model_construct(**{**valid.model_dump(), "approved_by": "   "})
    with pytest.raises(ApprovalGateError):
        ensure_strategy_approved(bypassed)


def test_ensure_strategy_approved_passes_when_approved() -> None:
    ensure_strategy_approved(_strategy_review())  # 예외 없이 통과해야 한다.


def test_unapproved_strategy_cannot_reach_backtest() -> None:
    """원문 §18: 승인되지 않은 전략이 백테스트로 전달되면 실패해야 하고,
    사용자 승인 기록 없이 전략이 실행되지 않아야 한다."""

    def run_backtest(review: StrategyReview | None) -> str:
        ensure_strategy_approved(review)
        return "backtest-started"

    with pytest.raises(ApprovalGateError):
        run_backtest(None)


# ---------------------------------------------------------------------------
# ensure_state_at_least
# ---------------------------------------------------------------------------


def test_ensure_state_at_least_rejects_when_behind() -> None:
    run_state = create_run_state("r", "X", "2025-01-01", actor="system")
    with pytest.raises(ApprovalGateError):
        ensure_state_at_least(run_state, PipelineState.HYPOTHESIS_APPROVED)


def test_ensure_state_at_least_passes_when_exactly_at_required() -> None:
    run_state = create_run_state("r", "X", "2025-01-01", actor="system")
    ensure_state_at_least(run_state, PipelineState.DATA_READY)  # 예외 없이 통과.


def test_ensure_state_at_least_passes_when_ahead() -> None:
    run_state = create_run_state("r", "X", "2025-01-01", actor="system")
    run_state = advance(run_state, PipelineState.CANDIDATE_ANALYSIS_READY, actor="user")
    run_state = advance(run_state, PipelineState.AWAITING_ANALYST_VIEW, actor="user")
    ensure_state_at_least(run_state, PipelineState.DATA_READY)  # 예외 없이 통과.
