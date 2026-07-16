현재 구현 중인 `Research-to-Backtest` 프로젝트의 요구사항이 일부 변경되었다. 기존 코드를 전면 재작성하지 말고, 먼저 현재 레포 구조와 구현 상태를 점검한 뒤 아래 변경사항을 기존 아키텍처에 최소 침습적으로 반영해라.

# 1. 변경 목적

이 프로젝트는 채용 과제 두 건을 하나의 파이프라인으로 구현하기 위한 것이다.

- 과제 1: 기업·산업 분석 샘플
  - 본인의 시각과 근거가 필수
- 과제 2: AI 활용 검증 자료
  - 무엇을 어떤 도구와 프롬프트로 어떻게 해결했는지 설명

기존 구상은 다음과 같았다.

```text
기업명 입력
→ DART·XBRL·주가·수급·뉴스 수집
→ AI 기업 분석
→ AI 투자 가설 생성
→ AI 전략 변환
→ Python 백테스트
```

그러나 이 구조에서는 기업분석과 투자 가설이 AI의 판단처럼 보이고, 과제 1에서 요구하는 “본인의 시각”이 약해질 수 있다.

따라서 전체 구조를 다음과 같이 변경한다.

```text
기업명·분석 기준일 입력
→ 데이터 수집 및 계산
→ AI가 분석 후보와 상충 근거 정리
→ 사용자가 분석 질문과 핵심 논지 작성
→ 사용자가 사용할 근거와 제외할 근거 선택
→ 사용자가 투자 가설 작성
→ AI가 가설을 측정 가능한 전략 DSL 초안으로 변환
→ 사용자가 전략 규칙을 검토·수정·승인
→ Python 백테스트
→ 사용자가 결과를 해석하고 가설을 채택·수정·기각
```

핵심 원칙은 다음과 같다.

> AI는 사실과 후보 관계를 정리하고 사용자의 가설을 구조화하는 보조 도구다.  
> 분석 관점, 핵심 논지, 근거 선택, 투자 가설, 전략 승인, 결과 해석은 사용자가 담당한다.

# 2. 먼저 수행할 작업

코드를 수정하기 전에 현재 레포를 점검하고 다음 내용을 보고해라.

1. 현재 디렉터리 구조
2. 구현된 모듈과 미구현 모듈
3. 현재 기업분석 생성 흐름
4. 현재 투자 가설 생성 주체와 호출 위치
5. 현재 전략 JSON 생성 흐름
6. 현재 Pydantic 모델 또는 데이터 스키마
7. 아래 변경사항과 충돌하는 기존 코드
8. 재사용 가능한 코드와 수정이 필요한 코드
9. 변경 작업 순서

점검 결과를 먼저 간단히 제시한 뒤 바로 수정 작업을 진행해라. 추가 확인을 기다리지 말고 합리적인 범위에서 구현을 계속해라.

# 3. 역할 분리

시스템에서 각 주체의 역할을 명확히 분리한다.

## 3.1 Python의 역할

- DART API 호출
- XBRL 원본 다운로드 및 파싱
- 계정 표준화
- 재무비율 계산
- 공시일 기준 Point-in-Time 정렬
- 주가·거래량·수급 지표 계산
- 백테스트
- 성과지표 산출
- 근거 ID와 데이터 lineage 관리

Python이 산출한 수치는 LLM이 다시 계산하지 않는다.

## 3.2 AI의 역할

- 재무 변화 후보 정리
- 주요 공시 이벤트 후보 정리
- 투자 포인트 후보 정리
- 위험요인 후보 정리
- 변수 간 관계 후보 정리
- 상충하는 근거 정리
- 사용자가 작성한 투자 가설을 전략 DSL 초안으로 변환
- 백테스트 결과 설명 초안 작성

AI가 최종 분석 논지나 최종 투자 가설을 자율적으로 확정하면 안 된다.

## 3.3 사용자의 역할

- 분석 질문 작성
- 핵심 논지 작성
- 중요 근거 선택
- 불필요하거나 신뢰하기 어려운 근거 제외
- 근거 선택 이유 작성
- 반대 논리와 불확실성 작성
- 투자 가설 작성
- 전략 임계값과 매매 규칙 최종 승인
- 백테스트 결과 해석
- 가설 채택·수정·기각 결정

# 4. 기존 AI 분석 모듈 변경

기존에 `CompanyAnalysis` 또는 이와 유사한 모델이 AI의 최종 기업분석을 직접 출력하고 있다면, 이를 다음 두 단계로 분리해라.

