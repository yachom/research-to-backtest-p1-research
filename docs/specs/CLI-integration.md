# CLI 통합 패스 구현 명세 (2026-07-15)

> 발주: 메인 세션(D8). 정본 근거: 1804_FEEDBACK.md §13·§14, docs/HUMAN_IN_THE_LOOP.md §3·§5.1,
> docs/OUTPUT_SCHEMA.md §0~§8, README §26·§29, MILESTONES D3(콘솔 스크립트 `r2b`)·D8.
> 두 트랙이 **병렬 워크트리**로 진행한다 — 파일 소유권(§1)을 벗어나는 수정 금지.

## 0. 목표·비범위

CLAUDE.md §6-1의 CLI 통합 패스 전체:

1. 스텁 실구현: `parse-xbrl`(core/xbrl), `reconcile-financials`(core/reconciliation),
   `backtest`(quant/backtest.runner — **`--run-id` 기반으로 형태 변경**, 1804 §14)
2. 신규 `build-financials`(core/financials.pipeline)
3. HITL 명령 8종(1804 §14) + run 생성 진입점 `create-run`(구현 보강, §5.1) + 상태 표시(`status`·`runs`)
4. `collect-financials --include-xbrl` 실연결(B1 다운로더)

**비범위**: `research` 스텁 실구현(C1'), LLM 호출(C1'·C2'), 보고서 생성(C3'), Streamlit(C3'),
`--use-fixture-analyst-view`·`--use-fixture-hypothesis`·`--auto-approve-for-test` 플래그
(엔드투엔드 오케스트레이션 명령의 소관 — C 마일스톤에서 추가. 단 상태 전이 API의
`auto_approved` 인자는 H1에 이미 있으므로 CLI는 항상 `auto_approved=False`로 호출한다).
`generate-candidates`·`generate-strategy-draft`·`generate-report`는 **상태 인지형 스텁**(§5.7)까지만.

## 1. 파일 소유권 (병합 충돌 차단 — 위반 시 병합 거부)

### T1 — 데이터 파이프라인·백테스트 (모델: **Opus**, 브랜치 `cli-t1-data`)

- **수정**: `src/research_backtest/app/cli.py` — ① parse-xbrl·reconcile-financials·backtest
  스텁 함수 제거 ② 자기 모듈 register 호출 추가 ③ collect-financials `--include-xbrl` 실연결.
  기존 명령 본문(resolve-company·collect-financials·collect-market)과 `research` 스텁,
  `_not_implemented`·공용 헬퍼는 유지.
- **신규**: `src/research_backtest/app/commands/__init__.py`(§2 고정 내용 그대로),
  `app/commands/data_pipeline.py`, `app/commands/backtest_cmd.py`
- **테스트**: `tests/unit/test_cli.py`(ALL_COMMANDS 갱신, 스텁 테스트를 research로 교체),
  `tests/unit/test_cli_collect_financials.py`(--include-xbrl), 신규
  `tests/unit/test_cli_data_pipeline.py`, `tests/unit/test_cli_backtest.py`
- **금지**: `core/`·`quant/` 전체(읽기·호출만), `app/commands/hitl_flow.py`,
  `tests/unit/hitl/`, `docs/`, `configs/`

### T2 — HITL 워크플로 (모델: **Sonnet**, 브랜치 `cli-t2-hitl`)

- **수정**: `core/hitl/models.py`(RunManifest 추가 + `__all__`),
  `core/hitl/store.py`(save/load_run_manifest + `__all__`)
- **신규**: `src/research_backtest/app/commands/__init__.py`(§2 고정 내용 — T1과 **바이트 동일**해야
  병합이 무충돌), `app/commands/hitl_flow.py`, `tests/unit/hitl/test_run_manifest.py`,
  `tests/unit/test_cli_hitl.py`
- **금지**: `app/cli.py`(등록은 병합 시 메인 세션), `app/commands/data_pipeline.py`·`backtest_cmd.py`,
  기존 `tests/unit/test_cli*.py`, `core/hitl/{states,gates,validation,diff}.py` 수정(읽기·호출만),
  `quant/` 수정, `docs/`, `configs/`

### 공통 금지

`core/exceptions.py`(기존 예외로 충분: ApprovalGateError·DataValidationError·ConfigError·
XbrlParseError·StrategyValidationError·LookaheadError), 새 pip 의존성, `.env`·`data/`·`outputs/` 커밋.

## 2. `app/commands/__init__.py` 고정 내용 (두 트랙 동일 — 정확히 이대로)

```python
"""r2b CLI 서브커맨드 모듈 (docs/specs/CLI-integration.md).

각 모듈은 ``register(app: typer.Typer) -> None``을 노출하고, 루트 앱 등록은
``app/cli.py``(메인 세션)가 수행한다. 모듈 간 상호 import는 금지한다.
"""
```

## 3. 공통 규약

- **register 패턴**: 각 커맨드 모듈은 모듈 수준 `register(app: typer.Typer) -> None`으로
  `@app.command(...)` 등록을 수행한다(데코레이터를 register 안에서 적용하거나
  `app.command(...)(fn)` 호출). 커맨드 모듈은 **`app.cli`를 import하지 않는다**(순환 금지).
- **종료 코드** (각 모듈에 로컬 상수로 선언, 값 고정):
  `0` 성공 / `1` 실행·검증·데이터 오류 / `2` NOT_IMPLEMENTED / `3` 설정 오류(ConfigError) /
  **`4` 승인 게이트 차단(신설: `GATE_BLOCKED_EXIT_CODE = 4`, ApprovalGateError 전용)**
- **예외 → 종료 코드 매핑**: ConfigError→3(빨간 "설정 오류: …"), ApprovalGateError→4(메시지 그대로),
  DataValidationError·FileNotFoundError·DartApiError·DartTransportError·XbrlParseError·
  StrategyValidationError·LookaheadError→1(메시지 그대로). typer.BadParameter는 typer 기본(2)이
  아닌 **옵션 검증에만** 사용(기존 cli.py 관례).
- **API 키·자격증명을 출력·예외에 남기지 않는다**(README §30.2). rich Console 사용.
- **run-id 기반 명령의 상태 표시**(1804 §13 "CLI에서 현재 상태 표시"): 명령 처리 후 마지막에
  공통 포맷 두 줄을 출력한다 — T1·T2 동일 문자열 포맷:
  ```
  파이프라인 상태: {state.value}  (run: {run_id})
  다음 단계: {안내 1줄}
  ```
  다음 단계 안내 문구는 §6.3 표의 "다음 단계" 열을 그대로 사용한다.
- 시각은 `datetime.now(KST)` / `now_kst_iso()`. 날짜 옵션 파싱은 기존 `_parse_iso_date_option`
  패턴(BadParameter) 복제.
- 회사 식별: 각 모듈은 자체 `_resolve_corp(company, settings) -> DartCorporation` 헬퍼를
  자기 파일 안에 구현한다(load_corp_code_registry → registry.resolve; AMBIGUOUS 후보 테이블·
  NOT_FOUND 안내 후 exit 1 — cli.py `_resolve_or_exit`·`_print_resolve_failure`와 동일 동작).
  **T1·T2 파일 간 import 금지**이므로 중복을 허용한다(병합 후 통합 정리는 메인 세션 후속).
- 한국어 docstring + 이 명세 § 참조. mypy strict·ruff(line 100)·ruff format 통과.
- outputs 루트: `get_settings().outputs_dir` (이미 존재, 기본 `outputs/`).
- `configs/*.yaml` 로더는 레포 루트 실행 전제(기존 관례) — 파일 부재 시 해당 로더의 예외 매핑을 따른다.

## 4. T1 — 명령 명세

### 4.1 `build-financials` (`app/commands/data_pipeline.py`)

```bash
r2b build-financials --company "SK하이닉스" [--scopes CFS] [--scopes OFS]
```

- resolve → corp_code → `build_financial_datasets(corp_code, data_dir=settings.data_dir, scopes=…)`.
  scopes 파싱은 cli.py `_parse_scopes`와 동일 규칙(기본 CFS+OFS, 그 외 BadParameter) — 자기 모듈에 복제.
- 출력: ① 요약(scope, fact_count, 파일별 행수 4종) ② validations 각 항목 pass/fail
  ③ coverage 요약 ④ 저장 경로(`financials_out_dir(data_dir, corp_code)`)와 build_report.json.
- 실패 안내: raw jsonl 부재 → "r2b collect-financials 먼저", 캘린더 부재 → "r2b collect-market 먼저"
  (pipeline이 실제로 던지는 예외를 코드로 확인해 메시지에 매핑; 회계식·available_from 위반
  DataValidationError → exit 1 메시지 그대로).

### 4.2 `parse-xbrl`

```bash
r2b parse-xbrl --corp-code 00164779 --rcept-no 20250319001045
```

(README §26.3 형태 유지) — `xbrl_filing_dir(data_dir, corp_code, rcept_no) / EXTRACTED_DIRNAME`이
없으면 exit 1 + "r2b collect-financials --include-xbrl 로 원본을 먼저 수집" 안내.
`parse_extracted(extracted_dir)` → `store_parsed_xbrl(parsed, xbrl_normalized_dir(data_dir, corp_code, rcept_no))`.
출력: facts/contexts/units/dimensions 행수 테이블 + 저장된 parquet 경로. XbrlParseError → exit 1.

### 4.3 `reconcile-financials`

```bash
r2b reconcile-financials --company "SK하이닉스" [--year 2024] [--report annual] \
  [--scopes CFS] [--strict]
```

- **대조 자체는 항상 전량**(`reconcile_all` — 전 XBRL 파싱 보장 포함, 멱등). `--year`·`--report`는
  **표시 필터**로 옵셔널화한다(README §26.4는 필수처럼 보이나 B3 파이프라인이 전량 대조·저장
  구조이므로 필터로 재해석 — 이 결정을 커맨드 docstring에 기록). `--report`는 annual/half/q1/q3.
- 출력: by_status 분포, annual/quarterly match_rate, parse 요약(파싱 실패 있으면 목록),
  필터 지정 시 해당 레코드 상세 테이블, failures CSV 경로.
- 종료 코드: `CONTEXT_MISMATCH`+`SCOPE_MISMATCH`+`ACCOUNT_MAPPING_MISMATCH` > 0 → **1**;
  `--strict`이면 `PASSING_STATUSES` 밖 전부(>0) → **1**; 그 외 0.
  (현 실데이터 기대: 총 290 = 연간 70 MATCH + 분기 190 MATCH·30 REQUIRES_REVIEW →
  기본 exit 0, --strict exit 1. 테스트·docstring에 이 기대를 기록.)

### 4.4 `backtest` (`app/commands/backtest_cmd.py`) — 1804 §14 형태

```bash
r2b backtest --run-id 20260715_090000_SK_HYNIX \
  [--start-date 2016-01-01] [--end-date <기본: run as_of_date>] \
  [--fs-scope CFS] [--benchmark <기본: configs/backtest.yaml>]
```

- 기존 스텁(--hypothesis/--start-date/--end-date 필수형)을 **대체**한다. 기본값:
  `--start-date` 2016-01-01(README §26.6·정오표 #5의 데이터 경계), `--end-date` run_manifest의
  as_of_date, `--fs-scope` CFS.
- 절차(순서 고정):
  1. `RunStore(settings.outputs_dir, run_id)` → `load_run_state()` (부재 시 exit 1 + create-run 안내).
  2. `run_manifest.json`을 **json으로 직접 로드**(§6.1 계약 — T2의 모델에 의존하지 않는다.
     병렬 개발 격리; 필수 소비 필드 corp_code·stock_code·as_of_date, 나머지 무시. 부재·필드
     누락 시 exit 1 + create-run 안내).
  3. 상태 검사: `COMPLETE`면 **exit 4**("해석까지 완료된 실행은 재백테스트하지 않는다 — 새 run 권장").
     그 외 `gates.ensure_state_at_least(run_state, STRATEGY_APPROVED)` (미달 → exit 4).
  4. 가설 게이트(방어선 중첩): `load_human_hypothesis()` → `gates.ensure_hypothesis_approved` →
     review 로드 후 `review.hypothesis_id == hypothesis.hypothesis_id` 확인(불일치 exit 1).
  5. `load_strategy_review()` → `load_backtest_config()`(+ `--benchmark` 지정 시 `model_copy(update=…)`) →
     `execute_approved_strategy(review, data_dir=…, stock_code=…, corp_code=…, start_date=…,
     end_date=…, out_dir=store.run_dir, backtest_config=…, fs_scope=…)`
     (runner가 1차 게이트·산출물 3종 저장을 담당 — 파일명 A6 상수 = OUTPUT_SCHEMA §0과 일치).
  6. 상태 전이: 현재가 `STRATEGY_APPROVED`일 때만
     `advance(→BACKTEST_COMPLETE, actor="system", note=f"{start}~{end} {strategy_name}")` →
     `advance(→AWAITING_INTERPRETATION, actor="system", note="사용자 해석 대기")` → save.
     `BACKTEST_COMPLETE`·`AWAITING_INTERPRETATION`에서의 재실행은 전이 없이 산출물만 갱신
     + "재실행 — 상태 전이 없음" 경고 1줄.
- 출력: 성과 요약 테이블(cumulative_return·cagr·sharpe·max_drawdown·num_trades·win_rate·
  profit_factor·벤치마크 초과수익 — BacktestResult 필드명은 코드 확인), 산출물 경로 3종,
  공통 상태 표시 2줄(§3).
- LookaheadError → exit 1(메시지 그대로 — 치명 결함 신호이므로 절대 삼키지 않는다).

### 4.5 `collect-financials --include-xbrl` 실연결 (cli.py)

- 재무 수집 성공 후: `find_periodic_filings(client, corp.corp_code, as_of_date=오늘(KST),
  lookback_years=오늘.year - from_year + 1)` → `rcept_dt.year ∈ [from_year, to_year + 1]` 필터
  (FY 연간보고서는 이듬해 3월 접수 — 경계 포함) → `download_xbrl_filings(client, filings,
  data_dir=…, force=force_download, min_interval_seconds=…)`.
- 기존 "Milestone B1에서 구현" 경고 제거. 결과 테이블(rcept_no·보고서·결과·경로 or 사유) +
  결과별 건수 요약. `XbrlDownloadOutcome`의 결과 값 명칭(DOWNLOADED/CACHED/NO_DATA/SKIPPED/FAILED 등)은
  코드·`tests/integration/test_xbrl_pipeline.py` 관용을 그대로 따른다. FAILED > 0 → exit 1.

### 4.6 cli.py 등록·정리

```python
from research_backtest.app.commands.backtest_cmd import register as register_backtest
from research_backtest.app.commands.data_pipeline import register as register_data_pipeline
# app = typer.Typer(...) 선언 직후:
register_data_pipeline(app)
register_backtest(app)
```

(메인 세션이 병합 시 같은 위치에 `hitl_flow` 등록 2줄을 추가한다 — T1은 T2 모듈을 참조하지 않는다.)

## 5. T2 — 명령 명세 (`app/commands/hitl_flow.py`)

### 5.0 RunManifest (core/hitl/models.py + store.py)

OUTPUT_SCHEMA §0의 `run_manifest.json`(README §29 실행 메타)의 코드화 — H1 범위 밖이었던
구현 보강. **RunState와 역할 분리**: manifest = 불변 식별 정보, run_state = 진행 상태.

```python
class RunManifest(BaseModel):
    """실행 1건의 불변 메타 (README §29, OUTPUT_SCHEMA §0 run_manifest.json)."""
    model_config = ConfigDict(extra="forbid")

    run_id: str
    company_query: str            # 사용자가 입력한 질의 문자열
    corp_code: str                # DART 8자리
    corp_name: str
    corp_eng_name: str | None = None
    stock_code: str               # 6자리 — 상장사만 run 생성 허용
    as_of_date: str               # YYYY-MM-DD (분석 기준일)
    created_at: str               # KST ISO8601
    code_version: str | None = None  # git short hash, best-effort
```

- store.py: `save_run_manifest`/`load_run_manifest`("run_manifest.json", 기존 `_write_json`/
  `_read_json` 패턴, next_step_hint="create-run으로 실행을 먼저 등록하세요."). 두 파일 `__all__` 갱신.
- README §29의 config_hash·started_at·completed_at·status는 채택하지 않는다(상태·이력은
  run_state.json이 정본, config는 산출물 자체가 기록 — docstring에 사유 1줄).

### 5.1 `create-run` (구현 보강 명령)

```bash
r2b create-run --company "SK하이닉스" --as-of-date 2025-12-31
```

- 1804 §14의 8종에는 없지만 전부 `--run-id`를 받으므로 **run 생성 진입점이 필요**하다 —
  HUMAN_IN_THE_LOOP §2 "기업명·분석 기준일 입력" 단계의 코드화(커맨드 docstring에 이 근거 기록).
- 절차: resolve(§3 공통 — 키 없으면 exit 3) → 비상장이면 exit 1 →
  **데이터 준비 검사**(DATA_READY의 의미 담보): `data_dir/normalized/financials/{corp_code}/financial_metrics.parquet`,
  `data_dir/normalized/market/{stock_code}/daily.parquet`, 캘린더 parquet 존재 확인 —
  없으면 exit 1 + 부족한 것별 준비 명령 안내(build-financials·collect-market). 검사만 하고 수집을 트리거하지 않는다.
- `run_id = generate_run_id(corp.corp_eng_name or corp.corp_name, datetime.now(KST))` →
  RunManifest 저장(code_version은 `git rev-parse --short HEAD` subprocess best-effort, 실패 시 None) →
  `create_run_state(run_id, corp_name, as_of_date, actor="user")` → save → 출력: run_id·매니페스트 경로 +
  공통 상태 표시(다음 단계: generate-candidates — C1' 예정임을 안내).

### 5.2 `runs` · `status`

```bash
r2b runs                     # outputs/ 스캔 테이블: run_id·company·as_of_date·상태·마지막 전이 시각
r2b status --run-id <id>     # 현재 상태 + 전이 이력 테이블 + 산출물 체크리스트 + 다음 단계
```

- `runs`: `outputs_dir` 하위 디렉토리 중 run_state.json 있는 것만(없으면 건너뛰고 마지막에
  "run_state 없는 디렉토리 N개 무시" 1줄). outputs_dir 자체가 없으면 빈 테이블 + 안내, exit 0.
- `status`: 전이 이력(from→to·actor·at·auto_approved·note) 테이블 + OUTPUT_SCHEMA §0 파일
  체크리스트(✓/–; evidence_manifest·candidate_analysis 등 미래 산출물도 – 로 표시) + 상태 표시 2줄.

### 5.3 `create-analyst-view`

```bash
r2b create-analyst-view --run-id <id> --input analyst_view.json
```

- 허용 상태: `AWAITING_ANALYST_VIEW`(전진 1회) / `ANALYST_VIEW_APPROVED`(재제출 —
  회귀 `→AWAITING_ANALYST_VIEW` 후 재전진, advance 2회). 그 외 → exit 4(이전 단계면 순서 안내,
  이후 단계면 "이 단계로의 회귀는 허용되지 않음" 안내).
- `--input` JSON → `AnalystView.model_validate`(pydantic 오류 → exit 1, 필드 경로 포함 요약) →
  `validate_analyst_view(view, FileEvidenceStore.from_manifest(run_dir / "evidence_manifest.json"))`.
  evidence_manifest.json 부재 → exit 1 + "Evidence Store는 generate-candidates(C1')가 생성" 안내 —
  **게이트 약화 금지: 검증 생략 경로를 만들지 않는다.**
- `save_analyst_view` → advance(actor="user") → save → 상태 표시.

### 5.4 `create-hypothesis`

```bash
r2b create-hypothesis --run-id <id> --input human_investment_hypothesis.json
```

- 허용 상태: `ANALYST_VIEW_APPROVED` / `HYPOTHESIS_DRAFT` / `HYPOTHESIS_APPROVED`(회귀 재작성).
- 검증: `HumanInvestmentHypothesis.model_validate` → `view_id == 저장된 analyst_view.view_id`
  (불일치 exit 1) → supported_variables는
  `{v for v in h.selected_variables if resolve_indicator(v) 성공}`(A5 registry — lag 표기 지원,
  StrategyValidationError → 미지원으로 간주) → `validate_hypothesis(h, evidence_store, supported)`.
  evidence_manifest 부재 처리 §5.3과 동일.
- 상태 전이(입력 JSON의 status가 저작·승인 기록의 정본 — CLI --approve 플래그를 두지 않는다):
  - `status=DRAFT` 입력: 저장 후 `HYPOTHESIS_DRAFT`까지 전진(현재가 이미 HYPOTHESIS_DRAFT면
    전이 없이 갱신, HYPOTHESIS_APPROVED면 회귀 1회).
  - `status=APPROVED` 입력(모델이 approved_by/approved_at 강제): 저장 후 현재 상태에서
    `HYPOTHESIS_APPROVED`까지 필요한 만큼(1~2회) 전진.
  - 그 외 status(TESTED 등) 입력 → exit 1("작성 단계에서는 DRAFT/APPROVED만").

### 5.5 `approve-strategy`

```bash
r2b approve-strategy --run-id <id> --review strategy_review.json
```

- 허용 상태: `AWAITING_STRATEGY_REVIEW`(전진) / `STRATEGY_APPROVED`(회귀 재승인). 그 외 exit 4.
- 검증(순서 고정, 실패 시 exit 코드 명시):
  1. `StrategyReview.model_validate` (→1)
  2. 가설 게이트: `load_human_hypothesis` → `ensure_hypothesis_approved`(→4) →
     `review.hypothesis_id == hypothesis.hypothesis_id`(→1)
  3. `load_strategy_draft()` 존재 필수 + `review.llm_draft_strategy`와 dict 동등성(→1 —
     AI 초안 위변조 방지, AI_ROLE_BOUNDARY §3)
  4. diff 정합: `diff_strategies(review.llm_draft_strategy, review.final_strategy,
     modified_by=review.approved_by)`의 field_path 집합 == `review.modifications`의 field_path
     집합(→1, 누락·과잉 경로를 각각 출력 — 수정 이력 누락 방지, 1804 §9)
  5. A5 재검증: `parse_strategy_spec(review.final_strategy)` → `compile_strategy`
     (StrategyValidationError→1)
- 저장: `save_strategy_review` + **`strategy_spec.json`**(= final_strategy 그대로,
  OUTPUT_SCHEMA §0 "승인된 최종 전략" — store에 전용 메서드가 없으므로 hitl_flow가
  `store.run_dir / "strategy_spec.json"`에 indent=2로 기록) → advance → 상태 표시.

### 5.6 `submit-interpretation`

```bash
r2b submit-interpretation --run-id <id> --input backtest_interpretation.json
```

- 허용 상태: `AWAITING_INTERPRETATION`(전진→COMPLETE) / `COMPLETE`(회귀 재제출). 그 외 exit 4.
- 검증: `BacktestInterpretation.model_validate`(→1) → `hypothesis_id` 일치(→1) →
  `strategy_id == strategy_spec.json의 strategy_name`(파일 부재·불일치 →1).
- **가설 판정 반영**(1804 §10 "가설의 채택·수정·기각"): hypothesis_decision →
  HumanInvestmentHypothesis.status 갱신 매핑 — SUPPORTED/PARTIALLY_SUPPORTED/REJECTED/REVISED는
  동명 status, INCONCLUSIVE는 TESTED. `updated_at` 갱신, approved_* 유지, 재검증 후
  human_investment_hypothesis.json 재저장.
- 저장 → advance → 상태 표시("보고서 생성은 generate-report — C3' 예정" 안내).

### 5.7 상태 인지형 스텁 3종 (exit 2 전에 게이트·상태 검사를 **실제로** 수행)

- `generate-candidates --run-id`: run_state 로드(부재→1) → 이미 `CANDIDATE_ANALYSIS_READY`
  이상이면 "이미 생성된 실행" 경고 → exit 2 "C1'에서 구현(Evidence Store + CandidateAnalysis 생성기)".
- `generate-strategy-draft --run-id`: `ensure_state_at_least(HYPOTHESIS_APPROVED)`(→4) +
  `load_human_hypothesis` → `ensure_hypothesis_approved`(→4 — **게이트 ②를 스텁에서도 강제**) →
  exit 2 "C2'에서 구현".
- `generate-report --run-id`: `ensure_state_at_least(COMPLETE)`(→4) → exit 2 "C3'에서 구현".

### 5.8 등록

`register(app)` 하나로 위 10개 명령 전부 등록. cli.py 연결은 **하지 않는다**(메인 세션).

## 6. 인터페이스 계약 (트랙 간 접점)

### 6.1 run_manifest.json (T2 생산 → T1 소비)

§5.0 필드가 파일 계약이다. T1 `backtest`는 pydantic 의존 없이 `json.loads`로 읽고
corp_code·stock_code·as_of_date **세 필드만 필수 소비**(str 타입 검사, 그 외 키 무시 —
전방 호환). 병합 후 `load_run_manifest`로 통일하는 정리는 메인 세션 후속.

### 6.2 산출물 파일명

OUTPUT_SCHEMA §0 그대로. backtest 산출물 3종은 A6 runner 상수
(backtest_result.json·trade_log.csv·daily_portfolio.csv), strategy_spec.json은 §5.5.

### 6.3 명령 × 상태 전이표 (정본 요약)

| 명령 | 허용 진입 상태 | 성공 시 전이 | 다음 단계 안내 |
|---|---|---|---|
| create-run | (신규) | ∅ → DATA_READY | generate-candidates (C1' 예정) |
| generate-candidates | DATA_READY~ | [C1'] → CANDIDATE_ANALYSIS_READY → AWAITING_ANALYST_VIEW | create-analyst-view |
| create-analyst-view | AWAITING_ANALYST_VIEW, ANALYST_VIEW_APPROVED(회귀) | → ANALYST_VIEW_APPROVED | create-hypothesis |
| create-hypothesis | ANALYST_VIEW_APPROVED, HYPOTHESIS_DRAFT, HYPOTHESIS_APPROVED(회귀) | → HYPOTHESIS_DRAFT [→ HYPOTHESIS_APPROVED] | generate-strategy-draft (C2' 예정) |
| generate-strategy-draft | HYPOTHESIS_APPROVED~ | [C2'] → STRATEGY_DRAFT_READY → AWAITING_STRATEGY_REVIEW | approve-strategy |
| approve-strategy | AWAITING_STRATEGY_REVIEW, STRATEGY_APPROVED(회귀) | → STRATEGY_APPROVED | backtest |
| backtest | STRATEGY_APPROVED (재실행: BACKTEST_COMPLETE·AWAITING_INTERPRETATION, 전이 없음) | → BACKTEST_COMPLETE → AWAITING_INTERPRETATION | submit-interpretation |
| submit-interpretation | AWAITING_INTERPRETATION, COMPLETE(회귀) | → COMPLETE | generate-report (C3' 예정) |
| generate-report | COMPLETE | [C3'] 전이 없음 | — |
| status / runs | 전 상태 | 없음 | 상태별 §6.3 안내 재사용 |

## 7. 테스트 요구

- `typer.testing.CliRunner`. T2는 `typer.Typer()` 새 인스턴스에 `register(app)`로 구성해 테스트
  (cli.py 미접촉). T1은 실제 `app.cli:app` 대상.
- Settings 주입은 기존 CLI 테스트(test_cli_collect_market.py 등)의 관례를 따른다
  (env monkeypatch·get_settings 캐시 처리 방식 포함 — 먼저 읽고 동일 패턴 사용).
- 필수 케이스(명령당 최소): 정상 경로 1 + 실패 경로(게이트 exit 4 / 검증 exit 1 / 부재 exit 1) 1+.
  상태 전이는 run_state.json 재로드로 `current_state`·`transitions[-1].actor`·
  `auto_approved is False`까지 확인. 스텁 3종은 exit 2와 게이트 선검사(§5.7) 확인.
- T1 backtest 테스트: tmp_path에 합성 daily/metrics/calendar parquet + 승인 review·manifest
  픽스처로 게이트(미승인→4)·전이(2회)·재실행(전이 없음)·산출물 존재까지. 수치 검증은 A6
  테스트 소관이므로 반복하지 않는다. 합성 데이터 구성은 `tests/unit/backtest/`의 기존 픽스처
  패턴을 읽고 재사용(수정 금지).
- 워크트리에서 `make check` 클린(ruff+format+mypy strict+pytest unit 전부, 기존 테스트 무손상).

## 8. 진행 관례 (D8)

- 워크트리에서 자체 `.venv` 생성: `python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"`.
- 실데이터 스모크(선택이 아니라 **DoD**): `DATA_DIR=/Users/baemingyu/project/MC_investment_homework/data`
  (읽기 전용으로 취급 — T1 build-financials·reconcile은 data/ 하위에 재산출하므로 허용, 그 외 쓰기 금지).
  DART 키 필요 시 메인 레포 `.env`를 `set -a && source …/.env && set +a`로 주입(키 값 출력 금지).
  outputs 스모크는 워크트리 로컬 `outputs/`(커밋 금지)에.
- 명세와 실측이 다르면 **조용히 우회하지 말고** 최종 보고에 "명세 이탈" 항목으로 사유와 함께 기록.
- 커밋: 자기 브랜치에 한국어 제목, 마지막 줄 `Co-Authored-By: Claude Opus <noreply@anthropic.com>`
  (T1) / `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>` (T2).

## 9. DoD

- [ ] `make check` 클린(각 워크트리) — 기존 테스트 전부 통과 + 신규 테스트
- [ ] T1: 실데이터 스모크 — build-financials(136 metrics 재산출)·parse-xbrl(1건)·
      reconcile-financials(총 290, 연간 70 MATCH 재현, 기본 exit 0·--strict exit 1)·
      backtest(픽스처 run으로 게이트→실행→전이) 출력 캡처를 최종 보고에 포함
- [ ] T2: create-run 실데이터 스모크(run 디렉토리·manifest·DATA_READY) + 픽스처 전이 체인
      (create-analyst-view→…→submit-interpretation, evidence_manifest 픽스처 사용) 출력 캡처
- [ ] 상태 표시 공통 포맷(§3) 준수 — 두 트랙 동일 문자열
- [ ] 브랜치 커밋 완료(§8 트레일러), 소유권(§1) 밖 파일 무변경(`git diff --stat main` 확인)
