"""docs/OUTPUT_SCHEMA.md §1~§8 모델 테스트 (원문 §18 "모델 테스트" 전부 포함).

7종 모델(CandidateAnalysis·AnalystView·HumanInvestmentHypothesis·
HypothesisCandidate·StrategyReview·BacktestInterpretation·AIUsageRecord) 각각에
대해 JSON round-trip과 핵심 검증 규칙을 검사하고, AuthoredContent/ContentOrigin도
함께 다룬다. 역할 분리 테스트 중 모델 계층에 속하는 항목(CandidateAnalysis 필드
고정, 미승인 가설 자동 승인 불가)도 여기 포함한다 — 나머지 2종(승인 게이트)은
test_hitl_gates.py에 있다.
"""

from typing import Any

import pytest
from pydantic import ValidationError

from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    AuthoredContent,
    BacktestInterpretation,
    CandidateAnalysis,
    ContentOrigin,
    Finding,
    HumanInvestmentHypothesis,
    HypothesisCandidate,
    HypothesisStatus,
    RelationshipCandidate,
    StrategyModification,
    StrategyReview,
    now_kst_iso,
)

# ---------------------------------------------------------------------------
# 공용 팩토리 — 각 모델의 "유효한" 최소 인스턴스
# ---------------------------------------------------------------------------


def _finding(**overrides: Any) -> Finding:
    payload: dict[str, Any] = {
        "finding_id": "F-1",
        "category": "financial",
        "statement": "영업이익이 전년 대비 증가했다.",
        "evidence_ids": ["EVID-001"],
        "confidence": 0.8,
        "source_type": "DART",
        "limitations": [],
    }
    payload.update(overrides)
    return Finding.model_validate(payload)


def _relationship_candidate(**overrides: Any) -> RelationshipCandidate:
    payload: dict[str, Any] = {
        "relationship_id": "R-1",
        "cause_or_signal": "HBM 매출 비중 증가",
        "outcome": "영업이익률 개선",
        "proposed_mechanism": "ASP 상승",
        "evidence_ids": ["EVID-001"],
        "counter_evidence_ids": [],
        "measurable_variables": ["operating_income_yoy"],
        "confidence": 0.7,
    }
    payload.update(overrides)
    return RelationshipCandidate.model_validate(payload)


def _candidate_analysis(**overrides: Any) -> CandidateAnalysis:
    payload: dict[str, Any] = {
        "financial_findings": [_finding()],
        "business_findings": [],
        "industry_findings": [],
        "catalyst_candidates": [],
        "risk_candidates": [],
        "relationship_candidates": [_relationship_candidate()],
        "conflicting_evidence": [],
        "missing_information": ["2026년 CAPEX 세부 계획 미공개"],
    }
    payload.update(overrides)
    return CandidateAnalysis.model_validate(payload)


def _analyst_view(**overrides: Any) -> AnalystView:
    payload: dict[str, Any] = {
        "view_id": "VIEW-1",
        "author": "홍길동",
        "research_question": "실적 회복이 주가에 이미 반영되었는가?",
        "core_thesis": "서프라이즈 여부가 관건이다.",
        "selected_evidence_ids": ["EVID-001", "EVID-002"],
        "rejected_evidence_ids": ["EVID-003"],
        "evidence_selection_reason": "1차 공시 자료를 우선했다.",
        "rejected_evidence_reasons": {"EVID-003": "결과 변수와 중복"},
        "interpretation": "HBM 비중 확대가 핵심이다.",
        "expected_mechanism": "ASP 상승 → 이익률 개선",
        "counterarguments": ["이미 선반영되었을 수 있다."],
        "uncertainties": ["CAPEX 계획 불확실"],
        "created_at": now_kst_iso(),
        "updated_at": now_kst_iso(),
    }
    payload.update(overrides)
    return AnalystView.model_validate(payload)