## 4.1 AI 분석 후보: CandidateAnalysis

AI는 최종 투자 판단이 아니라 분석 후보만 생성한다.

```python
from pydantic import BaseModel, Field


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

AI 프롬프트에는 다음 제약을 추가한다.

```text
1. 최종 투자 의견을 확정하지 않는다.
2. 제공된 evidence package 외의 사실을 사용하지 않는다.
3. 모든 finding에 evidence_id를 연결한다.
4. 사실, 해석 후보, 관계 가설 후보를 구분한다.
5. 주장과 충돌하는 근거도 함께 제시한다.
6. 데이터가 없는 경우 추정하지 않는다.
7. 사용자가 선택할 수 있도록 복수 후보를 제시한다.
```

# 5. 사용자 분석 관점 모델 추가

AI가 생성한 후보를 바탕으로 사용자가 직접 작성하는 `AnalystView` 모델을 추가한다.

```python
class AnalystView(BaseModel):
    view_id: str
    author: str

    research_question: str
    core_thesis: str

    selected_evidence_ids: list[str]
    rejected_evidence_ids: list[str] = []

    evidence_selection_reason: str
    rejected_evidence_reasons: dict[str, str] = {}

    interpretation: str
    expected_mechanism: str

    counterarguments: list[str]
    uncertainties: list[str]

    created_at: str
    updated_at: str
```

## 검증 규칙

- `research_question`은 비어 있을 수 없다.
- `core_thesis`는 비어 있을 수 없다.
- `selected_evidence_ids`는 최소 2개 이상이어야 한다.
- 선택한 근거 ID는 Evidence Store에 실제로 존재해야 한다.
- `counterarguments`는 최소 1개 이상이어야 한다.
- 선택 근거와 제외 근거가 중복되면 안 된다.
- 사용자가 직접 작성한 값과 AI 생성값을 구분할 수 있어야 한다.

## 저장 파일

```text
outputs/{run_id}/analyst_view.json
```

# 6. 사용자 투자 가설 모델 추가

기존 AI 자동 투자 가설 생성은 최종 산출물이 아니라 `hypothesis candidate` 생성으로 제한한다.

최종 투자 가설은 사용자가 작성하거나 승인해야 한다.

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

    status: str
    created_at: str
    updated_at: str
```

`status` 허용값:

```text
DRAFT
APPROVED
TESTED
SUPPORTED
PARTIALLY_SUPPORTED
REJECTED
REVISED
```

## 검증 규칙

- 승인되지 않은 가설은 백테스트로 전달하지 않는다.
- 최소 한 개 이상의 `falsification_conditions`가 필요하다.
- `selected_variables`는 Indicator Registry에서 지원하거나, 지원하지 않는 변수로 명시되어야 한다.
- 가설은 `AnalystView` 및 Evidence Store와 연결되어야 한다.
- AI가 초안을 제안한 경우에도 최종 승인 주체와 승인 시각을 저장한다.

## 저장 파일

```text
outputs/{run_id}/human_investment_hypothesis.json
```

# 7. AI 생성 가설 후보는 별도 보관

AI가 관계 후보나 가설 후보를 제시할 수는 있지만, 인간 작성 가설과 같은 모델에 저장하면 안 된다.

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

저장 파일:

```text
outputs/{run_id}/hypothesis_candidates.json
```

이 후보는 참고용이며 승인된 투자 가설이 아니다.

# 8. 전략 변환 프로세스 변경

기존 흐름이 다음과 같다면:

```text
AI 투자 가설
→ AI StrategySpec 생성
→ 백테스트
```

아래와 같이 변경한다.

```text
승인된 HumanInvestmentHypothesis
→ AI StrategySpec 초안 생성
→ Validator 검사
→ 사용자 검토 및 수정
→ 최종 StrategySpec 승인
→ 백테스트
```

# 9. 전략 검토 기록 추가

AI 초안과 사용자가 승인한 최종 전략의 차이를 저장한다.

```python
class StrategyModification(BaseModel):
    field_path: str
    draft_value: object | None
    final_value: object | None
    reason: str
    modified_by: str


class StrategyReview(BaseModel):
    review_id: str
    hypothesis_id: str

    llm_draft_strategy: dict
    final_strategy: dict

    modifications: list[StrategyModification]
    approval_reason: str

    approved_by: str
    approved_at: str
```

저장 파일:

```text
outputs/{run_id}/strategy_draft.json
outputs/{run_id}/strategy_review.json
outputs/{run_id}/strategy_spec.json
```

