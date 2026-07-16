"""파이프라인 상태 머신 테스트 (docs/HUMAN_IN_THE_LOOP.md §3, 원문 §13).

12-상태 전이표(허용/불허)를 테스트로 고정한다 — H1 DoD 4.
"""

from datetime import datetime

import pytest

from research_backtest.core.exceptions import ApprovalGateError
from research_backtest.core.hitl.states import (
    ALLOWED_REGRESSIONS,
    FORWARD_ORDER,
    PipelineState,
    RunState,
    advance,
    create_run_state,
    generate_run_id,
)

ALL_STATES: list[PipelineState] = list(PipelineState)


def _new_run_state() -> RunState:
    return create_run_state("run-1", "SK하이닉스", "2025-12-31", actor="system")


# ---------------------------------------------------------------------------
# 선언 순서 = 12종 고정
# ---------------------------------------------------------------------------


def test_pipeline_state_declares_twelve_states_in_order() -> None:
    assert [s.value for s in FORWARD_ORDER] == [
        "DATA_READY",
        "CANDIDATE_ANALYSIS_READY",
        "AWAITING_ANALYST_VIEW",
        "ANALYST_VIEW_APPROVED",
        "HYPOTHESIS_DRAFT",
        "HYPOTHESIS_APPROVED",
        "STRATEGY_DRAFT_READY",
        "AWAITING_STRATEGY_REVIEW",
        "STRATEGY_APPROVED",
        "BACKTEST_COMPLETE",
        "AWAITING_INTERPRETATION",
        "COMPLETE",
    ]


def test_create_run_state_starts_at_data_ready_with_initial_transition() -> None:
    run_state = _new_run_state()
    assert run_state.current_state == PipelineState.DATA_READY
    assert len(run_state.transitions) == 1
    initial = run_state.transitions[0]
    assert initial.from_state is None
    assert initial.to_state == PipelineState.DATA_READY


# ---------------------------------------------------------------------------
# 정상 전진 전체 경로
# ---------------------------------------------------------------------------


def test_full_forward_path_data_ready_to_complete() -> None:
    run_state = _new_run_state()
    for state in FORWARD_ORDER[1:]:
        run_state = advance(run_state, state, actor="user")
    assert run_state.current_state == PipelineState.COMPLETE
    # 최초 진입 1건 + 11번의 전진 = 12건의 이력.
    assert len(run_state.transitions) == len(FORWARD_ORDER)
    visited = [t.to_state for t in run_state.transitions]
    assert visited == list(FORWARD_ORDER)


@pytest.mark.parametrize("skip_to_index", [2, 5, 11])
def test_skipping_a_state_is_rejected(skip_to_index: int) -> None:
    run_state = _new_run_state()
    with pytest.raises(ApprovalGateError):
        advance(run_state, FORWARD_ORDER[skip_to_index], actor="user")


def test_advance_does_not_mutate_original_run_state() -> None:
    run_state = _new_run_state()
    original_transition_count = len(run_state.transitions)
    advance(run_state, FORWARD_ORDER[1], actor="user")
    assert len(run_state.transitions) == original_transition_count
    assert run_state.current_state == PipelineState.DATA_READY


# ---------------------------------------------------------------------------
# 허용된 회귀 4종
# ---------------------------------------------------------------------------


def _advance_to(run_state: RunState, target: PipelineState) -> RunState:
    idx = FORWARD_ORDER.index(target)
    for state in FORWARD_ORDER[1 : idx + 1]:
        run_state = advance(run_state, state, actor="user")
    return run_state


def test_allowed_regressions_are_exactly_four_documented_edges() -> None:
    assert ALLOWED_REGRESSIONS == {
        PipelineState.ANALYST_VIEW_APPROVED: PipelineState.AWAITING_ANALYST_VIEW,
        PipelineState.HYPOTHESIS_APPROVED: PipelineState.HYPOTHESIS_DRAFT,
        PipelineState.STRATEGY_APPROVED: PipelineState.AWAITING_STRATEGY_REVIEW,
        PipelineState.COMPLETE: PipelineState.AWAITING_INTERPRETATION,
    }


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (PipelineState.ANALYST_VIEW_APPROVED, PipelineState.AWAITING_ANALYST_VIEW),
        (PipelineState.HYPOTHESIS_APPROVED, PipelineState.HYPOTHESIS_DRAFT),
        (PipelineState.STRATEGY_APPROVED, PipelineState.AWAITING_STRATEGY_REVIEW),
        (PipelineState.COMPLETE, PipelineState.AWAITING_INTERPRETATION),
    ],
)
def test_allowed_regression_succeeds(from_state: PipelineState, to_state: PipelineState) -> None:
    run_state = _advance_to(_new_run_state(), from_state)
    regressed = advance(run_state, to_state, actor="user", note="사용자 수정 요청")
    assert regressed.current_state == to_state
    assert regressed.transitions[-1].note == "사용자 수정 요청"


