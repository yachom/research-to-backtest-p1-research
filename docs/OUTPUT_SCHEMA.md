# 산출물 스키마 (요구사항 v2)

> 정본 원문: `1804_FEEDBACK.md`. 이 문서는 구현 관점의 정리이며 모델 필드는
> 원문을 그대로 따른다. "구현 보강" 표시는 원문의 검증 규칙을 만족시키기 위해
> 추가한 필드다. 구현은 `core/hitl/models.py`.

## 0. 한 번의 실행이 생성하는 파일 (원문 §20)

```text
outputs/{run_id}/
├── run_manifest.json                  # 실행 메타 (README §29)
├── run_state.json                     # 파이프라인 상태·전이 이력 (구현 보강 — 원문 §13의 상태 표시·기록 요구)
├── evidence_manifest.json             # Evidence Store (C1'에서 생성)
├── candidate_analysis.json            # AI (CandidateAnalysis)
├── hypothesis_candidates.json         # AI 참고용 (list[HypothesisCandidate]) — 승인된 가설 아님
├── analyst_view.json                  # 사용자 (AnalystView)
├── human_investment_hypothesis.json   # 사용자 (HumanInvestmentHypothesis)
├── strategy_draft.json                # AI 초안 (StrategySpec dict)
├── strategy_review.json               # 사용자 (StrategyReview — 수정 이력 포함)
├── strategy_spec.json                 # 승인된 최종 전략
├── backtest_result.json / trade_log.csv / charts/
├── backtest_interpretation.json       # 사용자 (BacktestInterpretation)
├── ai_usage_log.jsonl                 # AIUsageRecord (과제 2 증빙)
└── research_report.md                 # 15개 섹션 보고서
```

## 1. CandidateAnalysis (AI — 원문 §4 그대로)

```python
class Finding(BaseModel):
    finding_id: str
    category: str
    statement: str
    evidence_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    source_type: str
    limitations: list[str] = []

class RelationshipCandidate(BaseModel):
    relationship_id: str
    cause_or_signal: str
    outcome: str
    proposed_mechanism: str
    evidence_ids: list[str]
    counter_evidence_ids: list[str] = []
    measurable_variables: list[str] = []
    confidence: float = Field(ge=0.0, le=1.0)

class CandidateAnalysis(BaseModel):
    financial_findings: list[Finding]
    business_findings: list[Finding]
    industry_findings: list[Finding]
    catalyst_candidates: list[Finding]
    risk_candidates: list[Finding]
    relationship_candidates: list[RelationshipCandidate]
    conflicting_evidence: list[Finding]
    missing_information: list[str]
```

역할 분리 테스트(원문 §18): CandidateAnalysis에는 최종 투자 의견 필드가 **없어야 한다**.

## 0.1 RunManifest (구현 보강 — CLI 통합 패스, docs/specs/CLI-integration.md §5.0)

`run_manifest.json`(README §29)의 코드화. RunState(진행 상태·전이 이력)와 역할을
분리해 **불변 식별 정보만** 담는다 — README §29의 config_hash·started_at·
completed_at·status는 채택하지 않는다(상태·이력은 run_state.json이 정본, 설정값은
산출물 자체가 기록). `r2b create-run`이 생성하고 `r2b backtest`가 corp_code·
stock_code·as_of_date를 소비한다.

```python
class RunManifest(BaseModel):
    run_id: str
    company_query: str            # 사용자 입력 질의 문자열 (resolve 이전 값)
    corp_code: str                # DART 8자리
    corp_name: str
    corp_eng_name: str | None
    stock_code: str               # 6자리 — 상장사만 run 생성 허용
    as_of_date: str               # YYYY-MM-DD (분석 기준일)
    created_at: str               # KST ISO8601
    code_version: str | None      # git short hash, best-effort
```

비고: §0 트리의 `charts/`는 C3' 예정이며, A6 백테스트의 실제 산출물 3종은
`backtest_result.json`·`trade_log.csv`·`daily_portfolio.csv`다(명세 A6 §5).

## 2. AnalystView (사용자 — 원문 §5 그대로)

필드: view_id, author, research_question, core_thesis, selected_evidence_ids,
rejected_evidence_ids, evidence_selection_reason, rejected_evidence_reasons(dict),
interpretation, expected_mechanism, counterarguments, uncertainties, created_at, updated_at.

검증(원문): research_question·core_thesis 비어 있을 수 없음 / selected ≥ 2 /
선택 근거는 Evidence Store에 실존 / counterarguments ≥ 1 / 선택∩제외 = ∅ /
사용자 작성값과 AI 생성값 구분 가능(content_origin).

