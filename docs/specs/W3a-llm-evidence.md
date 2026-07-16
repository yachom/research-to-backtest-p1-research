# Wave 3a 구현 명세 — L1 (core/llm) ∥ E1 (Evidence Store) (2026-07-15)

> 발주: 메인 세션(D8). Wave 3의 기반 계층 2개를 **병렬 워크트리**로 구현한다.
> 파일이 완전히 분리되어 있어 상호 의존이 없다. Wave 3b(C1' 후보 생성기 ∥ C2' 전략
> 초안)가 이 두 계층을 소비한다.
> 정본 근거: MILESTONES D2(재개정 2026-07-15)·C1', README §18(Evidence)·§30.2(보안),
> docs/HUMAN_IN_THE_LOOP.md §5, docs/AI_ROLE_BOUNDARY.md, 1804_FEEDBACK.md §4·§12.

## 0. 공통 규약

- CLAUDE.md §3 절대 규칙 전부. 특히 **PIT 원칙**(E1)과 **키·토큰 비노출**(L1 — 토큰 값을
  로그·예외 메시지·repr·테스트 출력 어디에도 남기지 않는다).
- mypy strict·ruff(line 100)·ruff format 통과, 한국어 docstring + 명세 § 참조.
- 새 pip 의존성 금지 — `claude-agent-sdk`는 pyproject에 이미 추가됨(메인 세션, 0.2.119 설치 확인).
- 예외는 기존 `core/exceptions.py` 재사용. LLM 오류는 **`LlmError`가 필요하면 메인 세션에
  요청하는 대신 `DataValidationError`(응답 형식 위반)·`ConfigError`(설정)·httpx 예외 전파로
  해결한다** — 부족하면 최종 보고에 기록(코드로 새 공용 예외를 만들지 말 것).
- 커밋: 자기 브랜치, 한국어 제목, 트레일러는 T1/T2 관례(§8 참고 — L1은 Sonnet, E1은 Opus).

## 1. 파일 소유권

### L1 — LLM 공용 계층 (모델: **Sonnet**, 브랜치 `w3a-l1-llm`)

- **신규**: `src/research_backtest/core/llm/__init__.py`, `core/llm/config.py`,
  `core/llm/client.py`, `core/llm/json_call.py`, `core/llm/prompts.py`,
  `core/llm/testing.py`(FakeLlmClient), `configs/llm.yaml`,
  `tests/unit/llm/`(신규 디렉토리 전체), `tests/integration/test_llm_live.py`
- **금지**: `core/config.py` 수정(Settings의 llm 필드 3종은 이미 존재 — 읽기만),
  `core/exceptions.py`, `research/`, `quant/`, `app/`, 기존 테스트 파일, `docs/`

### E1 — Evidence Store (모델: **Opus**, 브랜치 `w3a-e1-evidence`)

- **수정**: `src/research_backtest/research/__init__.py`(docstring 갱신 허용)
- **신규**: `research/evidence/__init__.py`, `research/evidence/models.py`,
  `research/evidence/builder.py`, `research/evidence/store.py`,
  `tests/unit/research/`(신규 디렉토리 전체), `tests/integration/test_evidence_build.py`
- **금지**: `core/` 전체(읽기·호출만 — 특히 `core/financials`·`core/hitl` 수정 금지),
  `quant/`, `app/`, `configs/`, `docs/`

두 트랙 간 공유 파일 **없음**. 서로의 디렉토리를 만들지 말 것.

## 2. L1 — core/llm 명세

### 2.1 설정 (`configs/llm.yaml` + `core/llm/config.py`)

```yaml
# LLM 호출 설정 (MILESTONES D2 재개정 — Claude Agent SDK + 구독 OAuth)
provider: claude_agent_sdk # 폴백 openrouter는 예약만 (키 없음 — D2)
model: claude-haiku-4-5-20251001 # 저가형 고정 (사용자 지시, 2026-07-15) — 전체 ID로 핀(재현성)
max_turns: 1
max_attempts: 3 # JSON 파싱·검증 실패 재시도 상한
timeout_seconds: 120.0
```

- `LlmConfig`(pydantic, extra="forbid") + `load_llm_config(path=Path("configs/llm.yaml"))` —
  `quant/backtest/costs.py`의 로더 패턴(파일 부재·형식 오류 → ConfigError)을 따른다.
- provider는 `Literal["claude_agent_sdk", "openrouter"]` (Settings.llm_provider와 동일 값 공간).
  openrouter는 **구현하지 않는다** — factory에서 "폴백 미구현(D2 — 키 확보 시 추가)" ConfigError.

### 2.2 클라이언트 (`core/llm/client.py`)