def _hypothesis(**overrides: Any) -> HumanInvestmentHypothesis:
    payload: dict[str, Any] = {
        "hypothesis_id": "HYP-1",
        "view_id": "VIEW-1",
        "author": "홍길동",
        "thesis": "HBM 비중 확대가 이익률을 끌어올린다.",
        "economic_rationale": "HBM 마진이 더 높다.",
        "expected_mechanism": "ASP 상승 → 이익률 개선",
        "selected_variables": ["operating_income_yoy"],
        "expected_direction": "up",
        "investment_horizon_days": 90,
        "evidence_ids": ["EVID-001"],
        "falsification_conditions": ["2개 분기 연속 컨센서스 하회 시 기각"],
        "limitations": ["매크로 변수에 민감"],
        "status": HypothesisStatus.DRAFT,
        "created_at": now_kst_iso(),
        "updated_at": now_kst_iso(),
    }
    payload.update(overrides)
    return HumanInvestmentHypothesis.model_validate(payload)


def _hypothesis_candidate(**overrides: Any) -> HypothesisCandidate:
    payload: dict[str, Any] = {
        "candidate_id": "CAND-1",
        "title": "HBM 비중과 이익률의 관계",
        "rationale": "ASP 상승이 이익률을 견인한다.",
        "measurable_variables": ["operating_income_yoy"],
        "evidence_ids": ["EVID-001"],
        "counter_evidence_ids": [],
        "limitations": [],
        "generated_by": "llm:inclusionai/ling-2.6-flash:free",
        "prompt_version": "hypothesis_candidate_v1",
    }
    payload.update(overrides)
    return HypothesisCandidate.model_validate(payload)


def _strategy_modification(**overrides: Any) -> StrategyModification:
    payload: dict[str, Any] = {
        "field_path": "entry.operating_income_yoy.right",
        "draft_value": 0.1,
        "final_value": 0.2,
        "reason": "통상 실적 변동과 구분하기 위해 기준을 상향했다.",
        "modified_by": "user",
    }
    payload.update(overrides)
    return StrategyModification.model_validate(payload)


def _strategy_review(**overrides: Any) -> StrategyReview:
    payload: dict[str, Any] = {
        "review_id": "REVIEW-1",
        "hypothesis_id": "HYP-1",
        "llm_draft_strategy": {"entry": {"operating_income_yoy": {"right": 0.1}}},
        "final_strategy": {"entry": {"operating_income_yoy": {"right": 0.2}}},
        "modifications": [_strategy_modification()],
        "approval_reason": "임계값을 보수적으로 조정해 승인한다.",
        "approved_by": "user",
        "approved_at": now_kst_iso(),
    }
    payload.update(overrides)
    return StrategyReview.model_validate(payload)


def _backtest_interpretation(**overrides: Any) -> BacktestInterpretation:
    payload: dict[str, Any] = {
        "interpretation_id": "INTERP-1",
        "hypothesis_id": "HYP-1",
        "strategy_id": "STRAT-1",
        "author": "홍길동",
        "main_findings": "전략은 벤치마크를 소폭 상회했다.",
        "supporting_results": ["초과수익률 +3.2%p"],
        "contradicting_results": [],
        "regime_dependence": None,
        "limitations": ["표본 기간이 짧다."],
        "hypothesis_decision": "SUPPORTED",
        "decision_reason": "가설이 제시한 방향과 부합했다.",
        "revised_hypothesis": None,
        "followup_tests": ["다른 반도체 기업으로 표본 확장"],
        "created_at": now_kst_iso(),
    }
    payload.update(overrides)
    return BacktestInterpretation.model_validate(payload)


def _ai_usage_record(**overrides: Any) -> AIUsageRecord:
    payload: dict[str, Any] = {
        "usage_id": "USAGE-1",
        "stage": "candidate_analysis",
        "model": "inclusionai/ling-2.6-flash:free",
        "prompt_name": "candidate_analysis",
        "prompt_version": "v1",
        "input_artifact_ids": ["evidence_manifest.json"],
        "output_artifact_ids": ["candidate_analysis.json"],
        "ai_role": "재무·산업 후보와 상충 근거 정리",
        "human_review_required": True,
        "human_changes_summary": None,
        "created_at": now_kst_iso(),
    }
    payload.update(overrides)
    return AIUsageRecord.model_validate(payload)