## 3. HumanInvestmentHypothesis (사용자 — 원문 §6)

```python
class HumanInvestmentHypothesis(BaseModel):
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
    falsification_conditions: list[str]
    limitations: list[str]

    status: HypothesisStatus     # DRAFT/APPROVED/TESTED/SUPPORTED/PARTIALLY_SUPPORTED/REJECTED/REVISED
    created_at: str
    updated_at: str

    # --- 구현 보강 (원문 검증 규칙의 모델화) ---
    unsupported_variables: list[str] = []          # "Registry 미지원 변수로 명시" 규칙
    content_origin: str = "HUMAN_HYPOTHESIS"       # HUMAN_HYPOTHESIS | AI_DRAFT_HUMAN_APPROVED
    approved_by: str | None = None                 # "최종 승인 주체와 승인 시각 저장" 규칙
    approved_at: str | None = None
```

검증(원문): 미승인 가설은 백테스트 전달 금지 / falsification ≥ 1 /
selected_variables는 Registry 지원 또는 미지원으로 명시 / AnalystView·Evidence 연결 /
AI 초안이어도 승인 주체·시각 저장 (⇒ 구현: APPROVED면 approved_by·approved_at 필수).

## 4. HypothesisCandidate (AI 참고용 — 원문 §7 그대로, 인간 가설과 모델·파일 분리)

```python
class HypothesisCandidate(BaseModel):
    candidate_id: str
    title: str
    rationale: str
    measurable_variables: list[str]
    evidence_ids: list[str]
    counter_evidence_ids: list[str]
    limitations: list[str]
    generated_by: str
    prompt_version: str
```

## 5. StrategyReview (사용자 — 원문 §9 그대로)

StrategyModification(field_path, draft_value, final_value, reason, modified_by) +
StrategyReview(review_id, hypothesis_id, llm_draft_strategy(dict), final_strategy(dict),
modifications, approval_reason, approved_by, approved_at).

수정 기록 예시(원문): `execution.trade_time` same_close→next_open(룩어헤드 방지),
`entry.operating_income_yoy.right` 0.1→0.2(통상 변동과 구분).

## 6. BacktestInterpretation (사용자 — 원문 §10 그대로)

필드: interpretation_id, hypothesis_id, strategy_id, author, main_findings,
supporting_results, contradicting_results, regime_dependence(str|None), limitations,
hypothesis_decision(SUPPORTED/PARTIALLY_SUPPORTED/REJECTED/REVISED/INCONCLUSIVE),
decision_reason, revised_hypothesis(str|None), followup_tests, created_at.
(+ 구현 보강: content_origin — 사람 작성 vs AI 초안 수정 구분 규칙)

필수 검증(원문): 백테스트 결과 없으면 작성 불가 / decision_reason 비어 있을 수 없음 /
supporting·contradicting 중 최소 하나 존재 / REVISED ⇒ revised_hypothesis 필수 /
사람 작성 vs AI 초안 수정 구분 기록.

## 7. AIUsageRecord (과제 2 증빙 — 원문 §12 그대로, ai_usage_log.jsonl)

```python
class AIUsageRecord(BaseModel):
    usage_id: str
    stage: str                      # 분석 후보/관계 후보/전략 초안/결과 설명 초안
    model: str
    prompt_name: str
    prompt_version: str
    input_artifact_ids: list[str]
    output_artifact_ids: list[str]
    ai_role: str
    human_review_required: bool
    human_changes_summary: str | None
    created_at: str
```

프롬프트 원문은 버전 파일로 레포에 저장(원문 §12 — D3·D7 레이아웃 적용):
`src/research_backtest/research/prompts/{candidate_analysis,hypothesis_candidate}_v1.txt`,
`src/research_backtest/quant/prompts/{strategy_translation,result_explanation}_v1.txt`

## 8. AuthoredContent · 출처 태그 (원문 §16 그대로)

AuthoredContent(content, content_origin, author, source_ids, ai_usage_id).
content_origin 7종: SOURCE_FACT / PYTHON_CALCULATION / AI_CANDIDATE / HUMAN_ANALYSIS /
HUMAN_HYPOTHESIS / AI_DRAFT_HUMAN_APPROVED / HUMAN_INTERPRETATION.
보고서 표시 태그: [데이터 사실][AI 정리][사용자 해석][사용자 가설][Python 검증 결과][사용자 최종 판단]
(최종 보고서에 태그 노출은 선택, 내부 모델 저장은 필수).

파이프라인 상태 12종·게이트·테스트 플래그: docs/HUMAN_IN_THE_LOOP.md §3 (원문 §13).