## 필수 기록 예시

```json
{
  "field_path": "execution.trade_time",
  "draft_value": "same_close",
  "final_value": "next_open",
  "reason": "종가 기준 신호를 당일 종가에 체결하는 룩어헤드 문제를 방지하기 위해 수정했다.",
  "modified_by": "user"
}
```

또는:

```json
{
  "field_path": "entry.operating_income_yoy.right",
  "draft_value": 0.1,
  "final_value": 0.2,
  "reason": "10% 증가는 반도체 기업의 통상적 실적 변동과 구분하기 어렵다고 판단해 기준을 상향했다.",
  "modified_by": "user"
}
```

# 10. 백테스트 결과에 사용자 해석 단계 추가

백테스트 완료 후 AI 설명만 생성하지 말고, 사용자가 최종 해석과 가설 판정을 작성하는 모델을 추가한다.

```python
class BacktestInterpretation(BaseModel):
    interpretation_id: str
    hypothesis_id: str
    strategy_id: str
    author: str

    main_findings: str
    supporting_results: list[str]
    contradicting_results: list[str]

    regime_dependence: str | None
    limitations: list[str]

    hypothesis_decision: str
    decision_reason: str

    revised_hypothesis: str | None
    followup_tests: list[str]

    created_at: str
```

`hypothesis_decision` 허용값:

```text
SUPPORTED
PARTIALLY_SUPPORTED
REJECTED
REVISED
INCONCLUSIVE
```

저장 파일:

```text
outputs/{run_id}/backtest_interpretation.json
```

## 필수 검증

- 백테스트 결과가 없으면 작성할 수 없다.
- `decision_reason`은 비어 있을 수 없다.
- `supporting_results`와 `contradicting_results` 중 적어도 하나는 존재해야 한다.
- `REVISED`인 경우 `revised_hypothesis`가 필요하다.
- 결과 해석은 사람이 작성했는지, AI 초안을 수정했는지 구분하여 기록한다.

# 11. 최종 보고서 구조 변경

최종 기업·산업 분석 보고서는 아래 순서로 생성한다.

```text
1. 분석 대상과 기준일
2. 분석 질문 — 사용자 작성
3. 핵심 결론 — 사용자 작성
4. 주요 재무 및 산업 근거
5. 선택한 근거와 선택 이유 — 사용자 작성
6. 제외한 근거와 제외 이유 — 사용자 작성
7. 반대 논리와 불확실성 — 사용자 작성
8. 투자 가설 — 사용자 작성·승인
9. 전략 규칙과 사용자 수정 내역
10. 백테스트 결과
11. 가설에 유리한 결과
12. 가설에 불리한 결과
13. 최종 판단 — 사용자 작성
14. 가설의 채택·수정·기각 여부
15. AI가 수행한 작업과 사용자가 수행한 작업
```

보고서 제목은 단순 기업명이 아니라 사용자의 논지가 드러나도록 구성할 수 있어야 한다.

예:

```text
SK하이닉스: 실적 회복보다 중요한 것은 시장 기대의 확인이다
```

# 12. AI 활용 검증 로그 추가

과제 2에서 AI 사용 과정을 설명할 수 있도록 다음 정보를 저장한다.

```python
class AIUsageRecord(BaseModel):
    usage_id: str
    stage: str

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

기록 대상:

- 분석 후보 생성
- 관계 후보 생성
- 전략 DSL 초안 생성
- 백테스트 결과 설명 초안 생성

저장 파일:

```text
outputs/{run_id}/ai_usage_log.jsonl
```

프롬프트 원문 또는 템플릿 버전도 레포에 저장한다.

```text
src/research/prompts/candidate_analysis_v1.txt
src/research/prompts/hypothesis_candidate_v1.txt
src/strategy/prompts/strategy_translation_v1.txt
src/backtest/prompts/result_explanation_v1.txt
```

# 13. 파이프라인 상태 변경

전체 실행 상태를 다음처럼 구분한다.

```text
DATA_READY
CANDIDATE_ANALYSIS_READY
AWAITING_ANALYST_VIEW
ANALYST_VIEW_APPROVED
HYPOTHESIS_DRAFT
HYPOTHESIS_APPROVED
STRATEGY_DRAFT_READY
AWAITING_STRATEGY_REVIEW
STRATEGY_APPROVED
BACKTEST_COMPLETE
AWAITING_INTERPRETATION
COMPLETE
```

CLI나 Streamlit UI에서 현재 상태를 표시해라.

승인되지 않은 단계를 자동으로 건너뛰어 백테스트하지 않도록 한다.

단, 개발 및 자동화 테스트를 위해 다음 옵션은 둘 수 있다.

```bash
--use-fixture-analyst-view
--use-fixture-hypothesis
--auto-approve-for-test
```

이 옵션은 테스트 환경에서만 동작하고, 실제 실행 출력에는 자동 승인 여부가 명확히 기록되어야 한다.

# 14. CLI 변경

가능하다면 다음 명령을 추가하거나 기존 명령을 확장한다.

```bash
python -m src.app.cli generate-candidates \
  --run-id {run_id}