# ---------------------------------------------------------------------------
# 1. CandidateAnalysis (+ Finding · RelationshipCandidate)
# ---------------------------------------------------------------------------


def test_candidate_analysis_round_trip() -> None:
    analysis = _candidate_analysis()
    restored = CandidateAnalysis.model_validate_json(analysis.model_dump_json())
    assert restored == analysis


def test_candidate_analysis_rejects_extra_field() -> None:
    payload = _candidate_analysis().model_dump(mode="json")
    payload["final_investment_opinion"] = "매수"
    with pytest.raises(ValidationError):
        CandidateAnalysis.model_validate(payload)


def test_candidate_analysis_field_set_has_no_final_opinion() -> None:
    """역할 분리(원문 §18): CandidateAnalysis에는 최종 투자 의견 필드가 없다."""
    assert set(CandidateAnalysis.model_fields) == {
        "financial_findings",
        "business_findings",
        "industry_findings",
        "catalyst_candidates",
        "risk_candidates",
        "relationship_candidates",
        "conflicting_evidence",
        "missing_information",
    }
    forbidden_terms = ("opinion", "recommendation", "final_view", "investment_decision")
    for field_name in CandidateAnalysis.model_fields:
        for term in forbidden_terms:
            assert term not in field_name


def test_finding_requires_at_least_one_evidence_id() -> None:
    with pytest.raises(ValidationError):
        _finding(evidence_ids=[])


def test_finding_confidence_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        _finding(confidence=1.5)


def test_relationship_candidate_defaults() -> None:
    rc = RelationshipCandidate.model_validate(
        {
            "relationship_id": "R-2",
            "cause_or_signal": "x",
            "outcome": "y",
            "proposed_mechanism": "z",
            "evidence_ids": ["EVID-001"],
            "confidence": 0.5,
        }
    )
    assert rc.counter_evidence_ids == []
    assert rc.measurable_variables == []


# ---------------------------------------------------------------------------
# 2. AnalystView
# ---------------------------------------------------------------------------


def test_analyst_view_round_trip() -> None:
    view = _analyst_view()
    restored = AnalystView.model_validate_json(view.model_dump_json())
    assert restored == view


def test_analyst_view_rejects_extra_field() -> None:
    payload = _analyst_view().model_dump(mode="json")
    payload["ai_score"] = 0.9
    with pytest.raises(ValidationError):
        AnalystView.model_validate(payload)


def test_analyst_view_blank_research_question_rejected() -> None:
    with pytest.raises(ValidationError):
        _analyst_view(research_question="   ")


def test_analyst_view_blank_core_thesis_rejected() -> None:
    with pytest.raises(ValidationError):
        _analyst_view(core_thesis="")


def test_analyst_view_requires_at_least_two_selected_evidence() -> None:
    with pytest.raises(ValidationError):
        _analyst_view(selected_evidence_ids=["EVID-001"])


def test_analyst_view_requires_at_least_one_counterargument() -> None:
    with pytest.raises(ValidationError):
        _analyst_view(counterarguments=[])


def test_analyst_view_rejects_selected_rejected_overlap() -> None:
    with pytest.raises(ValidationError, match="EVID-002"):
        _analyst_view(
            selected_evidence_ids=["EVID-001", "EVID-002"],
            rejected_evidence_ids=["EVID-002", "EVID-003"],
        )


@pytest.mark.parametrize(
    "key",
    [
        "blank_research_question",
        "blank_core_thesis",
        "insufficient_selected_evidence",
        "no_counterarguments",
        "overlapping_selected_rejected",
    ],
)
def test_analyst_view_fixture_violations_rejected(
    key: str, analyst_view_violations: dict[str, dict[str, Any]]
) -> None:
    """analyst_view_violations.json의 모델 레벨 위반 5종은 전부 ValidationError."""
    with pytest.raises(ValidationError):
        AnalystView.model_validate(analyst_view_violations[key])


