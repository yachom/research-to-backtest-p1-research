# H1 구현 명세 — Human-in-the-Loop 기반 계층 (요구사항 v2, Wave 1 병렬 트랙)

- **요구 원문: `1804_FEEDBACK.md` (필독 — 특히 §4~§13, §16, §18, §22 중요사항)**
- 정본 문서(**필독**): docs/OUTPUT_SCHEMA.md(모델 필드 — 원문 대비 "구현 보강" 표시 포함, 그대로 구현), docs/HUMAN_IN_THE_LOOP.md(흐름·상태·게이트), docs/AI_ROLE_BOUNDARY.md(저작 구분)
- 목적: v2 요구의 데이터 모델·상태 머신·승인 게이트·산출물 저장소를 **LLM 없이** 완성한다. C1'·C2'(LLM 연동)와 A6(백테스트)가 이 계층 위에 얹힌다.
- 비범위: LLM 호출, CLI 연결(메인 세션), Streamlit, 보고서 생성, Evidence 실데이터 생성(C1')

## 0. 파일 소유권 (D8 병렬 규칙 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/core/hitl/**` (신규 패키지)
- `tests/unit/hitl/**` (자체 conftest 허용), `tests/fixtures/hitl/**`
- **금지**: `app/cli.py`, `core/exceptions.py`(**ApprovalGateError 이미 등록됨** — 사용만), `core/models.py`, 타 패키지, 공유 테스트 파일. 다른 에이전트가 `core/financials`·`quant/strategy`·`core/xbrl`을 병렬 작업 중이다.

## 1. 모듈 배치

```text
core/hitl/__init__.py
core/hitl/models.py       # OUTPUT_SCHEMA.md의 모델 전부 (pydantic, extra="forbid")
core/hitl/states.py       # PipelineState 12종 + 전이 규칙 + RunState
core/hitl/gates.py        # 승인 게이트 (예외 발생 함수들)
core/hitl/store.py        # outputs/{run_id}/ 산출물 저장소 + EvidenceStore
core/hitl/validation.py   # AnalystView·Hypothesis 검증 규칙
core/hitl/diff.py         # 전략 초안 vs 최종 diff → StrategyModification 목록
```

## 2. models.py

- docs/OUTPUT_SCHEMA.md §1~§8의 모델을 **필드·타입 그대로** 구현한다: Finding(주의: `category: str` — 원문 필드), RelationshipCandidate, CandidateAnalysis, AnalystView, HypothesisStatus(StrEnum 7종), HumanInvestmentHypothesis(원문 필드 `thesis`·`expected_mechanism` + 구현 보강 4필드), **HypothesisCandidate**(원문 §7), StrategyModification, StrategyReview, BacktestInterpretation, **AIUsageRecord**(원문 §12), AuthoredContent, ContentOrigin(StrEnum 7종).
- 모든 모델 `model_config = ConfigDict(extra="forbid")`.
- 시각 필드(created_at 등)는 원문대로 `str`(ISO8601, KST 권장) — 파싱 강제하지 않되 `now_kst_iso()` 헬퍼 제공.
- 구조적 제약은 모델 validator로:
  - Finding.evidence_ids ≥ 1 (프롬프트 제약 3의 모델화)
  - AnalystView: research_question·core_thesis 비어있지 않음, selected ≥ 2, counterarguments ≥ 1, 선택∩제외=∅
  - Hypothesis: falsification ≥ 1, `status==APPROVED ⇒ approved_by·approved_at 필수`, content_origin ∈ {HUMAN_HYPOTHESIS, AI_DRAFT_HUMAN_APPROVED}
  - StrategyReview: approved_by 비어있지 않음, modifications의 modified_by 비어있지 않음
  - BacktestInterpretation(원문 §10 필수 검증): decision_reason 비어있지 않음 / supporting·contradicting 중 최소 하나 비어있지 않음 / `hypothesis_decision==REVISED ⇒ revised_hypothesis 필수`
  - (외부 참조 검증 — Evidence 존재·변수 지원·백테스트 결과 존재 — 은 validation.py, §5)
- **역할 분리 보장**(원문 §18): CandidateAnalysis에 최종 투자 의견·추천 필드를 두지 않는다 — 필드 집합을 고정하는 테스트 포함.

## 3. states.py

```python
class PipelineState(StrEnum):   # HUMAN_IN_THE_LOOP.md §3의 12종, 선언 순서 = 전진 순서
    DATA_READY = ...
    ...
    COMPLETE = ...

class StateTransition(BaseModel):
    from_state: PipelineState | None    # 최초 진입은 None
    to_state: PipelineState
    actor: str                          # "user" | "system" | "test-fixture" 등
    at: str
    auto_approved: bool = False         # 테스트 플래그 사용 시 True (v2 §13)
    note: str | None = None

class RunState(BaseModel):
    run_id: str
    company: str
    as_of_date: str
    current_state: PipelineState
    transitions: list[StateTransition]
```

- 허용 전이: ① 선언 순서상 **바로 다음** 상태로 전진 ② 명시적 회귀 에지 —
  `ANALYST_VIEW_APPROVED→AWAITING_ANALYST_VIEW`(관점 수정), `HYPOTHESIS_APPROVED→HYPOTHESIS_DRAFT`(가설 수정),
  `STRATEGY_APPROVED→AWAITING_STRATEGY_REVIEW`(전략 재검토), `COMPLETE→AWAITING_INTERPRETATION`(해석 수정, REVISED 판정 후속).
  그 외 전이는 `ApprovalGateError`.
- `advance(run_state, to, *, actor, auto_approved=False, note=None) -> RunState` — 전이 검증 + 이력 append.
- `generate_run_id(company: str, now: datetime) -> str` — README §29 형식 `YYYYMMDD_HHMMSS_{회사명 영문/코드 슬러그}`.

## 4. gates.py — 승인 게이트 (AI_ROLE_BOUNDARY.md §3의 코드화)

```python
def ensure_hypothesis_approved(h: HumanInvestmentHypothesis) -> None
    # status != APPROVED 또는 approved_by/at 누락 → ApprovalGateError("승인되지 않은 가설은 전략 변환·백테스트에 전달할 수 없다")
def ensure_strategy_approved(review: StrategyReview | None) -> None
    # review 없음/approved_by 빈 값 → ApprovalGateError
def ensure_state_at_least(run_state: RunState, required: PipelineState) -> None
```

- 예외 메시지는 한국어로 원인·필요 조치를 명시.

## 5. validation.py — 외부 참조 검증

```python
class EvidenceStore(Protocol):
    def has_evidence(self, evidence_id: str) -> bool: ...

class FileEvidenceStore:   # outputs/{run_id}/evidence_manifest.json {"evidence": [{"evidence_id": ...}, ...]}
    @classmethod
    def from_manifest(cls, path: Path) -> "FileEvidenceStore": ...
```

- `validate_analyst_view(view, store)` — OUTPUT_SCHEMA §2 규칙 전부: 모델 자체 제약 + **선택 근거 ID가 store에 실존**. 위반은 항목별 메시지를 모아 `DataValidationError`.
- `validate_hypothesis(h, store, supported_variables: Collection[str])` — evidence 실존 + view 연결(view_id 비어있지 않음; view 객체 대조는 store가 아니라 호출부) + `selected_variables ⊆ supported_variables ∪ h.unsupported_variables` (위반 변수 나열). **supported_variables를 파라미터로 받아** A5 Indicator Registry와 결합하지 않는다(통합은 메인 세션).
- `approve_hypothesis(h, *, approved_by, now) -> HumanInvestmentHypothesis` — 검증 후 status=APPROVED·approved_by/at·updated_at 채운 사본 반환.

## 6. store.py — 산출물 저장소

- `RunStore(outputs_dir: Path, run_id: str)` — 경로 규약: `outputs/{run_id}/{파일명}` (HUMAN_IN_THE_LOOP.md §4 표의 파일명 그대로).
- save/load 쌍: run_state, candidate_analysis, analyst_view, hypothesis_candidates(list[RelationshipCandidate]), human_hypothesis, strategy_draft(dict — StrategySpec 결합은 통합 단계), strategy_review, backtest_interpretation. 저장 전 pydantic 검증, JSON은 `ensure_ascii=False, indent=2`.
- load는 없으면 `FileNotFoundError`가 아니라 `DataValidationError`(무엇을 먼저 해야 하는지 안내 포함).
- AI 후보와 인간 가설은 **파일부터 분리**돼 있음을 docstring으로 강조(v2 §7).

## 7. diff.py

```python
def diff_strategies(draft: dict, final: dict, *, modified_by: str) -> list[StrategyModification]
```
- dict·list 재귀 비교, dot-path(`entry.all[0].right`) 생성, 추가·삭제·변경 모두 기록(draft_value/final_value에 None 사용). reason은 빈 문자열로 두고 사용자가 채운다(CLI·UI 단계).
- 결정적 순서(경로 정렬).

## 8. 테스트 (`tests/unit/hitl/`, 전부 오프라인)

| 대상 | 케이스 |
|---|---|
| models | OUTPUT_SCHEMA 필드 왕복(json round-trip) / extra 거부 / APPROVED인데 approved_by 없음 → 검증 실패 / falsification 0개 거부 / 선택∩제외 중복 거부 / counterarguments 0개 거부 |
| states | 정상 전진 전체 경로(DATA_READY→…→COMPLETE) / 건너뛰기 거부 / 허용 회귀 4종 / 불허 회귀 거부 / auto_approved 기록 / transitions 이력 누적 |
| gates | 미승인 가설 → ApprovalGateError / 승인 가설 통과 / review 없음 → 차단 |
| validation | evidence 미존재 ID 검출(누락 ID 나열) / 미지원 변수 검출 / unsupported_variables 명시 시 통과 / approve_hypothesis가 사본 반환(원본 불변) |
| store | 전 산출물 save→load 왕복 / 미존재 load 안내 메시지 / AI 후보와 인간 가설 파일 분리 확인 |
| diff | 값 변경·필드 추가·삭제·리스트 원소 변경 각각 dot-path 정확 / 동일 dict → 빈 목록 / v2 예시(entry 조건 right 0.1→0.2) 재현 |

fixture: `tests/fixtures/hitl/`에 evidence_manifest 샘플 + AnalystView·가설 샘플 JSON(검증 통과 1 + 규칙별 위반 다수).

## 9. DoD

1. ruff·mypy strict·pytest 전부 통과 (본인 워크트리 전체 스위트; 타 트랙 integration은 데이터 없어 skip 정상 — A5는 실데이터 불필요라 전부 통과할 것)
2. OUTPUT_SCHEMA.md와 구현 모델 필드가 1:1 일치 (스스로 대조표 작성해 보고)
3. 승인 게이트 3종이 예외로 강제됨을 테스트로 증명
4. 12-상태 전이표(허용/불허)가 테스트로 고정됨
