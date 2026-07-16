"""산출물 모델 (docs/OUTPUT_SCHEMA.md §1~§8, 정본 원문 1804_FEEDBACK.md).

필드·타입은 docs/OUTPUT_SCHEMA.md를 그대로 따른다. "구현 보강"으로 표시된 필드는
원문의 검증 규칙(예: 승인 주체·시각 저장, 미지원 변수 명시)을 만족시키기 위해
OUTPUT_SCHEMA.md가 추가한 필드다.

역할 분리 원칙(원문 §18, docs/AI_ROLE_BOUNDARY.md): AI가 생성하는 CandidateAnalysis·
HypothesisCandidate에는 최종 투자 의견 필드가 없다. 사용자가 작성하는 AnalystView·
HumanInvestmentHypothesis·BacktestInterpretation과 모델·파일 수준에서 분리된다.
"""

from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_KST = ZoneInfo("Asia/Seoul")


def now_kst_iso() -> str:
    """현재 시각을 KST ISO8601 문자열로 반환한다.

    모든 시각 필드(created_at·updated_at·approved_at 등)는 원문대로 파싱을
    강제하지 않는 ``str``이다 — 이 헬퍼는 그 문자열을 생성하는 일관된 방법을
    제공할 뿐, 모델 필드 자체가 datetime 파싱을 요구하지는 않는다.
    """
    return datetime.now(_KST).isoformat()


def _require_nonblank(value: str, field_name: str) -> str:
    """공백만으로 이루어진 문자열을 거부한다 — 원문 텍스트는 그대로 보존한다."""
    if not value.strip():
        raise ValueError(f"{field_name}은(는) 비어 있을 수 없습니다.")
    return value


def _is_blank(value: str | None) -> bool:
    """None이거나 공백만으로 이루어진 문자열이면 True (승인 필드 검증용)."""
    return value is None or not value.strip()


# ---------------------------------------------------------------------------
# §1 CandidateAnalysis (AI — 원문 §4 그대로)
# ---------------------------------------------------------------------------


class Finding(BaseModel):
    """AI가 정리한 사실·해석 후보 (원문 §4). ``category``는 원문 그대로의 필드명이다."""

    model_config = ConfigDict(extra="forbid")

    finding_id: str
    category: str
    statement: str
    evidence_ids: list[str] = Field(min_length=1)  # 프롬프트 제약 3의 모델화
    confidence: float = Field(ge=0.0, le=1.0)
    source_type: str
    limitations: list[str] = Field(default_factory=list)


class RelationshipCandidate(BaseModel):
    """변수 간 관계 후보 (원문 §4)."""

    model_config = ConfigDict(extra="forbid")

    relationship_id: str
    cause_or_signal: str
    outcome: str
    proposed_mechanism: str
    evidence_ids: list[str]
    counter_evidence_ids: list[str] = Field(default_factory=list)
    measurable_variables: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)


class CandidateAnalysis(BaseModel):
    """AI 분석 후보 — 최종 투자 판단이 아니라 후보만 담는다 (원문 §4).

    역할 분리 테스트(원문 §18): 이 모델에는 최종 투자 의견·추천 필드가 없다.
    필드 집합은 아래 8개로 고정되며, 새 필드를 추가할 때는
    ``tests/unit/hitl/test_hitl_models.py``의 필드 고정 테스트를 함께 갱신해야 한다.
    """

    model_config = ConfigDict(extra="forbid")

    financial_findings: list[Finding]
    business_findings: list[Finding]
    industry_findings: list[Finding]
    catalyst_candidates: list[Finding]
    risk_candidates: list[Finding]
    relationship_candidates: list[RelationshipCandidate]
    conflicting_evidence: list[Finding]
    missing_information: list[str]


# ---------------------------------------------------------------------------
# §2 AnalystView (사용자 — 원문 §5 그대로)
# ---------------------------------------------------------------------------


