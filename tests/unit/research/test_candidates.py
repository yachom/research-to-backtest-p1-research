"""후보 생성기 unit 테스트 (명세 W3b §4 — FakeLlmClient로 재시도·주입·검증).

네트워크 없이 :class:`FakeLlmClient`로 LLM 응답을 재생해:

- evidence_id 봉쇄(제공 밖 id → 재시도 피드백에 위반 id 포함)
- ``generated_by``·``prompt_version`` 코드 주입(LLM 출력 무시·덮어쓰기)
- 후보 개수(1~5) 검증
- ``select_evidence_for_prompt``의 결정적 정렬·절단

을 검증한다. 프롬프트는 실제 v1 파일(:data:`PROMPTS_DIR`)을 렌더해 템플릿-코드
정합까지 함께 확인한다.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import CandidateAnalysis
from research_backtest.core.llm.testing import FakeLlmClient
from research_backtest.research.candidates.generator import (
    PROMPTS_DIR,
    generate_candidate_analysis,
    generate_hypothesis_candidates,
    select_evidence_for_prompt,
)
from research_backtest.research.evidence.models import EvidencePackage, FinancialEvidence


def _evidence(evidence_id: str, significance: float) -> FinancialEvidence:
    return FinancialEvidence(
        evidence_id=evidence_id,
        category="SCALE",
        statement=f"{evidence_id} 서술",
        current_value=Decimal("100"),
        comparison_value=None,
        change_rate=0.1,
        period="FY2024",
        comparison_period=None,
        source_fact_ids=["FACT_x"],
        rcept_no="20250310000001",
        filing_date="2025-03-10",
        significance_score=significance,
        fs_scope="CFS",
        available_from="2025-03-11",
    )


def _package(evidence: list[FinancialEvidence]) -> EvidencePackage:
    return EvidencePackage(
        corp_code="00164779",
        as_of_date="2025-12-31",
        lookback_years=5,
        fs_scope="CFS",
        generated_at="2026-07-15T12:00:00+09:00",
        evidence=evidence,
    )


def _analysis_json(evidence_ids: list[str]) -> str:
    finding = {
        "finding_id": "FIND-1",
        "category": "재무",
        "statement": "매출이 성장했다.",
        "evidence_ids": evidence_ids,
        "confidence": 0.7,
        "source_type": "financial_statement",
        "limitations": [],
    }
    payload = {
        "financial_findings": [finding],
        "business_findings": [],
        "industry_findings": [],
        "catalyst_candidates": [],
        "risk_candidates": [],
        "relationship_candidates": [],
        "conflicting_evidence": [],
        "missing_information": ["추가 데이터 필요"],
    }
    return json.dumps(payload, ensure_ascii=False)


def _candidate(evidence_ids: list[str], **extra: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "candidate_id": "HC-1",
        "title": "실적 서프라이즈 가설",
        "rationale": "HBM 비중 확대가 이익률을 끌어올린다.",
        "measurable_variables": ["operating_income_yoy"],
        "evidence_ids": evidence_ids,
        "counter_evidence_ids": [],
        "limitations": [],
    }
    payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# select_evidence_for_prompt (§2.1)
# ---------------------------------------------------------------------------


def test_select_evidence_orders_by_significance_then_id() -> None:
    evidence = [
        _evidence("FIN_B", 0.5),
        _evidence("FIN_A", 0.5),  # 동률 → id 오름차순으로 A가 앞
        _evidence("FIN_C", 0.9),  # 최고 유의도 → 맨 앞
    ]
    selected = select_evidence_for_prompt(_package(evidence))
    assert [e.evidence_id for e in selected] == ["FIN_C", "FIN_A", "FIN_B"]


def test_select_evidence_truncates_to_max() -> None:
    evidence = [_evidence(f"FIN_{i:02d}", i / 100) for i in range(10)]
    selected = select_evidence_for_prompt(_package(evidence), max_evidence=3)
    assert len(selected) == 3
    # 유의도 상위 3건(0.09, 0.08, 0.07)
    assert [e.evidence_id for e in selected] == ["FIN_09", "FIN_08", "FIN_07"]


# ---------------------------------------------------------------------------
# generate_candidate_analysis (§2.1)
# ---------------------------------------------------------------------------


def test_candidate_analysis_success_first_attempt() -> None:
    package = _package([_evidence("FIN_A", 0.8), _evidence("FIN_B", 0.6)])
    client = FakeLlmClient([_analysis_json(["FIN_A"])])

    analysis, metadata = generate_candidate_analysis(
        package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=3
    )

    assert isinstance(analysis, CandidateAnalysis)
    assert analysis.financial_findings[0].evidence_ids == ["FIN_A"]
    assert metadata.num_attempts == 1
    assert len(client.calls) == 1
    # 프롬프트에 제약·evidence가 실렸는지(템플릿-코드 정합)
    user_prompt = client.calls[0][1]
    assert "복수 후보를 제시한다" in user_prompt
    assert "FIN_A" in user_prompt


def test_candidate_analysis_retries_on_unknown_evidence_id() -> None:
    """1차 응답이 제공 밖 evidence_id → 재시도 프롬프트에 위반 id가 실려야 한다 (§2.1)."""
    package = _package([_evidence("FIN_A", 0.8)])
    client = FakeLlmClient([_analysis_json(["FIN_BOGUS"]), _analysis_json(["FIN_A"])])

    analysis, metadata = generate_candidate_analysis(
        package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=3
    )

    assert metadata.num_attempts == 2
    assert len(client.calls) == 2
    # 2차 호출의 user_prompt에 위반 id가 피드백으로 포함된다
    assert "FIN_BOGUS" in client.calls[1][1]
    assert analysis.financial_findings[0].evidence_ids == ["FIN_A"]


def test_candidate_analysis_exhausts_and_raises() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    client = FakeLlmClient([_analysis_json(["FIN_BOGUS"])] * 2)

    with pytest.raises(DataValidationError):
        generate_candidate_analysis(package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=2)


def test_relationship_counter_evidence_ids_are_validated() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    payload = json.loads(_analysis_json(["FIN_A"]))
    payload["relationship_candidates"] = [
        {
            "relationship_id": "REL-1",
            "cause_or_signal": "HBM 매출",
            "outcome": "영업이익률",
            "proposed_mechanism": "ASP 상승",
            "evidence_ids": ["FIN_A"],
            "counter_evidence_ids": ["FIN_BOGUS"],  # 제공 밖 → 위반
            "measurable_variables": [],
            "confidence": 0.5,
        }
    ]
    client = FakeLlmClient([json.dumps(payload, ensure_ascii=False)])

    with pytest.raises(DataValidationError):
        generate_candidate_analysis(package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=1)


# ---------------------------------------------------------------------------
# generate_hypothesis_candidates (§2.1)
# ---------------------------------------------------------------------------


def test_hypothesis_candidates_success() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    analysis = CandidateAnalysis.model_validate(json.loads(_analysis_json(["FIN_A"])))
    client = FakeLlmClient([json.dumps([_candidate(["FIN_A"])], ensure_ascii=False)])

    candidates, metadata = generate_hypothesis_candidates(
        package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=3
    )

    assert len(candidates) == 1
    assert candidates[0].title == "실적 서프라이즈 가설"
    assert metadata.num_attempts == 1
    # 프롬프트에 지원 지표 목록이 실렸는지
    assert "operating_income_yoy" in client.calls[0][1]


def test_hypothesis_candidates_inject_generated_by_and_prompt_version() -> None:
    """LLM이 generated_by를 넣어도 코드가 metadata.model·"v1"로 덮어쓴다 (§2.1)."""
    package = _package([_evidence("FIN_A", 0.8)])
    analysis = CandidateAnalysis.model_validate(json.loads(_analysis_json(["FIN_A"])))
    poisoned = _candidate(["FIN_A"], generated_by="EVIL-MODEL", prompt_version="v999")
    client = FakeLlmClient([json.dumps([poisoned], ensure_ascii=False)])

    candidates, metadata = generate_hypothesis_candidates(
        package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=1
    )

    assert candidates[0].generated_by == metadata.model == "fake"
    assert candidates[0].prompt_version == "v1"


def test_hypothesis_candidates_reject_empty_list() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    analysis = CandidateAnalysis.model_validate(json.loads(_analysis_json(["FIN_A"])))
    client = FakeLlmClient(["[]", "[]"])

    with pytest.raises(DataValidationError):
        generate_hypothesis_candidates(
            package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=2
        )


def test_hypothesis_candidates_reject_more_than_five() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    analysis = CandidateAnalysis.model_validate(json.loads(_analysis_json(["FIN_A"])))
    six = [_candidate(["FIN_A"]) for _ in range(6)]
    client = FakeLlmClient([json.dumps(six, ensure_ascii=False)])

    with pytest.raises(DataValidationError):
        generate_hypothesis_candidates(
            package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=1
        )


def test_hypothesis_candidates_retry_on_unknown_evidence_id() -> None:
    package = _package([_evidence("FIN_A", 0.8)])
    analysis = CandidateAnalysis.model_validate(json.loads(_analysis_json(["FIN_A"])))
    bad = json.dumps([_candidate(["FIN_BOGUS"])], ensure_ascii=False)
    good = json.dumps([_candidate(["FIN_A"])], ensure_ascii=False)
    client = FakeLlmClient([bad, good])

    candidates, metadata = generate_hypothesis_candidates(
        package, analysis, client=client, prompts_dir=PROMPTS_DIR, max_attempts=3
    )

    assert metadata.num_attempts == 2
    assert "FIN_BOGUS" in client.calls[1][1]
    assert candidates[0].evidence_ids == ["FIN_A"]
