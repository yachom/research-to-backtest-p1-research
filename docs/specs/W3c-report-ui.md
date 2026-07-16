# Wave 3c 구현 명세 — R1 보고서·강건성 ∥ S1 Streamlit (2026-07-15)

> 발주: 메인 세션(D8). 마지막 기능 웨이브 — 이후는 문서 재편·제출물(메인 세션).
> 정본: docs/HUMAN_IN_THE_LOOP.md §6(15섹션)·§8(7화면), 1804 §15(화면 상세)·§16(출처 표시),
> README §24.2~24.3(강건성), docs/OUTPUT_SCHEMA.md §8(ContentOrigin·AuthoredContent),
> docs/AI_ROLE_BOUNDARY.md. 문서 재편(§25·정오표 6)과 README 재작성은 **메인 세션이 병행**
> — 에이전트는 README.md·docs/ 수정 금지.

## 0. 공통 규약

- CLAUDE.md §3 절대 규칙. 기존 규약(mypy strict·ruff 100·한국어 docstring·명세 § 참조·
  종료 코드·상태 표시 2줄·AIUsageRecord 규약은 W3b §0) 그대로.
- 사용 기반: core/llm(complete_validated·load_prompt·FakeLlmClient), core/hitl 전 계층,
  research/evidence, research/candidates, quant/strategy(draft 포함), quant/backtest
  (execute_approved_strategy·runner·metrics·costs), app/commands/hitl_flow.py의 공용 헬퍼.
- streamlit 1.59는 pyproject에 추가됨(메인). **새 의존성 추가 금지.**
- LLM 모델·설정은 configs/llm.yaml 경유(수정 금지).

## 1. 파일 소유권

### R1 — 보고서·강건성·generate-report (모델: **Opus**, 브랜치 `w3c-r1-report`)

- **신규**: `quant/backtest/robustness.py`, `research/report/__init__.py`,
  `research/report/builder.py`, `quant/prompts/result_explanation_v1.txt`,
  `tests/unit/backtest/test_robustness.py`, `tests/unit/research/test_report.py`,
  `tests/unit/test_cli_report.py`, `tests/integration/test_report_live.py`
- **수정**: `app/commands/hitl_flow.py` — **generate_report 함수 본문 + import만**.
  `tests/unit/test_cli_hitl.py` — generate-report 스텁 테스트 2건(현재 말미) 교체/이동만.
- **금지**: `app/cli.py`, `app/streamlit_app.py`·`app/ui/`(S1), `core/` 수정,
  기존 quant/backtest 4파일·quant/strategy 수정, `docs/`, `README.md`.

### S1 — Streamlit 7화면 (모델: **Sonnet**, 브랜치 `w3c-s1-streamlit`)

- **신규**: `app/streamlit_app.py`(엔트리), `app/ui/__init__.py`, `app/ui/state.py`,
  `app/ui/screens.py`(또는 화면별 분할 — 자유), `tests/unit/ui/`(신규 디렉토리 전체)
- **금지**: `app/cli.py`·`app/commands/` 수정(**호출만** — typer 함수 호출 금지, §3.2),
  `core/`·`research/`·`quant/` 수정, 기존 테스트 파일, `docs/`, `README.md`.

공유 파일 없음(R1의 hitl_flow 구역과 S1은 분리). 병합 순서 무관.

## 2. R1 명세

### 2.1 강건성 분석 (`quant/backtest/robustness.py`) — README §24.2·§24.3

```python
class RobustnessReport(BaseModel):  # + 하위 모델 자유 설계
    condition_ablation: list[AblationResult]   # §24.3 조건 제거
    cost_sensitivity: list[CostSensitivityResult]
    subperiod: list[SubperiodResult]
    skipped: list[str]                          # 미수행 항목·사유 (§24.2 잔여)

def run_robustness(
    review: StrategyReview, *, data_dir: Path, stock_code: str, corp_code: str,
    start_date: date, end_date: date, base_config: BacktestConfig, fs_scope: str = "CFS",
) -> RobustnessReport: ...
```

- **조건 제거(§24.3 — 필수)**: `final_strategy`의 `entry.all` 조건들을
  `resolve_indicator`로 소스 분류(FINANCIAL=실적 / PRICE=가격 / FLOW=수급)하고,
  §24.3의 5개 변형(가격만·실적만·실적+가격·실적+수급·실적+수급+가격) 중 **원 전략의
  조건 구성으로 만들 수 있는 변형만** 생성해 각각 백테스트한다(불가능한 변형은
  skipped에 사유 기록). 변형 전략은 entry.all의 해당 소스 조건 부분집합으로 구성하고
  exit·execution·universe는 보존한다. 변형이 컴파일 불가하면 skipped 기록.
  각 변형의 재검증·실행은 **`execute_approved_strategy`를 우회하지 않되** — 변형은
  승인본이 아니므로 review를 변조하지 않고 `engine.run_backtest` 경로(연구용, runner
  docstring이 허용)를 쓴다. **원 전략(전체 조건) 결과가 승인 백테스트와 일치하는지
  assert**(자기 검증). 결과: 변형별 num_trades·cumulative_return·mdd·win_rate·PF.