class AnalystView(BaseModel):
    """사용자가 작성하는 분석 관점 (원문 §5). AI 후보를 바탕으로 사람이 채운다."""

    model_config = ConfigDict(extra="forbid")

    view_id: str
    author: str

    research_question: str
    core_thesis: str

    selected_evidence_ids: list[str] = Field(min_length=2)
    rejected_evidence_ids: list[str] = Field(default_factory=list)

    evidence_selection_reason: str
    rejected_evidence_reasons: dict[str, str] = Field(default_factory=dict)

    interpretation: str
    expected_mechanism: str

    counterarguments: list[str] = Field(min_length=1)
    uncertainties: list[str]

    created_at: str
    updated_at: str

    @field_validator("research_question")
    @classmethod
    def _validate_research_question(cls, v: str) -> str:
        return _require_nonblank(v, "research_question")

    @field_validator("core_thesis")
    @classmethod
    def _validate_core_thesis(cls, v: str) -> str:
        return _require_nonblank(v, "core_thesis")

    @model_validator(mode="after")
    def _validate_no_evidence_overlap(self) -> "AnalystView":
        overlap = set(self.selected_evidence_ids) & set(self.rejected_evidence_ids)
        if overlap:
            raise ValueError(
                "selected_evidence_ids와 rejected_evidence_ids가 겹칩니다: "
                + ", ".join(sorted(overlap))
            )
        return self


# ---------------------------------------------------------------------------
# §3 HumanInvestmentHypothesis (사용자 — 원문 §6 + 구현 보강)
# ---------------------------------------------------------------------------


class HypothesisStatus(StrEnum):
    """가설 상태 (원문 §6 허용값 7종)."""

    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    TESTED = "TESTED"
    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    REJECTED = "REJECTED"
    REVISED = "REVISED"


# HumanInvestmentHypothesis.content_origin의 허용값 (구현 보강 — OUTPUT_SCHEMA §3).
# ContentOrigin(§8) 7종 중 "사용자가 최종 작성" 또는 "AI 초안을 사용자가 승인"만 허용한다.
_HUMAN_HYPOTHESIS_ORIGINS = frozenset({"HUMAN_HYPOTHESIS", "AI_DRAFT_HUMAN_APPROVED"})


