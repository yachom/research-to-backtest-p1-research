# Wave 3b 구현 명세 — C1' 후보 생성 ∥ C2' 전략 초안 (2026-07-15)

> 발주: 메인 세션(D8). Wave 3a(core/llm·research/evidence — main 병합 완료)를 소비해
> AI 단계 2개를 **병렬 워크트리**로 실구현한다. LLM은 **저가형 Haiku**(configs/llm.yaml
> 핀, 변경 금지)·구독 OAuth. 정본: 1804 §4(프롬프트 제약 7)·§7·§8·§12, MILESTONES C1'·C2',
> docs/HUMAN_IN_THE_LOOP.md, docs/AI_ROLE_BOUNDARY.md, README §21(DSL)·§23.4(기본 전략).

## 0. 공통 규약

- CLAUDE.md §3 절대 규칙. 특히 **#3 AI/인간 저작 분리**(AI 산출물은 candidate/draft
  파일에만), **#2 승인 게이트**(기존 게이트 코드 유지·약화 금지), **#4 토큰 비노출**.
- 사용 가능한 기반(전부 main에 있음 — 먼저 읽을 것):
  - `core/llm`: `create_llm_client(config, settings)`, `complete_validated(client,
    system_prompt=, user_prompt=, validator=, max_attempts=)`(JSON 재시도·코드펜스 처리),
    `load_prompt(dir, name, version)` → `PromptTemplate.render(**kw)`,
    `FakeLlmClient`(testing), `LlmCallMetadata`, `load_llm_config()`
  - `research/evidence`: `build_financial_evidence(corp_code, as_of=, data_dir=,
    lookback_years=, fs_scope=)` → `EvidencePackage`, `EvidencePackageStore(run_dir)`
  - `core/hitl`: RunStore(run_manifest 포함)·states·gates·AIUsageRecord
- **모든 LLM 호출 직후 AIUsageRecord를 `store.append_ai_usage`로 기록**(1804 §12 — 과제 2
  증빙). 필드 규약: `usage_id=f"usage-{stage}-{KST YYYYMMDDHHMMSS}"`, `model=metadata.model`,
  `prompt_version="v1"`, `human_review_required=True`, `ai_role`은 AI_ROLE_BOUNDARY 문구
  ("후보 정리" / "가설 후보 제시" / "전략 초안 변환"), input/output_artifact_ids는 실제 파일명.
- LLM 산출 JSON에서 신뢰 필드를 받지 않는다: `generated_by`·`prompt_version`·저작 관련
  값은 **코드가 주입**(LLM 출력에 있으면 무시하고 덮어씀).
- 프롬프트 v1 파일은 한국어, **1804 §4 제약 7개를 문면 그대로 포함**, "설명 없이 유효한
  JSON만 출력" 지시 포함. `str.format` 플레이스홀더는 PromptTemplate.render와 정합.
- 종료 코드·상태 표시 2줄 포맷은 CLI-integration §3을 그대로 따른다.
- mypy strict·ruff 100·한국어 docstring(명세 § 참조). 새 pip 의존성·새 공용 예외 금지.

## 1. 파일 소유권

### C1' — 후보 생성 (모델: **Opus**, 브랜치 `w3b-c1-candidates`)

- **신규**: `research/candidates/__init__.py`, `research/candidates/generator.py`,
  `research/prompts/candidate_analysis_v1.txt`, `research/prompts/hypothesis_candidate_v1.txt`,
  `tests/unit/research/test_candidates.py`, `tests/integration/test_candidates_live.py`
- **수정**: `app/commands/hitl_flow.py` — 허용 구역: ① `generate_candidates` 함수 본문
  ② `create_run` 로직의 재사용 함수 추출(`_create_run_impl(...) -> run_id str` — CLI 출력·
  옵션 동작은 불변) ③ 이에 필요한 import 추가. **다른 함수·register()·공통 헬퍼 수정 금지.**