```

```bash
python -m src.app.cli create-analyst-view \
  --run-id {run_id} \
  --input analyst_view.json
```

```bash
python -m src.app.cli create-hypothesis \
  --run-id {run_id} \
  --input human_investment_hypothesis.json
```

```bash
python -m src.app.cli generate-strategy-draft \
  --run-id {run_id}
```

```bash
python -m src.app.cli approve-strategy \
  --run-id {run_id} \
  --review strategy_review.json
```

```bash
python -m src.app.cli backtest \
  --run-id {run_id}
```

```bash
python -m src.app.cli submit-interpretation \
  --run-id {run_id} \
  --input backtest_interpretation.json
```

```bash
python -m src.app.cli generate-report \
  --run-id {run_id}
```

# 15. Streamlit UI가 구현되어 있다면 변경

다음 화면 또는 단계가 보여야 한다.

## 화면 1. 기업 및 분석 기준일 입력

- 기업명
- 분석 기준일
- 분석 기간
- 분석 초점

## 화면 2. AI 분석 후보 검토

- 재무 변화 후보
- 산업 변화 후보
- 촉매 후보
- 위험 후보
- 상충 근거
- 선택 체크박스
- 제외 체크박스

## 화면 3. 본인의 분석 관점 작성

- 분석 질문
- 핵심 논지
- 근거 선택 이유
- 제외 이유
- 예상 메커니즘
- 반대 논리
- 불확실성

## 화면 4. 투자 가설 작성

- 가설
- 경제적 근거
- 변수
- 예상 방향
- 보유기간
- 반증 조건
- 한계
- 승인 버튼

## 화면 5. 전략 초안 검토

- AI가 변환한 진입 규칙
- AI가 변환한 청산 규칙
- 체결 시점
- 임계값 수정
- 수정 이유 입력
- 승인 버튼

## 화면 6. 백테스트 결과

- 성과지표
- 차트
- 거래내역
- 조건 제거 분석
- 벤치마크 비교

## 화면 7. 최종 해석

- 가설에 유리한 결과
- 가설에 불리한 결과
- 최종 판정
- 판정 이유
- 수정 가설
- 추가 검증

# 16. 보고서 생성 시 출처 표시

각 문단 또는 핵심 주장에 다음을 구분해 표시할 수 있어야 한다.

```text
[데이터 사실]
[AI 정리]
[사용자 해석]
[사용자 가설]
[Python 검증 결과]
[사용자 최종 판단]
```

최종 보고서에는 태그를 그대로 노출하지 않아도 되지만, 내부 데이터 모델에는 `authorship` 또는 `content_origin`을 저장한다.

```python
class AuthoredContent(BaseModel):
    content: str
    content_origin: str
    author: str | None
    source_ids: list[str]
    ai_usage_id: str | None