@pytest.mark.parametrize(
    ("from_state", "to_state"),
    [
        (PipelineState.HYPOTHESIS_DRAFT, PipelineState.DATA_READY),
        (PipelineState.STRATEGY_APPROVED, PipelineState.HYPOTHESIS_DRAFT),
        (PipelineState.COMPLETE, PipelineState.DATA_READY),
        # HYPOTHESIS_APPROVED에는 회귀 에지가 있지만(→HYPOTHESIS_DRAFT), 그보다
        # 더 먼 과거 상태로 건너뛰는 것은 여전히 거부되어야 한다.
        (PipelineState.HYPOTHESIS_APPROVED, PipelineState.AWAITING_ANALYST_VIEW),
    ],
)
def test_undocumented_regression_is_rejected(
    from_state: PipelineState, to_state: PipelineState
) -> None:
    run_state = _advance_to(_new_run_state(), from_state)
    with pytest.raises(ApprovalGateError):
        advance(run_state, to_state, actor="user")


# ---------------------------------------------------------------------------
# auto_approved 기록 + 이력 누적
# ---------------------------------------------------------------------------


def test_auto_approved_flag_is_recorded_on_transition() -> None:
    run_state = _new_run_state()
    run_state = advance(
        run_state,
        FORWARD_ORDER[1],
        actor="test-fixture",
        auto_approved=True,
        note="--auto-approve-for-test",
    )
    last = run_state.transitions[-1]
    assert last.auto_approved is True
    assert last.actor == "test-fixture"
    assert last.note == "--auto-approve-for-test"


def test_auto_approved_defaults_to_false() -> None:
    run_state = advance(_new_run_state(), FORWARD_ORDER[1], actor="user")
    assert run_state.transitions[-1].auto_approved is False


def test_transitions_accumulate_across_multiple_advances() -> None:
    run_state = _new_run_state()
    run_state = advance(run_state, FORWARD_ORDER[1], actor="user")
    run_state = advance(run_state, FORWARD_ORDER[2], actor="user")
    run_state = advance(run_state, FORWARD_ORDER[3], actor="user")
    assert [t.to_state for t in run_state.transitions] == list(FORWARD_ORDER[:4])
    assert [t.from_state for t in run_state.transitions] == [
        None,
        FORWARD_ORDER[0],
        FORWARD_ORDER[1],
        FORWARD_ORDER[2],
    ]


# ---------------------------------------------------------------------------
# generate_run_id
# ---------------------------------------------------------------------------


def test_generate_run_id_format() -> None:
    run_id = generate_run_id("SK하이닉스", datetime(2026, 7, 14, 14, 0, 0))
    assert run_id.startswith("20260714_140000_")
    slug = run_id.removeprefix("20260714_140000_")
    assert slug  # 슬러그가 비어 있지 않다.


def test_generate_run_id_is_deterministic_for_same_input() -> None:
    now = datetime(2026, 7, 14, 9, 30, 0)
    assert generate_run_id("SK하이닉스", now) == generate_run_id("SK하이닉스", now)


def test_generate_run_id_strips_disallowed_characters() -> None:
    run_id = generate_run_id("SK Hynix Inc.", datetime(2026, 7, 14, 14, 0, 0))
    slug = run_id[len("20260714_140000_") :]
    assert " " not in slug
    assert "." not in slug
    assert all(ch.isalnum() or ch == "_" for ch in slug)
    assert not slug.startswith("_")
    assert not slug.endswith("_")


def test_generate_run_id_falls_back_when_slug_would_be_empty() -> None:
    run_id = generate_run_id("   ", datetime(2026, 7, 14, 14, 0, 0))
    assert run_id == "20260714_140000_COMPANY"


def test_generate_run_id_accepts_stock_code() -> None:
    run_id = generate_run_id("000660", datetime(2026, 7, 14, 14, 0, 0))
    assert run_id == "20260714_140000_000660"