- **수정**: `app/cli.py` — `research` 스텁 실구현(§2.3)만. 다른 명령 금지.
- **수정**: `tests/unit/test_cli_hitl.py` — **generate-candidates 스텁 테스트(현재 780~797행
  부근)만** 교체. generate-strategy-draft·generate-report 테스트 구역은 절대 건드리지 말 것.
  `tests/unit/test_cli.py`의 research 스텁 테스트(exit 2)도 교체 소유.
- **금지**: `core/`·`quant/` 수정, `research/evidence/` 수정(호출만), E1·L1 테스트 수정.

### C2' — 전략 초안 (모델: **Sonnet**, 브랜치 `w3b-c2-strategy-draft`)

- **신규**: `quant/strategy/draft.py`, `quant/prompts/strategy_translation_v1.txt`,
  `tests/unit/strategy/test_draft.py`, `tests/unit/test_cli_strategy_draft.py`,
  `tests/integration/test_strategy_draft_live.py`
- **수정**: `app/commands/hitl_flow.py` — 허용 구역: `generate_strategy_draft` 함수 본문 +
  필요한 import 추가만. **다른 함수·register() 수정 금지.**
- **수정**: `tests/unit/test_cli_hitl.py` — **generate-strategy-draft 스텁 테스트(현재
  800~812행 부근)만** 삭제(대체 테스트는 신규 `tests/unit/test_cli_strategy_draft.py`에).
  다른 구역 절대 금지.
- **금지**: `app/cli.py`, `core/`·`research/` 수정(호출만), `quant/strategy/` 기존 파일
  (schema·compiler·registry·indicators) 수정, `quant/backtest/`.

공통: 두 트랙 다 hitl_flow.py와 test_cli_hitl.py를 만진다 — **자기 구역 밖을 건드리거나
파일을 재포맷하면 병합을 거부한다**. import 블록 충돌은 병합 시 메인이 해소한다.

## 2. C1' 명세

### 2.1 생성기 (`research/candidates/generator.py`)

```python
def select_evidence_for_prompt(package: EvidencePackage, *, max_evidence: int = 60) -> list[FinancialEvidence]:
    """significance 내림차순, 동률은 evidence_id 오름차순 — 결정적 상위 선택."""

def generate_candidate_analysis(
    package: EvidencePackage, *, client: LlmTextClient, prompts_dir: Path,
    max_attempts: int, max_evidence: int = 60,
) -> tuple[CandidateAnalysis, LlmCallMetadata]: ...

def generate_hypothesis_candidates(
    package: EvidencePackage, analysis: CandidateAnalysis, *, client: LlmTextClient,
    prompts_dir: Path, max_attempts: int, max_evidence: int = 60,
) -> tuple[list[HypothesisCandidate], LlmCallMetadata]: ...
```

- **validator(재시도 루프에 주입)** — CandidateAnalysis: `model_validate` + **모든
  evidence_ids(Finding 전 리스트·RelationshipCandidate의 evidence/counter·
  conflicting_evidence)가 프롬프트에 제공한 evidence 부분집합에 존재**해야 한다. 위반
  ID 목록을 오류 메시지로 만들어 재시도 피드백이 되게 한다(1804 §4-2·3의 기계적 강제).
- hypothesis_candidates: 최상위 list(1~5개), 각 원소 필수 필드는 HypothesisCandidate에서
  `generated_by`·`prompt_version`을 **뺀** 부분 — 이 둘은 코드가 metadata.model·"v1"로 주입.
  evidence_ids/counter_evidence_ids 실존 검증은 동일. `measurable_variables`는 검증하지
  않는다(참고용 후보 — 단, 프롬프트에 A5 지원 지표 목록을 제공해 유도한다.
  `quant.strategy.registry`의 FINANCIAL/PRICE/FLOW_INDICATORS를 코드로 렌더).
- 프롬프트 입력: 기업명·as_of·evidence를 JSON으로 직렬화(Decimal→str), CandidateAnalysis
  스키마 설명(필드·타입·한국어 작성 지시), "복수 후보 제시"(§4-7)·"상충 근거 포함"(§4-5).

