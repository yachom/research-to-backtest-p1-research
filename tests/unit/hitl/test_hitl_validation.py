"""외부 참조 검증 테스트 (H1 §5) — Evidence Store 실존, 변수 지원 여부, 가설 승인."""

from pathlib import Path
from typing import Any

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    AnalystView,
    HumanInvestmentHypothesis,
    HypothesisStatus,
)
from research_backtest.core.hitl.validation import (
    FileEvidenceStore,
    approve_hypothesis,
    validate_analyst_view,
    validate_hypothesis,
)

# ---------------------------------------------------------------------------
# FileEvidenceStore
# ---------------------------------------------------------------------------


def test_file_evidence_store_loads_manifest(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    assert store.has_evidence("EVID-001")
    assert store.has_evidence("EVID-002")
    assert not store.has_evidence("EVID-999")


def test_file_evidence_store_missing_manifest_raises_with_guidance(tmp_path: Path) -> None:
    missing = tmp_path / "no_such_manifest.json"
    with pytest.raises(DataValidationError, match="generate-candidates"):
        FileEvidenceStore.from_manifest(missing)


def test_file_evidence_store_malformed_manifest_raises(tmp_path: Path) -> None:
    bad = tmp_path / "evidence_manifest.json"
    bad.write_text('{"not_evidence": []}', encoding="utf-8")
    with pytest.raises(DataValidationError):
        FileEvidenceStore.from_manifest(bad)


# ---------------------------------------------------------------------------
# validate_analyst_view
# ---------------------------------------------------------------------------


def test_validate_analyst_view_passes_for_valid_view(
    analyst_view_valid_payload: dict[str, Any], evidence_manifest_path: Path
) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    view = AnalystView.model_validate(analyst_view_valid_payload)
    validate_analyst_view(view, store)  # 예외 없이 통과해야 한다.


def test_validate_analyst_view_detects_missing_evidence(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    view = AnalystView.model_construct(
        view_id="V1",
        author="u",
        research_question="q",
        core_thesis="t",
        selected_evidence_ids=["EVID-001", "EVID-777", "EVID-888"],
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
    with pytest.raises(DataValidationError) as exc_info:
        validate_analyst_view(view, store)
    message = str(exc_info.value)
    assert "EVID-777" in message
    assert "EVID-888" in message


def test_validate_analyst_view_reraises_model_constraint_violation_via_model_construct(
    evidence_manifest_path: Path,
) -> None:
    """model_construct로 pydantic validator를 우회한 인스턴스도 재검증에서 잡힌다."""
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    view = AnalystView.model_construct(
        view_id="V1",
        author="u",
        research_question="",  # 원래는 pydantic이 거부하지만 model_construct는 우회한다.
        core_thesis="t",
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
    with pytest.raises(DataValidationError):
        validate_analyst_view(view, store)


# ---------------------------------------------------------------------------
# validate_hypothesis
# ---------------------------------------------------------------------------


def _hypothesis(**overrides: Any) -> HumanInvestmentHypothesis:
    payload: dict[str, Any] = {
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


def test_validate_hypothesis_passes_for_supported_variables(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis()
    validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


def test_validate_hypothesis_detects_missing_evidence(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis(evidence_ids=["EVID-001", "EVID-666"])
    with pytest.raises(DataValidationError, match="EVID-666"):
        validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


def test_validate_hypothesis_detects_unsupported_variable(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis(selected_variables=["operating_income_yoy", "made_up_signal"])
    with pytest.raises(DataValidationError, match="made_up_signal"):
        validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


def test_validate_hypothesis_passes_when_unsupported_variable_declared(
    evidence_manifest_path: Path,
) -> None:
    """unsupported_variables에 명시하면 Registry 미지원이어도 통과한다."""
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis(
        selected_variables=["operating_income_yoy", "custom_signal"],
        unsupported_variables=["custom_signal"],
    )
    validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


def test_validate_hypothesis_detects_blank_view_id(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis(view_id="  ")
    with pytest.raises(DataValidationError, match="AnalystView"):
        validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


def test_validate_hypothesis_aggregates_multiple_violations(evidence_manifest_path: Path) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = _hypothesis(
        evidence_ids=["EVID-999"],
        selected_variables=["unknown_var"],
        view_id="",
    )
    with pytest.raises(DataValidationError) as exc_info:
        validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})
    message = str(exc_info.value)
    assert "EVID-999" in message
    assert "unknown_var" in message
    assert "AnalystView" in message


@pytest.mark.parametrize("key", ["unsupported_variable_undeclared", "evidence_not_in_store"])
def test_fixture_hypothesis_external_reference_violations(
    key: str,
    hypothesis_violations: dict[str, dict[str, Any]],
    evidence_manifest_path: Path,
) -> None:
    store = FileEvidenceStore.from_manifest(evidence_manifest_path)
    hypothesis = HumanInvestmentHypothesis.model_validate(hypothesis_violations[key])
    with pytest.raises(DataValidationError):
        validate_hypothesis(hypothesis, store, supported_variables={"operating_income_yoy"})


# ---------------------------------------------------------------------------
# approve_hypothesis
# ---------------------------------------------------------------------------


def test_approve_hypothesis_returns_approved_copy_without_mutating_original() -> None:
    original = _hypothesis()
    approved = approve_hypothesis(original, approved_by="user", now="2026-07-14T12:00:00+09:00")

    assert original.status == HypothesisStatus.DRAFT
    assert original.approved_by is None

    assert approved.status == HypothesisStatus.APPROVED
    assert approved.approved_by == "user"
    assert approved.approved_at == "2026-07-14T12:00:00+09:00"
    assert approved.updated_at == "2026-07-14T12:00:00+09:00"
    assert approved is not original


def test_approve_hypothesis_uses_now_kst_iso_when_now_omitted() -> None:
    approved = approve_hypothesis(_hypothesis(), approved_by="user")
    assert approved.approved_at is not None
    assert approved.approved_at != ""


def test_approve_hypothesis_rejects_blank_approver() -> None:
    with pytest.raises(DataValidationError):
        approve_hypothesis(_hypothesis(), approved_by="   ")


def test_approve_hypothesis_result_satisfies_full_model_validation() -> None:
    """approve_hypothesis의 반환값은 재검증을 거치므로 항상 전체 모델 제약을 만족한다."""
    approved = approve_hypothesis(
        _hypothesis(), approved_by="user", now="2026-07-14T12:00:00+09:00"
    )
    # 재구성해도 동일해야 한다 — 이미 검증된 값이라는 뜻.
    restored = HumanInvestmentHypothesis.model_validate_json(approved.model_dump_json())
    assert restored == approved
