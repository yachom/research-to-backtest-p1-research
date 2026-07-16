"""RunStore 산출물 저장소 테스트 (H1 §6) — save/load 왕복, 안내 메시지, AI/인간 파일 분리."""

import json
from pathlib import Path
from typing import Any

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    Finding,
    HumanInvestmentHypothesis,
    HypothesisCandidate,
    HypothesisStatus,
    StrategyModification,
    StrategyReview,
)
from research_backtest.core.hitl.states import create_run_state
from research_backtest.core.hitl.store import RunStore


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "outputs", "20260714_140000_SKHYNIX")


def _candidate_analysis() -> CandidateAnalysis:
    finding = Finding(
        finding_id="F-1",
        category="financial",
        statement="영업이익 증가",
        evidence_ids=["EVID-001"],
        confidence=0.8,
        source_type="DART",
    )
    return CandidateAnalysis(
        financial_findings=[finding],
        business_findings=[],
        industry_findings=[],
        catalyst_candidates=[],
        risk_candidates=[],
        relationship_candidates=[],
        conflicting_evidence=[],
        missing_information=[],
    )


def _analyst_view() -> AnalystView:
    return AnalystView(
        view_id="VIEW-1",
        author="홍길동",
        research_question="q?",
        core_thesis="thesis",
        selected_evidence_ids=["EVID-001", "EVID-002"],
        rejected_evidence_ids=[],
        evidence_selection_reason="r",
        rejected_evidence_reasons={},
        interpretation="i",
        expected_mechanism="m",
        counterarguments=["c"],
        uncertainties=[],
        created_at="t",
        updated_at="t",
    )


def _hypothesis_candidates() -> list[HypothesisCandidate]:
    return [
        HypothesisCandidate(
            candidate_id="CAND-1",
            title="t",
            rationale="r",
            measurable_variables=["operating_income_yoy"],
            evidence_ids=["EVID-001"],
            counter_evidence_ids=[],
            limitations=[],
            generated_by="llm",
            prompt_version="v1",
        )
    ]


def _human_hypothesis() -> HumanInvestmentHypothesis:
    return HumanInvestmentHypothesis(
        hypothesis_id="HYP-1",
        view_id="VIEW-1",
        author="홍길동",
        thesis="t",
        economic_rationale="r",
        expected_mechanism="m",
        selected_variables=["operating_income_yoy"],
        expected_direction="up",
        investment_horizon_days=90,
        evidence_ids=["EVID-001"],
        falsification_conditions=["조건"],
        limitations=[],
        status=HypothesisStatus.DRAFT,
        created_at="t",
        updated_at="t",
    )


def _strategy_review() -> StrategyReview:
    return StrategyReview(
        review_id="REVIEW-1",
        hypothesis_id="HYP-1",
        llm_draft_strategy={"a": 1},
        final_strategy={"a": 2},
        modifications=[
            StrategyModification(
                field_path="a", draft_value=1, final_value=2, reason="x", modified_by="user"
            )
        ],
        approval_reason="ok",
        approved_by="user",
        approved_at="t",
    )


def _backtest_interpretation() -> BacktestInterpretation:
    return BacktestInterpretation(
        interpretation_id="INTERP-1",
        hypothesis_id="HYP-1",
        strategy_id="STRAT-1",
        author="홍길동",
        main_findings="findings",
        supporting_results=["a"],
        contradicting_results=[],
        regime_dependence=None,
        limitations=[],
        hypothesis_decision="SUPPORTED",
        decision_reason="reason",
        revised_hypothesis=None,
        followup_tests=[],
        created_at="t",
    )


# ---------------------------------------------------------------------------
# save/load 왕복 (8종)
# ---------------------------------------------------------------------------


def test_run_state_round_trip(store: RunStore) -> None:
    run_state = create_run_state("run-1", "SK하이닉스", "2025-12-31", actor="system")
    path = store.save_run_state(run_state)
    assert path.name == "run_state.json"
    assert store.load_run_state() == run_state


def test_candidate_analysis_round_trip(store: RunStore) -> None:
    analysis = _candidate_analysis()
    store.save_candidate_analysis(analysis)
    assert store.load_candidate_analysis() == analysis


def test_analyst_view_round_trip(store: RunStore) -> None:
    view = _analyst_view()
    store.save_analyst_view(view)
    assert store.load_analyst_view() == view


def test_hypothesis_candidates_round_trip(store: RunStore) -> None:
    candidates = _hypothesis_candidates()
    store.save_hypothesis_candidates(candidates)
    assert store.load_hypothesis_candidates() == candidates


def test_human_hypothesis_round_trip(store: RunStore) -> None:
    hypothesis = _human_hypothesis()
    store.save_human_hypothesis(hypothesis)
    assert store.load_human_hypothesis() == hypothesis


def test_strategy_draft_round_trip(store: RunStore) -> None:
    draft = {
        "strategy_name": "demo",
        "entry": {"all": [{"left": "x", "operator": ">", "right": 1}]},
    }
    store.save_strategy_draft(draft)
    assert store.load_strategy_draft() == draft


def test_strategy_review_round_trip(store: RunStore) -> None:
    review = _strategy_review()
    store.save_strategy_review(review)
    assert store.load_strategy_review() == review


def test_backtest_interpretation_round_trip(store: RunStore) -> None:
    interpretation = _backtest_interpretation()
    store.save_backtest_interpretation(interpretation)
    assert store.load_backtest_interpretation() == interpretation