class HumanInvestmentHypothesis(BaseModel):
    """사용자가 작성·승인하는 최종 투자 가설 (원문 §6).

    AI 초안은 :class:`HypothesisCandidate`로 별도 파일에 보관한다.
    """

    model_config = ConfigDict(extra="forbid")

    hypothesis_id: str
    view_id: str
    author: str

    thesis: str
    economic_rationale: str
    expected_mechanism: str

    selected_variables: list[str]
    expected_direction: str
    investment_horizon_days: int

    evidence_ids: list[str]
    falsification_conditions: list[str] = Field(min_length=1)
    limitations: list[str]

    status: HypothesisStatus
    created_at: str
    updated_at: str

    # --- 구현 보강 (OUTPUT_SCHEMA.md §3) ---
    unsupported_variables: list[str] = Field(default_factory=list)
    content_origin: str = "HUMAN_HYPOTHESIS"
    approved_by: str | None = None
    approved_at: str | None = None

    @field_validator("content_origin")
    @classmethod
    def _validate_content_origin(cls, v: str) -> str:
        if v not in _HUMAN_HYPOTHESIS_ORIGINS:
            raise ValueError(
                f"content_origin은 {sorted(_HUMAN_HYPOTHESIS_ORIGINS)} 중 하나여야 합니다: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_approval_requires_approver(self) -> "HumanInvestmentHypothesis":
        if self.status == HypothesisStatus.APPROVED and (
            _is_blank(self.approved_by) or _is_blank(self.approved_at)
        ):
            raise ValueError(
                "status가 APPROVED이면 approved_by·approved_at을 모두 기록해야 합니다"
                "(AI 초안을 승인한 경우에도 최종 승인 주체·시각을 저장한다 — 원문 §6)."
            )
        return self


# ---------------------------------------------------------------------------
# §4 HypothesisCandidate (AI 참고용 — 원문 §7 그대로, 인간 가설과 모델·파일 분리)
# ---------------------------------------------------------------------------


class HypothesisCandidate(BaseModel):
    """AI가 제시하는 참고용 가설 후보 (원문 §7). 승인된 투자 가설이 아니다.

    ``human_investment_hypothesis.json``과는 별도 파일(``hypothesis_candidates.json``)에
    저장한다 — AI_ROLE_BOUNDARY.md §3의 "다른 모델·다른 파일" 강제 장치.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    title: str
    rationale: str
    measurable_variables: list[str]
    evidence_ids: list[str]
    counter_evidence_ids: list[str]
    limitations: list[str]
    generated_by: str
    prompt_version: str


# ---------------------------------------------------------------------------
# §5 StrategyReview (사용자 — 원문 §9 그대로)
# ---------------------------------------------------------------------------


class StrategyModification(BaseModel):
    """AI 초안과 최종 승인 전략의 필드 단위 차이 (원문 §9)."""

    model_config = ConfigDict(extra="forbid")

    field_path: str
    draft_value: object | None = None
    final_value: object | None = None
    reason: str
    modified_by: str

    @field_validator("modified_by")
    @classmethod
    def _validate_modified_by(cls, v: str) -> str:
        return _require_nonblank(v, "modified_by")


class StrategyReview(BaseModel):
    """사용자가 전략 초안을 검토·수정·승인한 기록 (원문 §9).

    ``modifications``의 각 원소는 ``StrategyModification`` 자체 검증으로
    ``modified_by`` 비어있음을 이미 거부하므로 별도 재검증이 필요 없다.
    """

    model_config = ConfigDict(extra="forbid")

    review_id: str
    hypothesis_id: str

    llm_draft_strategy: dict[str, object]
    final_strategy: dict[str, object]

    modifications: list[StrategyModification]
    approval_reason: str

    approved_by: str
    approved_at: str

    @field_validator("approved_by")
    @classmethod
    def _validate_approved_by(cls, v: str) -> str:
        return _require_nonblank(v, "approved_by")


# ---------------------------------------------------------------------------
# §6 BacktestInterpretation (사용자 — 원문 §10 + 구현 보강)
# ---------------------------------------------------------------------------


_HYPOTHESIS_DECISIONS = frozenset(
    {"SUPPORTED", "PARTIALLY_SUPPORTED", "REJECTED", "REVISED", "INCONCLUSIVE"}
)

# BacktestInterpretation.content_origin의 허용값 (구현 보강 — OUTPUT_SCHEMA §6):
# "사람이 작성했는지, AI 초안을 수정했는지 구분하여 기록한다."
_INTERPRETATION_ORIGINS = frozenset({"HUMAN_INTERPRETATION", "AI_DRAFT_HUMAN_APPROVED"})


class BacktestInterpretation(BaseModel):
    """사용자가 작성하는 백테스트 결과 최종 해석·가설 판정 (원문 §10)."""

    model_config = ConfigDict(extra="forbid")

    interpretation_id: str
    hypothesis_id: str
    strategy_id: str
    author: str

    main_findings: str
    supporting_results: list[str]
    contradicting_results: list[str]

    regime_dependence: str | None = None
    limitations: list[str]

    hypothesis_decision: str
    decision_reason: str

    revised_hypothesis: str | None = None
    followup_tests: list[str]

    created_at: str

    # --- 구현 보강 (OUTPUT_SCHEMA.md §6) ---
    content_origin: str = "HUMAN_INTERPRETATION"

    @field_validator("hypothesis_decision")
    @classmethod
    def _validate_hypothesis_decision(cls, v: str) -> str:
        if v not in _HYPOTHESIS_DECISIONS:
            raise ValueError(
                f"hypothesis_decision은 {sorted(_HYPOTHESIS_DECISIONS)} 중 하나여야 합니다: {v!r}"
            )
        return v

    @field_validator("decision_reason")
    @classmethod
    def _validate_decision_reason(cls, v: str) -> str:
        return _require_nonblank(v, "decision_reason")

    @field_validator("content_origin")
    @classmethod
    def _validate_content_origin(cls, v: str) -> str:
        if v not in _INTERPRETATION_ORIGINS:
            raise ValueError(
                f"content_origin은 {sorted(_INTERPRETATION_ORIGINS)} 중 하나여야 합니다: {v!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_supporting_or_contradicting(self) -> "BacktestInterpretation":
        if not self.supporting_results and not self.contradicting_results:
            raise ValueError(
                "supporting_results와 contradicting_results 중 적어도 하나는 있어야 합니다."
            )
        return self

    @model_validator(mode="after")
    def _validate_revised_requires_hypothesis(self) -> "BacktestInterpretation":
        if self.hypothesis_decision == "REVISED" and not (
            self.revised_hypothesis and self.revised_hypothesis.strip()
        ):
            raise ValueError("hypothesis_decision이 REVISED이면 revised_hypothesis가 필요합니다.")
        return self


# ---------------------------------------------------------------------------
# §7 AIUsageRecord (과제 2 증빙 — 원문 §12 그대로)
# ---------------------------------------------------------------------------


class AIUsageRecord(BaseModel):
    """AI 호출 1건의 사용 기록 (원문 §12) — ``ai_usage_log.jsonl``에 append."""

    model_config = ConfigDict(extra="forbid")

    usage_id: str
    stage: str

    model: str
    prompt_name: str
    prompt_version: str

    input_artifact_ids: list[str]
    output_artifact_ids: list[str]

    ai_role: str
    human_review_required: bool

    human_changes_summary: str | None = None
    created_at: str


# ---------------------------------------------------------------------------
# §8 AuthoredContent · ContentOrigin (원문 §16 그대로)
# ---------------------------------------------------------------------------


class ContentOrigin(StrEnum):
    """서술형 콘텐츠의 저작 출처 7종 (원문 §16)."""

    SOURCE_FACT = "SOURCE_FACT"
    PYTHON_CALCULATION = "PYTHON_CALCULATION"
    AI_CANDIDATE = "AI_CANDIDATE"
    HUMAN_ANALYSIS = "HUMAN_ANALYSIS"
    HUMAN_HYPOTHESIS = "HUMAN_HYPOTHESIS"
    AI_DRAFT_HUMAN_APPROVED = "AI_DRAFT_HUMAN_APPROVED"
    HUMAN_INTERPRETATION = "HUMAN_INTERPRETATION"


class AuthoredContent(BaseModel):
    """저작 구분이 붙은 서술형 콘텐츠 단위 (원문 §16). 보고서 태그 노출은 선택이지만
    이 모델의 ``content_origin`` 저장은 필수다(docs/AI_ROLE_BOUNDARY.md §2)."""

    model_config = ConfigDict(extra="forbid")

    content: str
    content_origin: ContentOrigin
    author: str | None = None
    source_ids: list[str]
    ai_usage_id: str | None = None


# ---------------------------------------------------------------------------
# RunManifest (구현 보강 — docs/specs/CLI-integration.md §5.0, OUTPUT_SCHEMA.md §0)
# ---------------------------------------------------------------------------


class RunManifest(BaseModel):
    """실행(run) 1건의 불변 메타 (README §29, OUTPUT_SCHEMA.md §0 run_manifest.json).

    :class:`~research_backtest.core.hitl.states.RunState`(진행 상태·전이 이력)와
    역할을 분리한다 — 이 모델은 ``create-run`` 시점에 한 번 기록되는 불변 식별
    정보만 담는다. README §29가 제안하는 config_hash·started_at·completed_at·
    status는 채택하지 않는다: 상태·이력은 run_state.json이 정본이고, 실행에
    사용된 설정값은 그 결과 산출물(strategy_spec.json 등) 자체가 이미 기록하므로
    이 매니페스트에 중복 저장할 이유가 없다(docs/specs/CLI-integration.md §5.0).
    """

    model_config = ConfigDict(extra="forbid")

    run_id: str
    company_query: str  # 사용자가 입력한 질의 문자열 (--company 원본, resolve 이전 값)
    corp_code: str  # DART 8자리
    corp_name: str
    corp_eng_name: str | None = None
    stock_code: str  # 6자리 — 상장사만 run 생성 허용
    as_of_date: str  # YYYY-MM-DD (분석 기준일)
    created_at: str  # KST ISO8601
    code_version: str | None = None  # git short hash, best-effort


__all__ = [
    "AIUsageRecord",
    "AnalystView",
    "AuthoredContent",
    "BacktestInterpretation",
    "CandidateAnalysis",
    "ContentOrigin",
    "Finding",
    "HumanInvestmentHypothesis",
    "HypothesisCandidate",
    "HypothesisStatus",
    "RelationshipCandidate",
    "RunManifest",
    "StrategyModification",
    "StrategyReview",
    "now_kst_iso",
]