- **비용 민감도**: commission·sell_tax·slippage를 0×/1×/2× 배율로 원 전략 재실행.
- **하위 기간**: [start, mid]·[mid, end] 이분할(mid=거래일 기준 중앙) 원 전략 재실행.
- **§24.2 잔여**(인샘플/아웃오브샘플·파라미터 민감도·시장 국면)는 skipped에
  "후순위(제출 후 확장)"로 기록 — 조용한 누락 금지.
- 저장: `robustness_report.json`(run_dir, generate-report가 저장). 데이터 로드는
  runner의 관례(daily/metrics parquet)를 따르되 중복 로드를 피하도록 내부 구조 자유.

### 2.2 15-섹션 보고서 (`research/report/builder.py`) — HITL §6·1804 §16

```python
def build_research_report(
    store: RunStore, *, robustness: RobustnessReport | None,
    ai_explanation: str | None, ai_explanation_origin: str,  # "AI_DRAFT" 계열 표기용
) -> str:  # 마크다운 전문
```

- run 산출물 전부(manifest·evidence·candidate_analysis·analyst_view·hypothesis·
  review·backtest_result·interpretation·ai_usage_log)를 로드해 **HITL §6의 15개 섹션
  순서 그대로** 마크다운을 만든다. 산출물 부재는 store의 next_step_hint 예외 전파(→CLI가
  exit 1) — COMPLETE 상태 run은 전부 존재한다.
- **제목은 논지형**(HITL §6): `"{corp_name}: {analyst_view.core_thesis}"` (기업명 나열
  금지 — core_thesis가 이미 문장이므로 그대로 부제 없이 사용).
- **저작 주체 표기(1804 §16, ContentOrigin)**: 각 섹션 제목 옆에 출처 태그를 단다 —
  `[사용자 작성]`(2·3·5·6·7·8·11·12·13·14) / `[Python 계산]`(1·10 성과표·12 강건성표·15 집계) /
  `[AI 후보·초안 — 사용자 승인]`(4의 후보 표·9의 초안·10의 AI 설명 초안). AI 설명 초안
  단락에는 본문에도 "아래는 AI가 작성한 초안 설명이며, 최종 해석(§11~14)은 사용자가
  작성했다" 명시.
- 섹션 매핑(요지): 1=manifest / 2=research_question / 3=core_thesis+main_findings /
  4=선택 evidence 상세 표(+CandidateAnalysis findings 요약) / 5·6=선택·제외와 이유 /
  7=counterarguments·uncertainties / 8=가설 전문(승인 기록 포함) / 9=final_strategy
  JSON+modifications 표(0건이면 "무수정 승인"과 approval_reason) / 10=성과지표 표+
  AI 설명 초안 / 11·12=supporting·contradicting(+12에 강건성 표: 조건 제거·비용·기간) /
  13=decision_reason / 14=hypothesis_decision과 갱신된 hypothesis.status /
  15=ai_usage_log 표(stage·model·prompt_version) + "사용자 수행: 관점·근거 선택·가설·
  전략 승인·해석" 대비 서술.
- 결정성: 같은 입력 → 같은 출력(타임스탬프는 generated_at 1곳만, 인자로 주입 가능하게).

### 2.3 result_explanation 프롬프트 + `generate-report` CLI (hitl_flow 구역)

- `quant/prompts/result_explanation_v1.txt`: 입력 = 성과지표 요약·가설 요지·강건성 요약.
  지시 = "사실 서술만, 성과의 원인 단정 금지, 유리·불리 양면 제시, 투자 의견 금지,
  2~4문단 한국어, 마크다운 헤더 금지". 출력은 **일반 텍스트**(JSON 아님) —
  `client.complete_text` 직접 사용(complete_validated 불필요). 사용 후
  AIUsageRecord(stage="result_explanation", output_artifact_ids=["research_report.md"]).
- `generate-report --run-id` 실구현: 상태 `COMPLETE` 필수(기존 게이트 유지, 미달 exit 4)
  → run_manifest에서 종목·기간(백테스트와 동일 규칙: start 기본 2016-01-01, end=as_of —
  backtest_result.json의 start/end를 그대로 재사용해 **승인 백테스트와 동일 창** 보장)
  → `run_robustness` 실행·`robustness_report.json` 저장 → LLM 설명 초안 생성(실패 시
  **보고서는 계속 생성**하고 해당 단락에 "AI 설명 초안 생성 실패 — 사용자 해석만 수록"
  기록 + 경고 출력; LLM은 부가 기능이지 게이트가 아니다) → `build_research_report` →
  `research_report.md` 저장 → 요약 출력(제목·섹션 수·강건성 변형 수·저장 경로) + 상태
  표시 2줄(전이 없음 — COMPLETE 유지, 재실행 덮어쓰기 허용).

### 2.4 R1 테스트·DoD

- unit: 조건 분류·변형 생성(§24.3 5종 매핑, 불가 변형 skipped)·비용 배율·이분할 경계
  (synthetic parquet — tests/unit/backtest 픽스처 재사용), 보고서 15섹션 전부 존재·
  순서·저작 태그·논지형 제목·무수정 승인 표기(fixture run), CLI 상태 게이트(4)·
  LLM 실패 시 계속 생성.