def test_analyst_view_fixture_valid_passes(analyst_view_valid_payload: dict[str, Any]) -> None:
    view = AnalystView.model_validate(analyst_view_valid_payload)
    assert view.selected_evidence_ids == ["EVID-001", "EVID-002"]


# ---------------------------------------------------------------------------
# 3. HumanInvestmentHypothesis
# ---------------------------------------------------------------------------


def test_hypothesis_round_trip() -> None:
    hypothesis = _hypothesis()
    restored = HumanInvestmentHypothesis.model_validate_json(hypothesis.model_dump_json())
    assert restored == hypothesis


def test_hypothesis_rejects_extra_field() -> None:
    payload = _hypothesis().model_dump(mode="json")
    payload["ai_confidence"] = 0.99
    with pytest.raises(ValidationError):
        HumanInvestmentHypothesis.model_validate(payload)


def test_hypothesis_requires_at_least_one_falsification_condition() -> None:
    with pytest.raises(ValidationError):
        _hypothesis(falsification_conditions=[])


def test_hypothesis_approved_status_requires_approved_by_and_at() -> None:
    with pytest.raises(ValidationError, match="approved_by"):
        _hypothesis(status=HypothesisStatus.APPROVED, approved_by=None, approved_at=None)


def test_hypothesis_approved_status_rejects_blank_approver() -> None:
    with pytest.raises(ValidationError):
        _hypothesis(
            status=HypothesisStatus.APPROVED,
            approved_by="   ",
            approved_at=now_kst_iso(),
        )


def test_hypothesis_approved_with_approver_succeeds() -> None:
    hypothesis = _hypothesis(
        status=HypothesisStatus.APPROVED,
        approved_by="user",
        approved_at=now_kst_iso(),
    )
    assert hypothesis.status == HypothesisStatus.APPROVED


def test_hypothesis_content_origin_restricted_to_two_values() -> None:
    with pytest.raises(ValidationError):
        _hypothesis(content_origin="AI_CANDIDATE")
    ai_draft = _hypothesis(content_origin="AI_DRAFT_HUMAN_APPROVED")
    assert ai_draft.content_origin == "AI_DRAFT_HUMAN_APPROVED"


def test_hypothesis_status_allows_seven_values() -> None:
    assert {member.value for member in HypothesisStatus} == {
        "DRAFT",
        "APPROVED",
        "TESTED",
        "SUPPORTED",
        "PARTIALLY_SUPPORTED",
        "REJECTED",
        "REVISED",
    }


@pytest.mark.parametrize(
    "key",
    [
        "no_falsification_conditions",
        "approved_without_approver",
        "invalid_content_origin",
    ],
)
def test_hypothesis_fixture_model_violations_rejected(
    key: str, hypothesis_violations: dict[str, dict[str, Any]]
) -> None:
    with pytest.raises(ValidationError):
        HumanInvestmentHypothesis.model_validate(hypothesis_violations[key])


def test_hypothesis_fixture_valid_passes(hypothesis_valid_payload: dict[str, Any]) -> None:
    hypothesis = HumanInvestmentHypothesis.model_validate(hypothesis_valid_payload)
    assert hypothesis.status == HypothesisStatus.DRAFT