```python
@dataclass(frozen=True)
class LlmCallMetadata:
    model: str            # 응답이 보고한 실제 모델 ID
    num_attempts: int     # 1이면 첫 시도 성공
    duration_ms: int      # 전체(재시도 포함)
    input_tokens: int | None
    output_tokens: int | None
    cost_usd_notional: float | None  # 구독 인증 시 명목값 — 기록용

class LlmTextClient(Protocol):
    def complete_text(self, *, system_prompt: str, user_prompt: str) -> tuple[str, LlmCallMetadata]: ...
```

- **ClaudeAgentSdkClient**(LlmTextClient 구현):
  - 생성 인자: `LlmConfig`, `Settings`. **인증 주입**: `settings.anthropic_api_key`와
    `settings.claude_code_oauth_token`이 **둘 다 비어있지 않으면 ConfigError**(D2 — 의도치
    않은 API 과금 방지). 하나만 있으면 해당 값을 `os.environ`에 주입(이미 설정돼 있으면
    보존). 둘 다 비면 ConfigError("LLM 인증 없음 — .env에 CLAUDE_CODE_OAUTH_TOKEN 설정").
  - 내부: `claude_agent_sdk.query()` + `ClaudeAgentOptions(model=config.model,
    max_turns=config.max_turns, allowed_tools=[], system_prompt=…)` — **도구 사용 금지 고정**
    (AI_ROLE_BOUNDARY: LLM은 텍스트 정리만). 프로젝트는 sync 코드베이스이므로 내부에서
    `asyncio.run()`으로 감싼 sync 메서드로 노출한다.
  - **스모크 실측 반영(2026-07-15)**: ① AssistantMessage가 여러 개 오며 첫 개는 빈 텍스트일
    수 있다 — 전체 Assistant 텍스트를 이어붙이되 `ResultMessage.result`가 있으면 그것을
    우선한다. ② 모델은 `"1+1"` 요청에도 마크다운 코드펜스(```json … ```)로 감싼다 —
    JSON 추출(§2.3)에서 처리. ③ ResultMessage: is_error·num_turns·duration_ms·usage
    (input_tokens/output_tokens)·total_cost_usd 제공 — LlmCallMetadata에 채운다.
  - `is_error=True`면 DataValidationError(결과 텍스트 포함하되 **토큰·env 값이 포함될 수
    있는 내용은 그대로 노출하지 않도록** 메시지 앞부분 500자 절단).

### 2.3 JSON 강제 호출 (`core/llm/json_call.py`)

```python
def complete_validated[T](
    client: LlmTextClient,
    *,
    system_prompt: str,
    user_prompt: str,
    validator: Callable[[object], T],   # 예: SomeModel.model_validate / 리스트 검증 함수
    max_attempts: int,
) -> tuple[T, LlmCallMetadata]: ...
```