```

허용 `content_origin`:

```text
SOURCE_FACT
PYTHON_CALCULATION
AI_CANDIDATE
HUMAN_ANALYSIS
HUMAN_HYPOTHESIS
AI_DRAFT_HUMAN_APPROVED
HUMAN_INTERPRETATION
```

# 17. 기존 코드 마이그레이션 원칙

- 기존 DART·XBRL 수집 코드는 유지한다.
- 기존 재무 계산 코드는 유지한다.
- 기존 Evidence Store를 재사용한다.
- 기존 최종 `CompanyAnalysis`가 있다면 삭제하기보다 호환 계층을 둔다.
- 기존 AI 투자 가설 생성기는 `HypothesisCandidateGenerator`로 이름과 역할을 변경한다.
- 기존 전략 생성기는 승인된 인간 가설만 입력받도록 수정한다.
- 기존 백테스트 엔진은 수정 최소화한다.
- 기존 보고서 생성기는 새 산출물을 조합하도록 변경한다.
- 기존 출력 파일을 깨뜨려야 한다면 migration note를 작성한다.

# 18. 테스트 추가

다음 테스트를 반드시 추가한다.

## 모델 테스트

- AnalystView에 분석 질문이 없으면 실패
- 선택 근거가 Evidence Store에 없으면 실패
- 선택 근거와 제외 근거가 겹치면 실패
- 반대 논리가 없으면 실패
- 승인되지 않은 가설이 전략 생성기로 전달되면 실패
- 승인되지 않은 전략이 백테스트로 전달되면 실패
- REVISED 판정인데 수정 가설이 없으면 실패

## 역할 분리 테스트

- CandidateAnalysis가 최종 투자 의견 필드를 갖지 않는지 확인
- AI 출력만으로 HumanInvestmentHypothesis가 자동 승인되지 않는지 확인
- 사용자 승인 기록 없이 전략이 실행되지 않는지 확인
- AI draft와 final strategy 수정 이력이 저장되는지 확인

## 보고서 테스트

- 분석 질문 포함
- 핵심 논지 포함
- 선택·제외 근거와 이유 포함
- 반대 논리 포함
- 인간 작성 가설 포함
- AI와 인간의 역할 구분 포함
- 최종 가설 판정 포함

# 19. 문서 변경

다음 문서를 수정하거나 추가한다.

```text
docs/PROJECT_SPEC.md
docs/HUMAN_IN_THE_LOOP.md
docs/AI_ROLE_BOUNDARY.md
docs/OUTPUT_SCHEMA.md
docs/MILESTONES.md
README.md
```

`README.md`에는 프로젝트를 다음과 같이 설명한다.

```text
본 프로젝트는 AI가 투자 판단을 대신하는 시스템이 아니다.
DART·XBRL·시장 데이터를 구조화하고 분석 후보를 제시하되,
분석 관점과 투자 가설은 사용자가 직접 설정한다.
AI는 사용자의 가설을 실행 가능한 전략 규칙으로 변환하며,
Python 백테스트가 이를 검증한다.
최종 가설의 채택·수정·기각 역시 사용자가 결정한다.
```

# 20. 변경 후 최종 산출물

한 번의 실행에서 다음 파일이 생성되어야 한다.

```text
outputs/{run_id}/
├── run_manifest.json
├── evidence_manifest.json
├── candidate_analysis.json
├── hypothesis_candidates.json
├── analyst_view.json
├── human_investment_hypothesis.json
├── strategy_draft.json
├── strategy_review.json
├── strategy_spec.json
├── backtest_result.json
├── trade_log.csv
├── backtest_interpretation.json
├── ai_usage_log.jsonl
├── research_report.md
└── charts/
```

# 21. 구현 우선순위

다음 순서로 작업해라.

1. 현재 코드와 변경 명세의 gap 분석
2. 신규 Pydantic 모델 추가
3. AI 분석 출력을 CandidateAnalysis로 변경
4. AnalystView 입력·저장·검증 추가
5. HumanInvestmentHypothesis 입력·승인 추가
6. 전략 생성기가 승인된 가설만 받도록 변경
7. StrategyReview 및 수정 이력 추가
8. 백테스트 후 BacktestInterpretation 추가
9. 파이프라인 상태 및 승인 게이트 추가
10. 최종 보고서 구조 변경
11. 테스트 추가
12. 문서 업데이트
13. 전체 테스트 및 샘플 실행

# 22. 완료 후 보고 형식

수정이 끝나면 다음 내용을 보고해라.

1. 변경된 파일 목록
2. 새로 추가된 모델
3. 기존 구조에서 재사용한 부분
4. 삭제·이름 변경·마이그레이션된 부분
5. 파이프라인 전후 비교
6. 추가한 테스트와 실행 결과
7. 샘플 실행 명령
8. 샘플 출력 파일 경로
9. 아직 미구현이거나 임시 처리한 사항
10. 다음 구현 우선순위

중요 사항:

- 기존 구현을 불필요하게 전면 재작성하지 마라.
- AI가 본인의 시각과 투자 가설을 대신 작성하는 구조로 되돌리지 마라.
- 승인 게이트를 형식적으로만 두지 말고 실제 실행 흐름에서 강제해라.
- 인간 작성 데이터와 AI 생성 데이터를 명확히 구분해라.
- DART·XBRL 데이터 처리 및 Point-in-Time 원칙은 기존 명세대로 유지해라.
- 작업 중 발견한 기존 버그는 변경 범위와 직접 관련이 있다면 함께 수정하고 테스트를 추가해라.