def test_ai_output_alone_cannot_auto_approve_hypothesis() -> None:
    """역할 분리(원문 §18): AI 출력만으로 HumanInvestmentHypothesis가 자동 승인되지 않는다.

    AI가 만드는 산출물은 HypothesisCandidate뿐이고, 그 필드로는
    HumanInvestmentHypothesis를 구성할 수 없다. 설령 AI가 만든 값들을 그대로
    가져와 status=APPROVED로 구성하려 해도 approved_by·approved_at 없이는
    모델 생성 자체가 거부된다 — "자동 승인"이 구조적으로 불가능하다.
    """
    candidate = _hypothesis_candidate(generated_by="llm")
    with pytest.raises(ValidationError):
        HumanInvestmentHypothesis.model_validate(
            {
                "hypothesis_id": candidate.candidate_id,
                "view_id": "VIEW-1",
                "author": candidate.generated_by,
                "thesis": candidate.title,
                "economic_rationale": candidate.rationale,
                "expected_mechanism": candidate.rationale,
                "selected_variables": candidate.measurable_variables,
                "expected_direction": "up",
                "investment_horizon_days": 90,
                "evidence_ids": candidate.evidence_ids,
                "falsification_conditions": ["조건"],
                "limitations": candidate.limitations,
                "status": HypothesisStatus.APPROVED,
                "created_at": now_kst_iso(),
                "updated_at": now_kst_iso(),
                # approved_by/approved_at을 의도적으로 채우지 않는다 — AI는 이 값을
                # 채울 권한이 없고, 사람이 validation.approve_hypothesis를 호출해야 한다.
            }
        )


# ---------------------------------------------------------------------------
# 4. HypothesisCandidate
# ---------------------------------------------------------------------------


def test_hypothesis_candidate_round_trip() -> None:
    candidate = _hypothesis_candidate()
    restored = HypothesisCandidate.model_validate_json(candidate.model_dump_json())
    assert restored == candidate


def test_hypothesis_candidate_rejects_extra_field() -> None:
    payload = _hypothesis_candidate().model_dump(mode="json")
    payload["status"] = "APPROVED"
    with pytest.raises(ValidationError):
        HypothesisCandidate.model_validate(payload)


def test_hypothesis_candidate_has_no_approval_fields() -> None:
    """HypothesisCandidate는 승인 가설이 아니므로 status·approved_by 필드가 없다."""
    assert "status" not in HypothesisCandidate.model_fields
    assert "approved_by" not in HypothesisCandidate.model_fields
    assert "approved_at" not in HypothesisCandidate.model_fields


# ---------------------------------------------------------------------------
# 5. StrategyReview (+ StrategyModification)
# ---------------------------------------------------------------------------


def test_strategy_review_round_trip() -> None:
    review = _strategy_review()
    restored = StrategyReview.model_validate_json(review.model_dump_json())
    assert restored == review


def test_strategy_review_rejects_extra_field() -> None:
    payload = _strategy_review().model_dump(mode="json")
    payload["auto_approved"] = True
    with pytest.raises(ValidationError):
        StrategyReview.model_validate(payload)


def test_strategy_review_requires_nonblank_approved_by() -> None:
    with pytest.raises(ValidationError):
        _strategy_review(approved_by="")


def test_strategy_modification_requires_nonblank_modified_by() -> None:
    with pytest.raises(ValidationError):
        _strategy_modification(modified_by="")


def test_strategy_review_modification_history_is_stored() -> None:
    """역할 분리(원문 §18): AI draft와 final strategy의 수정 이력이 저장된다."""
    review = _strategy_review()
    assert review.llm_draft_strategy != review.final_strategy
    assert len(review.modifications) == 1
    mod = review.modifications[0]
    assert mod.draft_value == 0.1
    assert mod.final_value == 0.2
    assert mod.modified_by == "user"


def test_strategy_modification_allows_none_for_addition_or_deletion() -> None:
    added = _strategy_modification(
        field_path="execution.stop_loss", draft_value=None, final_value=-0.1
    )
    removed = _strategy_modification(
        field_path="entry.legacy_rule", draft_value="x", final_value=None
    )
    assert added.draft_value is None
    assert removed.final_value is None


# ---------------------------------------------------------------------------
# 6. BacktestInterpretation
# ---------------------------------------------------------------------------


def test_backtest_interpretation_round_trip() -> None:
    interpretation = _backtest_interpretation()
    restored = BacktestInterpretation.model_validate_json(interpretation.model_dump_json())
    assert restored == interpretation


def test_backtest_interpretation_rejects_extra_field() -> None:
    payload = _backtest_interpretation().model_dump(mode="json")
    payload["confidence"] = 0.9
    with pytest.raises(ValidationError):
        BacktestInterpretation.model_validate(payload)