- integration(live): E2E COMPLETE run 픽스처(로컬 구성)로 `r2b generate-report` 실행 —
  LLM 설명 초안 실호출 1회, research_report.md 15섹션 확인. live 호출 예산 **2회**.
- `make check` 클린. 실데이터 캡처(보고서 제목·§10·§12 표)를 최종 보고에 포함.

## 3. S1 명세 — Streamlit 7화면 (1804 §15)

### 3.1 구조

- 엔트리 `app/streamlit_app.py`: `streamlit run src/research_backtest/app/streamlit_app.py`.
  사이드바 = run 선택(outputs 스캔, `runs` 명령과 동일 규칙) + 새 run 생성 + 현재 상태
  표시(파이프라인 상태 뱃지 + 다음 단계). 본문 = 상태에 맞는 화면으로 안내하되 7화면
  탭/단계 네비게이션 제공(진행 불가 화면은 잠금 + 사유 표시 — 게이트 UI 반영).
- **비즈니스 로직 재구현 금지**: 저장·검증·상태 전이는 core/hitl(store·gates·states·
  validation·diff)와 research/candidates(`run_generate_candidates`는 typer 의존이 없는
  범위에서 — typer.Exit를 던지는 CLI 함수는 **호출 금지**, 대신 하위 API를 동일 순서로
  조립하고 그 순서를 docstring에 CLI 대응 명령으로 기록), quant/strategy/draft·
  quant/backtest/runner를 직접 사용. **CLI와 다른 완화 규칙을 만들지 않는다**(게이트
  약화 금지 — 예: evidence 검증 생략, 미승인 실행 버튼 활성화).
- 화면 구성(1804 §15 문면 그대로의 필드):
  ① 기업·기준일 입력(+기간·초점 — 초점은 run_manifest에 없으므로 입력만 받고 안내:
  "분석 초점은 v2 산출물에 없음 — analyst_view에 반영하라", 저장 안 함을 명시) →
  create-run 동작 ② AI 분석 후보 검토: candidate_analysis 5범주+상충 근거 표시, 선택·
  제외 체크박스 → 선택 결과를 화면 ③의 초기값으로 ③ 관점 작성: AnalystView 폼 →
  검증·저장·전진 ④ 가설 작성: 폼 + **승인 버튼**(status=APPROVED, approved_by 입력 필수)
  ⑤ 전략 초안 검토: generate-strategy-draft 트리거 버튼 + 초안 JSON 표시 + **임계값
  수정 UI**(entry/exit 숫자 필드 편집) + 수정 이유 입력 + 승인 버튼(diff_strategies로
  modifications 자동 생성, reason 채움 — approve-strategy와 동일 검증 체인) ⑥ 백테스트
  결과: 실행 버튼(승인 시에만 활성) + 성과지표 표 + equity 차트(daily_portfolio.csv
  기반 st.line_chart) + 거래내역 표 + 벤치마크 비교 + (robustness_report.json 있으면)
  조건 제거 표 ⑦ 최종 해석: BacktestInterpretation 폼 → COMPLETE + generate-report
  안내.
- LLM 호출 화면(② 생성 버튼·⑤ 초안 버튼)은 spinner + 실패 시 st.error(메시지 그대로,
  토큰 비노출). content_origin은 내부 모델이 저장하므로 UI 태그 노출은 선택(HITL §8) —
  AI 산출 영역에 "AI 후보/초안" 캡션은 표시한다.

### 3.2 S1 테스트·DoD

- `streamlit.testing.v1.AppTest` 기반 스모크: 앱 로드, run 없음 상태 렌더, 픽스처 run
  (tmp outputs, 상태별 3케이스: DATA_READY / AWAITING_ANALYST_VIEW / COMPLETE)에서
  해당 화면 위젯 존재·잠금 로직 확인. LLM 호출 경로는 FakeLlmClient monkeypatch.
- `make check` 클린(streamlit 임포트가 mypy strict 통과해야 — 타입 스텁 부재 시
  pyproject의 mypy 설정을 바꾸지 말고 모듈 국소 `# type: ignore[import-untyped]` +
  사유 주석). 실행 스크린샷은 불요 — 대신 `AppTest` 출력과 수동 실행 로그(초기 렌더
  성공)를 최종 보고에.
- 상태 전이·승인 기록이 CLI와 동일 산출물을 만드는지 1케이스(픽스처 run에서 화면 ③
  저장 → run_state.json의 transitions 확인).

## 4. 진행 관례·보고

W3b §0·§8과 동일(워크트리 셋업·.env 주입 규칙·live 예산 — R1 2회/S1 0회(FakeLlm만)·
명세 이탈 보고·트레일러 R1=Opus/S1=Sonnet). 최종 보고: 구현 요약(파일·줄수)·테스트
before/after·캡처(R1: 보고서 발췌, S1: AppTest 결과)·명세 이탈·브랜치·커밋 해시.