- 루프: 호출 → `extract_json(text)` → `json.loads` → `validator(payload)`. 어느 단계든
  실패하면 **오류 요지를 덧붙인 재요청 프롬프트**(원 요청 + "이전 응답의 문제: {오류}.
  코드펜스·설명 없이 유효한 JSON만 출력하라")로 재시도, `max_attempts` 소진 시
  DataValidationError(마지막 오류 포함).
- `extract_json(text: str) -> str`: ① ```json … ``` / ``` … ``` 코드펜스 내부 우선
  ② 없으면 첫 `{`~마지막 `}` (또는 첫 `[`~마지막 `]`) 슬라이스 ③ 그래도 없으면 원문.
  결정적·순수 함수, 단위 테스트 필수(스모크 실측 케이스 포함).
- 반환 metadata는 **누적**(num_attempts=총 시도, duration_ms=합, 토큰=합산 가능하면 합산).

### 2.4 프롬프트 버전 로더 (`core/llm/prompts.py`)

- 규약(HITL §5): 프롬프트는 `{name}_v{version}.txt` 파일. 로더:
  `load_prompt(dir_path: Path, name: str, version: int) -> PromptTemplate`.
- `PromptTemplate(name, version, text)` + `render(**kwargs) -> str`: `str.format` 기반,
  **누락 변수는 KeyError를 DataValidationError로 변환**(프롬프트-코드 불일치 조기 발견),
  잉여 kwargs는 오류. `prompt_version` 문자열 표현은 `"v{version}"` (AIUsageRecord용).
- 파일이 없으면 ConfigError(경로 포함). 실제 프롬프트 파일은 Wave 3b가 작성한다 —
  L1 테스트는 tmp_path 픽스처 파일로.

### 2.5 FakeLlmClient (`core/llm/testing.py`)

- `FakeLlmClient(responses: Sequence[str])` — complete_text가 순서대로 소비, 소진 시
  AssertionError. 호출 기록(`calls: list[tuple[str, str]]`) 보존. metadata는 고정값
  (model="fake", tokens None). Wave 3b 에이전트들과 자체 테스트가 사용하는 공식 테스트 더블.

### 2.6 live integration 테스트 (`tests/integration/test_llm_live.py`)

- Settings에 인증이 없으면 skip(기존 integration 테스트의 skip 관례를 읽고 동일하게).
- 1케이스: Haiku로 `complete_validated` 실호출 — `{"ok": true}` 스키마(pydantic 모델)
  강제, metadata.model이 config.model로 시작하는지·num_attempts ≥ 1 확인. 호출은 1회로
  최소화(구독 rate limit 고려).

### 2.7 L1 DoD

- [ ] `make check` 클린(live 테스트는 .env 인증으로 실제 1회 호출 포함)
- [ ] extract_json 단위 테스트: 코드펜스·이중 텍스트·비JSON 실패 케이스
- [ ] 재시도 루프 단위 테스트: Fake로 1차 비JSON → 2차 성공, 소진 시 예외
- [ ] 인증 규칙 테스트: 둘 다 설정 → ConfigError, 둘 다 없음 → ConfigError (env 오염 없이 Settings 주입)
- [ ] 토큰 값이 출력·예외·로그에 없음(테스트에서 확인 가능한 범위) — 최종 보고에 확인 방법 기록

## 3. E1 — Evidence Store 명세

### 3.1 배경·원칙

README §18: "Python이 산출·검증한 수치만 Evidence로 만들고, LLM은 재계산하지 않는다."
Evidence는 **결정적 Python 계산**의 산물이다(content_origin 성격: PYTHON_CALCULATION).
1804 §4 제약 2·3·6(evidence package 밖 사실 금지, evidence_id 연결, 추정 금지)의 공급자
측 기반. **PIT**: `available_from <= as_of`인 행만 사용(절대 규칙 #1) — as_of 이후에
접수된 공시의 수치는 evidence에 절대 포함되지 않는다.

### 3.2 모델 (`research/evidence/models.py`) — README §18.2 그대로 + 구현 보강

```python
class FinancialEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str            # "FIN_{METRIC|ACCOUNT}_{PERIOD}" 결정적 규약, 패키지 내 유일
    category: str               # §3.4 분류
    statement: str              # 한국어 서술 1문장 — Python 템플릿 생성(LLM 아님)
    current_value: Decimal | None
    comparison_value: Decimal | None
    change_rate: float | None
    period: str                 # 예: "2025Q1", "FY2024"
    comparison_period: str | None
    source_fact_ids: list[str]
    rcept_no: str
    filing_date: str            # rcept_dt ISO
    significance_score: float   # [0,1]

    # --- 구현 보강 (README 모델에 없음 — 사유를 docstring에) ---
    fs_scope: str               # "CFS" (MVP 기본 — §3.3)
    available_from: str         # PIT 검증 근거 보존 (ISO)
```

`EvidencePackage(BaseModel)`: corp_code, as_of_date, lookback_years, generated_at,
evidence: list[FinancialEvidence] — 생성 파라미터의 재현성 보존.

### 3.3 빌더 (`research/evidence/builder.py`)

```python
def build_financial_evidence(
    corp_code: str,
    *,
    as_of: date,
    data_dir: Path,
    lookback_years: int = 5,
    fs_scope: str = "CFS",
) -> EvidencePackage: ...
```

- 입력: A4 산출물 — `financial_metrics.parquet`(실측: metric 4종 — revenue_yoy·
  operating_income_yoy·net_income_yoy·operating_margin), `quarterly_financials.parquet`·
  `annual_financials.parquet`(11개 registry 계정 wide). 파일 부재 → FileNotFoundError
  (build-financials 안내 문구 포함).
- **필터(순서 고정)**: ① `fs_scope` 일치 ② `available_from <= as_of` ③ `period_end`가
  `as_of - lookback_years`년 이후. 필터 후 0행이면 DataValidationError(안내 포함).
- **evidence 생성 규칙**(전부 결정적, LLM·난수 금지):
  1. **metric evidence**: 필터된 metrics 각 행 → 1건. change_rate=value(YoY류),
     operating_margin은 current_value=value·직전 동분기 값을 comparison으로.
  2. **계정 수준 evidence**: wide 프레임에서 주요 계정(영업이익·순이익·매출·부채·재고 등
     registry 계정)의 ① 부호 전환(적자↔흑자) ② 전년 동기 대비 증감률(E1이 직접 계산 —
     단, A4 metrics에 이미 있는 지표와 중복 생성 금지) ③ 최근 연간 추세(연속 증가/감소
     3기 이상). **DSL 지표(quant/strategy/registry)의 재구현은 금지** — evidence 파생은
     분석 서술용이지 백테스트 지표가 아니다(경계를 docstring에 명시).
  3. 각 evidence의 rcept_no·filing_date·available_from은 **근거 행에서 그대로**;
     comparison이 다른 행이면 source_fact_ids에 두 행 모두 기록.
- **source_fact_ids 규약**: normalized_facts에 fact_id 컬럼이 없으므로
  `"FACT_{account_id|metric_id}_{fs_scope}_{fiscal_year}Q{fiscal_quarter|A}"` 결정적 생성
  (구현 보강 — 규약을 models.py docstring에 기록).
- **significance_score**: 결정적 규칙 — |change_rate| 단조 증가 + 부호 전환 가점 +
  최근성 가중(as_of에 가까울수록 ↑), [0,1] 클램프. 구체 수식은 에이전트 설계 재량이되
  ① 결정적 ② 문서화(docstring) ③ 단위 테스트로 경계 검증(0·1 클램프, 단조성)이 요건.
- **evidence_id 유일성**: 패키지 내 중복 시 DataValidationError(생성 규칙 버그를 조기 검출).
- **statement 템플릿**: README §18.2 예시 문체("…이 전년 동기 대비 개선되었다") —
  방향(개선/악화/전환)·기간·수치를 포함한 한국어 1문장.

### 3.4 category (구현 보강 — README는 예시 "PROFITABILITY"만 제공)

`GROWTH`(yoy류) · `PROFITABILITY`(operating_margin·이익 수준) · `STABILITY`(부채·자본) ·
`CASH_FLOW`(영업·투자 현금흐름) · `SCALE`(매출·자산 규모 추세) — StrEnum으로 고정,
매핑 표를 models.py에 상수로.

### 3.5 저장 (`research/evidence/store.py`)

```python
class EvidencePackageStore:
    def __init__(self, run_dir: Path) -> None: ...
    def save(self, package: EvidencePackage) -> tuple[Path, Path]:
        """evidence_package.json + evidence_manifest.json 저장 (원자적 개념 — 둘 다 쓰거나 예외)."""
    def load(self) -> EvidencePackage: ...
```

- `evidence_manifest.json` 형식은 **core/hitl/validation.py의 FileEvidenceStore.from_manifest가
  읽는 형식과 반드시 호환**: `{"evidence": [{"evidence_id": "...", ...추가 필드 허용}]}` —
  manifest 항목에는 evidence_id·category·statement·significance_score를 넣는다(사용자
  브라우징용 요약). 상세 전문은 `evidence_package.json`(EvidencePackage 직렬화).
- run_dir 결합·CLI 연결은 Wave 3b(C1'-gen) — E1은 라이브러리 계층만.

### 3.6 비범위 (후속 기록)

- 공시 원문 분석(README §19.9 P1-09 — disclosure_sections·material_events)은 **이번 범위
  밖**(MVP는 재무 Evidence 중심, README §34 서사와 일치). industry/뉴스 evidence 없음 —
  LLM 프롬프트가 missing_information으로 처리하게 된다(1804 §4-6).
- OFS evidence, 시장(가격·수급) evidence — 후순위.

### 3.7 E1 DoD

- [ ] `make check` 클린
- [ ] 단위 테스트: PIT 경계(available_from == as_of 포함, as_of+1일 제외), lookback 절단,
      부호 전환 검출, significance 경계, evidence_id 유일성·결정성(같은 입력 → 같은 출력),
      manifest가 FileEvidenceStore.from_manifest로 실제 로드되는지(core/hitl 호출 검증)
- [ ] integration(실데이터): SK하이닉스 `as_of=2025-12-31`로 evidence ≥ 20건 생성,
      전 건 available_from ≤ as_of 검증, 카테고리 분포 출력을 최종 보고에 포함.
      추가로 `as_of=2023-06-30`(중간 시점)으로 재실행해 **미래 공시 evidence가 0건**임을 확인
- [ ] README §18.2 예시("FIN_OP_MARGIN_2025Q3" 형태)와 evidence_id·필드 호환

## 4. 진행 관례 (T1/T2와 동일 — D8)

- 워크트리에서 `git checkout -b {브랜치}` → `make install` → 베이스라인 `make check`
  (**주의: .env가 레포 루트에 없는 워크트리에서는 integration이 skip된다 — 정상.**
  실데이터·live 검증 시에만 메인 레포 `.env`를 `set -a && source … && set +a`로 주입하고
  `DATA_DIR=/Users/baemingyu/project/MC_investment_homework/data` 사용, 값 출력 금지).
- 명세와 실측이 다르면 조용히 우회하지 말고 해결 후 최종 보고에 "명세 이탈"로 기록.
- 최종 보고: 구현 요약(파일·줄수), 테스트 before/after, live/실데이터 검증 출력 캡처,
  명세 이탈 목록, 브랜치·커밋 해시.
- 커밋 트레일러: L1 `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` /
  E1 `Co-Authored-By: Claude Opus <noreply@anthropic.com>`.