# ---------------------------------------------------------------------------
# 미존재 load 안내 메시지
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("loader_name", "hint_substring"),
    [
        ("load_run_state", "create_run_state"),
        ("load_candidate_analysis", "generate-candidates"),
        ("load_analyst_view", "create-analyst-view"),
        ("load_hypothesis_candidates", "가설 후보"),
        ("load_human_hypothesis", "create-hypothesis"),
        ("load_strategy_draft", "generate-strategy-draft"),
        ("load_strategy_review", "approve-strategy"),
        ("load_backtest_interpretation", "submit-interpretation"),
    ],
)
def test_missing_artifact_raises_data_validation_error_with_guidance(
    store: RunStore, loader_name: str, hint_substring: str
) -> None:
    loader = getattr(store, loader_name)
    with pytest.raises(DataValidationError, match=hint_substring):
        loader()


def test_missing_artifact_is_not_a_bare_file_not_found_error(store: RunStore) -> None:
    with pytest.raises(DataValidationError):
        try:
            store.load_run_state()
        except FileNotFoundError:
            pytest.fail("FileNotFoundError가 노출되면 안 된다 — DataValidationError여야 한다")


# ---------------------------------------------------------------------------
# AI 후보 vs 인간 가설 — 파일 분리
# ---------------------------------------------------------------------------


def test_ai_candidates_and_human_hypothesis_are_stored_in_separate_files(store: RunStore) -> None:
    store.save_hypothesis_candidates(_hypothesis_candidates())
    store.save_human_hypothesis(_human_hypothesis())

    candidates_path = store.run_dir / "hypothesis_candidates.json"
    hypothesis_path = store.run_dir / "human_investment_hypothesis.json"

    assert candidates_path != hypothesis_path
    assert candidates_path.exists()
    assert hypothesis_path.exists()

    candidates_raw = json.loads(candidates_path.read_text(encoding="utf-8"))
    hypothesis_raw = json.loads(hypothesis_path.read_text(encoding="utf-8"))

    # AI 후보 파일은 list[HypothesisCandidate] — 승인 관련 필드가 없다.
    assert isinstance(candidates_raw, list)
    assert "status" not in candidates_raw[0]
    assert "approved_by" not in candidates_raw[0]

    # 인간 가설 파일은 단일 객체이며 승인 관련 필드를 갖는다.
    assert isinstance(hypothesis_raw, dict)
    assert "status" in hypothesis_raw
    assert "approved_by" in hypothesis_raw


def test_loading_hypothesis_candidates_never_returns_human_hypothesis_type(
    store: RunStore,
) -> None:
    store.save_hypothesis_candidates(_hypothesis_candidates())
    candidates = store.load_hypothesis_candidates()
    assert all(isinstance(c, HypothesisCandidate) for c in candidates)
    assert all(not isinstance(c, HumanInvestmentHypothesis) for c in candidates)


# ---------------------------------------------------------------------------
# ai_usage_log.jsonl append
# ---------------------------------------------------------------------------


def _usage_record(**overrides: Any) -> AIUsageRecord:
    payload: dict[str, Any] = {
        "usage_id": "U-1",
        "stage": "candidate_analysis",
        "model": "inclusionai/ling-2.6-flash:free",
        "prompt_name": "candidate_analysis",
        "prompt_version": "v1",
        "input_artifact_ids": [],
        "output_artifact_ids": ["candidate_analysis.json"],
        "ai_role": "후보 정리",
        "human_review_required": True,
        "human_changes_summary": None,
        "created_at": "t",
    }
    payload.update(overrides)
    return AIUsageRecord.model_validate(payload)


def test_append_ai_usage_creates_one_line_per_record(store: RunStore) -> None:
    store.append_ai_usage(_usage_record(usage_id="U-1"))
    store.append_ai_usage(_usage_record(usage_id="U-2", stage="strategy_draft"))

    log_path = store.run_dir / "ai_usage_log.jsonl"
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    for line in lines:
        parsed = json.loads(line)  # 각 줄이 단독으로 유효한 JSON이어야 한다(JSONL).
        assert "usage_id" in parsed


def test_load_ai_usage_log_round_trip(store: RunStore) -> None:
    store.append_ai_usage(_usage_record(usage_id="U-1"))
    store.append_ai_usage(_usage_record(usage_id="U-2", stage="strategy_draft"))
    records = store.load_ai_usage_log()
    assert [r.usage_id for r in records] == ["U-1", "U-2"]
    assert records[1].stage == "strategy_draft"


def test_load_ai_usage_log_returns_empty_list_when_absent(store: RunStore) -> None:
    assert store.load_ai_usage_log() == []


def test_ai_usage_log_preserves_korean_text_readably(store: RunStore) -> None:
    store.append_ai_usage(_usage_record(ai_role="재무·산업 후보와 상충 근거 정리"))
    raw = (store.run_dir / "ai_usage_log.jsonl").read_text(encoding="utf-8")
    assert "재무·산업 후보와 상충 근거 정리" in raw  # ensure_ascii=False


# ---------------------------------------------------------------------------
# 경로 규약
# ---------------------------------------------------------------------------


def test_run_dir_matches_outputs_run_id_convention(tmp_path: Path) -> None:
    store = RunStore(tmp_path / "outputs", "20260714_140000_SKHYNIX")
    assert store.run_dir == tmp_path / "outputs" / "20260714_140000_SKHYNIX"


def test_saved_json_is_pretty_printed_with_indent(store: RunStore) -> None:
    store.save_analyst_view(_analyst_view())
    raw = (store.run_dir / "analyst_view.json").read_text(encoding="utf-8")
    assert "\n  " in raw  # indent=2
