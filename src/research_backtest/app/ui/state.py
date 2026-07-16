"""Streamlit UI 상태 조회 헬퍼 (docs/specs/W3c-report-ui.md §3, S1 소유).

이 모듈은 **조회 전용**이다 — outputs 디렉토리 스캔, 화면별 잠금(lock) 판정,
상태 배지·다음 단계 문구를 계산할 뿐 산출물을 쓰거나 상태를 전이하지 않는다
(쓰기·전이는 :mod:`research_backtest.app.ui.actions`가 core/hitl API를 직접
호출해 수행한다).

``run`` 목록 스캔은 ``app/commands/hitl_flow.py``\\ 의 ``runs``\\ 명령
(docs/specs/CLI-integration.md §5.2 ``list_runs``)과 동일한 규칙을 따른다:
``outputs_dir`` 하위 디렉토리 중 ``run_state.json``\\ 이 있고 검증을 통과하는
것만 run으로 인정한다.

화면 잠금 판정은 ``app/commands/hitl_flow.py``\\ 의 ``_check_allowed_state``\\ /
게이트 호출부(§5.3~§5.7)가 쓰는 허용 상태 집합을 그대로 옮긴 것이다 — CLI가
막는 상태 전이는 이 화면에서도 동일하게 막혀야 한다(게이트 약화 금지,
CLAUDE.md §3-2).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError as PydanticValidationError

from research_backtest.core.config import Settings
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.states import FORWARD_ORDER, PipelineState
from research_backtest.core.hitl.store import RunStore

#: 화면 번호(1~7) — 1804 §15 문면 순서 그대로.
SCREEN_TITLES: tuple[str, ...] = (
    "① 기업·기준일 입력",
    "② AI 분석 후보 검토",
    "③ 분석 관점 작성",
    "④ 투자 가설 작성",
    "⑤ 전략 초안 검토",
    "⑥ 백테스트 결과",
    "⑦ 최종 해석",
)

#: 상태 배지용 한국어 설명 — 순수 표시 문자열이며 게이트 판정에는 쓰이지 않는다.
PIPELINE_STATE_LABELS: dict[PipelineState, str] = {
    PipelineState.DATA_READY: "데이터 준비 완료",
    PipelineState.CANDIDATE_ANALYSIS_READY: "AI 분석 후보 생성됨",
    PipelineState.AWAITING_ANALYST_VIEW: "분석 관점 작성 대기",
    PipelineState.ANALYST_VIEW_APPROVED: "분석 관점 저장됨",
    PipelineState.HYPOTHESIS_DRAFT: "투자 가설 초안",
    PipelineState.HYPOTHESIS_APPROVED: "투자 가설 승인됨",
    PipelineState.STRATEGY_DRAFT_READY: "전략 초안 생성됨",
    PipelineState.AWAITING_STRATEGY_REVIEW: "전략 검토 대기",
    PipelineState.STRATEGY_APPROVED: "전략 승인됨",
    PipelineState.BACKTEST_COMPLETE: "백테스트 완료",
    PipelineState.AWAITING_INTERPRETATION: "결과 해석 대기",
    PipelineState.COMPLETE: "완료",
}

#: 상태별 다음 단계 안내 — hitl_flow.py `_NEXT_STEP_HINTS`(§6.3)와 동일 문구를
#: 이 화면 전용으로 다시 선언한다(해당 모듈은 import 대상이 아니다, §3.1).
NEXT_STEP_HINTS: dict[PipelineState, str] = {
    PipelineState.DATA_READY: "② AI 분석 후보 생성",
    PipelineState.CANDIDATE_ANALYSIS_READY: "③ 분석 관점 작성",
    PipelineState.AWAITING_ANALYST_VIEW: "③ 분석 관점 작성",
    PipelineState.ANALYST_VIEW_APPROVED: "④ 투자 가설 작성",
    PipelineState.HYPOTHESIS_DRAFT: "④ 투자 가설 승인",
    PipelineState.HYPOTHESIS_APPROVED: "⑤ 전략 초안 생성",
    PipelineState.STRATEGY_DRAFT_READY: "⑤ 전략 검토·승인",
    PipelineState.AWAITING_STRATEGY_REVIEW: "⑤ 전략 검토·승인",
    PipelineState.STRATEGY_APPROVED: "⑥ 백테스트 실행",
    PipelineState.BACKTEST_COMPLETE: "⑦ 결과 해석 제출",
    PipelineState.AWAITING_INTERPRETATION: "⑦ 결과 해석 제출",
    PipelineState.COMPLETE: "완료 — 터미널에서 `r2b generate-report` 실행",
}


@dataclass(frozen=True)
class RunSummary:
    """사이드바 run 선택 목록의 항목 1건."""

    run_id: str
    company: str
    as_of_date: str
    current_state: PipelineState
    last_transition_at: str


def scan_runs(settings: Settings) -> list[RunSummary]:
    """``outputs_dir``을 스캔해 run 목록을 만든다.

    hitl_flow.py의 ``list_runs``\\ (§5.2)와 동일 규칙 — ``run_state.json``이
    없거나 검증에 실패하는 디렉토리는 조용히 건너뛴다(무시된 개수는 이 함수의
    관심사가 아니다 — 필요하면 호출부가 ``outputs_dir`` 존재 여부를 별도 확인).
    """
    outputs_dir = settings.outputs_dir
    summaries: list[RunSummary] = []
    if not outputs_dir.exists():
        return summaries
    for run_dir in sorted(p for p in outputs_dir.iterdir() if p.is_dir()):
        if not (run_dir / "run_state.json").exists():
            continue
        try:
            run_state = RunStore(outputs_dir, run_dir.name).load_run_state()
        except (DataValidationError, PydanticValidationError):
            continue
        last_at = run_state.transitions[-1].at if run_state.transitions else "-"
        summaries.append(
            RunSummary(
                run_id=run_state.run_id,
                company=run_state.company,
                as_of_date=run_state.as_of_date,
                current_state=run_state.current_state,
                last_transition_at=last_at,
            )
        )
    return summaries


def _idx(pipeline_state: PipelineState) -> int:
    return FORWARD_ORDER.index(pipeline_state)


class ScreenLockState(StrEnum):
    """화면 하나의 편집 가능 여부 3분류.

    - ``LOCKED``: 아직 이전 단계가 끝나지 않아 진입할 수 없다(사유와 함께 잠금
      표시만 한다).
    - ``READ_ONLY``: 이미 다음 단계로 진행되어(회귀 경로 없음) 더 이상 이
      화면에서 수정할 수 없다 — 저장된 값을 읽기 전용으로 보여준다.
    - ``EDITABLE``: 현재 폼·버튼이 활성화된다.
    """

    LOCKED = "LOCKED"
    READ_ONLY = "READ_ONLY"
    EDITABLE = "EDITABLE"


@dataclass(frozen=True)
class ScreenAvailability:
    """화면 1개의 잠금 판정 결과."""

    state: ScreenLockState
    reason: str | None = None

    @property
    def locked(self) -> bool:
        return self.state is ScreenLockState.LOCKED

    @property
    def read_only(self) -> bool:
        return self.state is ScreenLockState.READ_ONLY


def _editable(reason: str | None = None) -> ScreenAvailability:
    return ScreenAvailability(ScreenLockState.EDITABLE, reason)


def _locked(reason: str) -> ScreenAvailability:
    return ScreenAvailability(ScreenLockState.LOCKED, reason)


def _read_only(reason: str) -> ScreenAvailability:
    return ScreenAvailability(ScreenLockState.READ_ONLY, reason)


def screen2_availability(current: PipelineState) -> ScreenAvailability:
    """AI 분석 후보 검토 — generate-candidates 재생성 허용 집합(hitl_flow §2.2)과 동일.

    ``{DATA_READY, CANDIDATE_ANALYSIS_READY, AWAITING_ANALYST_VIEW}``에서는
    (재)생성 가능, ``ANALYST_VIEW_APPROVED`` 이후는 재생성이 이전 산출물을
    무효화하므로 읽기 전용으로만 노출한다(``_ensure_candidates_stage``).
    """
    if _idx(current) >= _idx(PipelineState.ANALYST_VIEW_APPROVED):
        return _read_only(
            "이미 분석 관점 이후 단계로 진행되어 후보를 재생성할 수 없습니다"
            "(새 run을 만들어 다시 시작하세요). 아래는 저장된 후보를 읽기 전용으로 보여줍니다."
        )
    return _editable()


def screen3_availability(current: PipelineState) -> ScreenAvailability:
    """분석 관점 작성 — create-analyst-view 허용 상태(hitl_flow §5.3)와 동일."""
    if _idx(current) < _idx(PipelineState.AWAITING_ANALYST_VIEW):
        return _locked("AI 분석 후보를 먼저 생성하세요 (화면②).")
    if _idx(current) > _idx(PipelineState.ANALYST_VIEW_APPROVED):
        return _read_only("이후 단계로 진행되어 이 화면은 더 이상 수정할 수 없습니다(읽기 전용).")
    return _editable()


def screen4_availability(current: PipelineState) -> ScreenAvailability:
    """투자 가설 작성·승인 — create-hypothesis 허용 상태(hitl_flow §5.4)와 동일."""
    if _idx(current) < _idx(PipelineState.ANALYST_VIEW_APPROVED):
        return _locked("분석 관점을 먼저 저장하세요 (화면③).")
    if _idx(current) > _idx(PipelineState.HYPOTHESIS_APPROVED):
        return _read_only("이후 단계로 진행되어 이 화면은 더 이상 수정할 수 없습니다(읽기 전용).")
    return _editable()


def screen5_availability(current: PipelineState) -> ScreenAvailability:
    """전략 초안 검토 — generate-strategy-draft·approve-strategy 허용 상태(hitl_flow §5.5)."""
    if _idx(current) < _idx(PipelineState.HYPOTHESIS_APPROVED):
        return _locked("투자 가설을 먼저 승인하세요 (화면④).")
    if _idx(current) > _idx(PipelineState.STRATEGY_APPROVED):
        return _read_only("이후 단계로 진행되어 이 화면은 더 이상 수정할 수 없습니다(읽기 전용).")
    return _editable()


#: 화면⑤ 세부 — 초안 (재)생성이 허용되는 상태 집합(hitl_flow §5.5 그대로).
SCREEN5_DRAFT_STATES: frozenset[PipelineState] = frozenset(
    {
        PipelineState.HYPOTHESIS_APPROVED,
        PipelineState.STRATEGY_DRAFT_READY,
        PipelineState.AWAITING_STRATEGY_REVIEW,
    }
)

#: 화면⑤ 세부 — 승인이 허용되는 상태 집합(hitl_flow §5.5 그대로).
SCREEN5_APPROVE_STATES: frozenset[PipelineState] = frozenset(
    {PipelineState.AWAITING_STRATEGY_REVIEW, PipelineState.STRATEGY_APPROVED}
)


def screen6_availability(current: PipelineState) -> ScreenAvailability:
    """백테스트 결과 — backtest 최소 상태(hitl_flow/backtest_cmd §4.4)와 동일.

    ``COMPLETE``는 잠금이 아니라(결과는 계속 조회 가능) 실행 버튼만 별도로
    비활성화한다 — :func:`screen6_run_blocked_reason` 참고.
    """
    if _idx(current) < _idx(PipelineState.STRATEGY_APPROVED):
        return _locked("전략을 먼저 승인하세요 (화면⑤).")
    return _editable()


def screen6_run_blocked_reason(current: PipelineState) -> str | None:
    """실행 버튼 비활성 사유 — backtest_cmd.py의 COMPLETE 재백테스트 거부와 동일."""
    if current == PipelineState.COMPLETE:
        return "해석까지 완료된 실행은 재백테스트하지 않습니다 — 새 run을 권장합니다."
    return None


def screen7_availability(current: PipelineState) -> ScreenAvailability:
    """최종 해석 — submit-interpretation 허용 상태(hitl_flow §5.6)와 동일."""
    if _idx(current) < _idx(PipelineState.AWAITING_INTERPRETATION):
        return _locked("백테스트를 먼저 실행하세요 (화면⑥).")
    return _editable()


__all__ = [
    "NEXT_STEP_HINTS",
    "PIPELINE_STATE_LABELS",
    "SCREEN5_APPROVE_STATES",
    "SCREEN5_DRAFT_STATES",
    "SCREEN_TITLES",
    "RunSummary",
    "ScreenAvailability",
    "ScreenLockState",
    "scan_runs",
    "screen2_availability",
    "screen3_availability",
    "screen4_availability",
    "screen5_availability",
    "screen6_availability",
    "screen6_run_blocked_reason",
    "screen7_availability",
]