### 2.2 `generate-candidates` CLI 실구현 (hitl_flow.py)

- 옵션 추가: `--lookback-years`(기본 5).
- 절차: run_state·run_manifest 로드 → **상태 정책**: `DATA_READY`=정상 경로(마지막에 전진
  2회: →CANDIDATE_ANALYSIS_READY→AWAITING_ANALYST_VIEW, actor="system", note에 모델·건수);
  `CANDIDATE_ANALYSIS_READY`·`AWAITING_ANALYST_VIEW`=재생성(전이 없음, "재생성 — 상태 전이
  없음" 경고); `ANALYST_VIEW_APPROVED` 이상=exit 4(이후 산출물이 이전 후보에 기반하므로
  거부, 새 run 안내) → evidence 빌드(`build_financial_evidence(corp_code,
  as_of=manifest.as_of_date, data_dir=settings.data_dir, lookback_years=…)`) →
  `EvidencePackageStore(run_dir).save` → `create_llm_client(load_llm_config(), settings)` →
  분석 생성·저장 → 후보 생성·저장 → AIUsageRecord 2건 → 상태 전이 → 출력.
- 출력: evidence 건수·manifest 경로, findings 카테고리별 건수·relationship·conflicting·
  missing_information 건수, 후보 제목 목록, LLM 메타(모델·시도수·토큰) 테이블, 상태 2줄.
- 오류 매핑: ConfigError→3(LLM 인증 없음 포함), DataValidationError(재시도 소진 포함)→1,
  ApprovalGateError→4. FileNotFoundError(A4 산출물 없음)→1 + build-financials 안내.

### 2.3 `research` CLI 실구현 (cli.py)

- 형태 유지(README §26.5): `research --company --as-of-date [--lookback-years 5]`.
- 동작 = `_create_run_impl(...)`로 run 생성 → `generate-candidates` 로직 호출(동일 함수
  재사용 — hitl_flow에서 import) → 마지막에 "다음 단계: create-analyst-view" 안내.
  이미 존재하는 run을 찾지 않는다(항상 새 run — v2 흐름의 시작점, docstring에 명시).
- `_not_implemented` 헬퍼는 이제 사용처가 없으면 제거 가능(C3' generate-report는
  hitl_flow 소관이므로 cli.py에는 스텁이 남지 않는다).

### 2.4 live integration (`tests/integration/test_candidates_live.py`)

- 인증·실데이터 없으면 skip. 1케이스: 실데이터 evidence(00164779, as_of=2025-12-31)로
  `generate_candidate_analysis` 실호출 — CandidateAnalysis 검증 통과·evidence_ids 전
  실존·finding ≥ 1 확인. (후보 생성까지 포함하면 호출 2회 — 허용.)

## 3. C2' 명세

### 3.1 초안 생성기 (`quant/strategy/draft.py`)

```python
def draft_strategy(
    hypothesis: HumanInvestmentHypothesis, *, stock_code: str, client: LlmTextClient,
    prompts_dir: Path, max_attempts: int,
) -> tuple[dict[str, object], LlmCallMetadata]: ...
```

- **게이트**: 함수 첫 줄에서 `gates.ensure_hypothesis_approved(hypothesis)` (방어선 중첩 —
  A6 runner 패턴).
- **validator(재시도 주입)**: dict → `parse_strategy_spec` → `compile_strategy`
  (StrategyValidationError 메시지를 피드백으로) + 추가 규칙: `universe.tickers ==
  [stock_code]`(불일치는 오류 피드백), `strategy_name` 비어있지 않음, `execution`은
  signal_time="close"·trade_time="next_open" 고정(README §22 룩어헤드 방지 — 다른 값이면
  오류 피드백).