def test_backtest_interpretation_requires_nonblank_decision_reason() -> None:
    with pytest.raises(ValidationError):
        _backtest_interpretation(decision_reason="  ")


def test_backtest_interpretation_requires_supporting_or_contradicting() -> None:
    with pytest.raises(ValidationError):
        _backtest_interpretation(supporting_results=[], contradicting_results=[])


def test_backtest_interpretation_supporting_only_is_enough() -> None:
    interpretation = _backtest_interpretation(supporting_results=["a"], contradicting_results=[])
    assert interpretation.supporting_results == ["a"]


def test_backtest_interpretation_contradicting_only_is_enough() -> None:
    interpretation = _backtest_interpretation(supporting_results=[], contradicting_results=["b"])
    assert interpretation.contradicting_results == ["b"]


def test_backtest_interpretation_revised_requires_revised_hypothesis() -> None:
    with pytest.raises(ValidationError, match="REVISED"):
        _backtest_interpretation(hypothesis_decision="REVISED", revised_hypothesis=None)


def test_backtest_interpretation_revised_with_hypothesis_succeeds() -> None:
    interpretation = _backtest_interpretation(
        hypothesis_decision="REVISED",
        revised_hypothesis="보유기간을 120일로 늘려 재검증한다.",
    )
    assert interpretation.hypothesis_decision == "REVISED"


def test_backtest_interpretation_rejects_unknown_decision() -> None:
    with pytest.raises(ValidationError):
        _backtest_interpretation(hypothesis_decision="MAYBE")


def test_backtest_interpretation_content_origin_restricted() -> None:
    with pytest.raises(ValidationError):
        _backtest_interpretation(content_origin="AI_CANDIDATE")


# ---------------------------------------------------------------------------
# 7. AIUsageRecord
# ---------------------------------------------------------------------------


def test_ai_usage_record_round_trip() -> None:
    record = _ai_usage_record()
    restored = AIUsageRecord.model_validate_json(record.model_dump_json())
    assert restored == record


def test_ai_usage_record_rejects_extra_field() -> None:
    payload = _ai_usage_record().model_dump(mode="json")
    payload["cost_usd"] = 0.0
    with pytest.raises(ValidationError):
        AIUsageRecord.model_validate(payload)


def test_ai_usage_record_human_changes_summary_optional() -> None:
    record = _ai_usage_record(human_changes_summary="사용자가 임계값을 상향 조정함.")
    assert record.human_changes_summary is not None


# ---------------------------------------------------------------------------
# 8. AuthoredContent · ContentOrigin (원문 §16)
# ---------------------------------------------------------------------------


def test_content_origin_has_seven_values() -> None:
    assert {member.value for member in ContentOrigin} == {
        "SOURCE_FACT",
        "PYTHON_CALCULATION",
        "AI_CANDIDATE",
        "HUMAN_ANALYSIS",
        "HUMAN_HYPOTHESIS",
        "AI_DRAFT_HUMAN_APPROVED",
        "HUMAN_INTERPRETATION",
    }


def test_authored_content_round_trip() -> None:
    content = AuthoredContent(
        content="HBM 매출 비중이 전년 대비 확대되었다.",
        content_origin=ContentOrigin.SOURCE_FACT,
        author=None,
        source_ids=["EVID-001"],
        ai_usage_id=None,
    )
    restored = AuthoredContent.model_validate_json(content.model_dump_json())
    assert restored == content


def test_authored_content_rejects_invalid_origin() -> None:
    with pytest.raises(ValidationError):
        AuthoredContent.model_validate(
            {
                "content": "x",
                "content_origin": "NOT_A_REAL_ORIGIN",
                "author": None,
                "source_ids": [],
                "ai_usage_id": None,
            }
        )


def test_authored_content_rejects_extra_field() -> None:
    with pytest.raises(ValidationError):
        AuthoredContent.model_validate(
            {
                "content": "x",
                "content_origin": "SOURCE_FACT",
                "author": None,
                "source_ids": [],
                "ai_usage_id": None,
                "confidence": 0.5,
            }
        )