- 프롬프트(`strategy_translation_v1.txt`): 가설 필드(thesis·economic_rationale·
  expected_mechanism·selected_variables·expected_direction·investment_horizon_days) +
  DSL JSON 구조 설명 + **지원 지표 목록**(registry 3분류 + `{base}_lagN` 표기법) +
  §23.4 예시 JSON(tests/fixtures/strategy/earnings_flow_breakout.json 내용 인라인) +
  제약: "가설의 selected_variables를 우선 사용", "임계값은 가설·상식 범위에서 제안하되
  최종 결정은 사용자 몫"(AI_ROLE_BOUNDARY — 초안일 뿐), "JSON만 출력".
- horizon 연결: `investment_horizon_days`를 max_holding_days 규칙으로 반영하도록
  프롬프트에 지시(검증은 하지 않음 — 초안 재량).

### 3.2 `generate-strategy-draft` CLI 실구현 (hitl_flow.py)

- 기존 게이트 2종(ensure_state_at_least(HYPOTHESIS_APPROVED)·ensure_hypothesis_approved)
  유지 → run_manifest에서 stock_code → `draft_strategy(...)` → `store.save_strategy_draft`
  → AIUsageRecord(stage="strategy_translation", input=["human_investment_hypothesis.json"],
  output=["strategy_draft.json"]) → **상태 정책**: `HYPOTHESIS_APPROVED`=전진 2회
  (→STRATEGY_DRAFT_READY→AWAITING_STRATEGY_REVIEW, actor="system");
  `STRATEGY_DRAFT_READY`·`AWAITING_STRATEGY_REVIEW`=재생성(전이 없음, 경고);
  `STRATEGY_APPROVED` 이상=exit 4(승인본 무효화 방지, 회귀는 approve-strategy 재승인 경로).
- 출력: 초안 JSON(indent=2, syntax 하이라이트 무방), LLM 메타 테이블, "검토·수정 후
  approve-strategy --review로 승인" 안내, 상태 2줄.

### 3.3 live integration (`tests/integration/test_strategy_draft_live.py`)

- 인증 없으면 skip. 1케이스: 픽스처 승인 가설(§23.4 변수 사용) → `draft_strategy` 실호출
  → parse+compile 통과·tickers 강제 확인.

## 4. 테스트 요구 (공통)

- unit은 `FakeLlmClient`로 — 재시도 경로(1차 위반 evidence_id → 피드백 프롬프트에 위반
  ID 포함 확인 → 2차 성공), 주입 필드 덮어쓰기(generated_by), 상태 정책 3분기(정상 전진·
  재생성 무전이·이후 상태 exit 4), AIUsageRecord가 실제 append되는지(jsonl 재로드).
- CLI 테스트에서 LLM 주입: hitl_flow가 클라이언트를 **함수 내부에서 생성**하면 테스트
  불가 — `monkeypatch.setattr`로 대체 가능한 모듈 수준 팩토리 참조를 쓰거나 얇은
  `_build_client()` 헬퍼를 자기 구역에 두고 테스트에서 patch(관례: 기존 테스트들의
  monkeypatch 스타일). 두 트랙 각자 자기 구역 헬퍼로(공유 금지).
- `make check` 클린(자기 워크트리 — .env 없으면 live는 skip). live 검증은 메인 레포
  `.env` 주입으로 별도 실행해 결과 캡처(토큰 값 출력 금지).

## 5. DoD

- [ ] `make check` 클린 + live 테스트 실호출 통과(각 1회 이상, Haiku)
- [ ] C1': 실데이터 E2E 캡처 — 임시 run에 `r2b generate-candidates` 실행,
      CandidateAnalysis findings 요약·후보 제목·AIUsageRecord 2건·상태 전이 확인
      (r2b create-run → generate-candidates, 워크트리 로컬 outputs)
- [ ] C2': 픽스처 run(HYPOTHESIS_APPROVED)에 `r2b generate-strategy-draft` 실행 캡처 —
      초안이 §21 화이트리스트·컴파일 통과, 상태 AWAITING_STRATEGY_REVIEW
- [ ] 소유권 준수(`git diff --stat main` — §1 목록만), 자기 구역 밖 무변경
- [ ] 커밋 트레일러: C1' `Co-Authored-By: Claude Opus <noreply@anthropic.com>` /
      C2' `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`
