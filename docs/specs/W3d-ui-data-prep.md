# W3d 구현 명세 — Streamlit 화면 ① 데이터 준비 통합 (2026-07-15)

> 발주: 메인 세션. 사용자 요구(원문): "스트림릿에서 기업·기준일 입력하면 모든 걸
> 실행할 수 있도록" + "데이터 수집(다트 등)에 시간이 오래 걸리면 예상 시간 등을 표기".
> 실사용 재현: 화면 ①에서 신규 기업(예: 네이버 035420) run 생성 시 "데이터 준비가
> 완료되지 않았습니다: 재무 지표 …·시장 데이터 …" 로 차단됨 — 이 차단을 **원클릭
> 준비 실행**으로 해소한다. 단일 트랙(**Sonnet**, 브랜치 `w3d-ui-data-prep`).

## 0. 소유권·불변 조건

- **수정**: `app/ui/actions.py`·`app/ui/screens.py`·`app/ui/state.py`(필요분),
  `tests/unit/ui/`(확장). **금지**: `core/`·`research/`·`quant/`·`app/cli.py`·
  `app/commands/`·`docs/`·`configs/` — 데이터 준비는 **기존 core 함수 조립만**으로
  구현한다(CLI와 동일 로직 원칙, 진행 콜백을 위한 core 시그니처 변경 금지).
- 게이트·검증·상태 전이 정책 불변. 키 값 비노출 불변.

## 1. 데이터 준비 오케스트레이션 (`app/ui/actions.py`)

```python
@dataclass(frozen=True)
class PrepStep:
    key: str            # "financials" | "market" | "build" | ("xbrl" | "reconcile" 옵션)
    label: str          # 한국어 표시명
    estimate_seconds: float  # §3 산식
class PrepPlan: steps: list[PrepStep] ...   # 설계 재량

def plan_data_preparation(corp, *, from_year, to_year, include_xbrl, settings) -> PrepPlan
def run_preparation_step(step, corp, *, settings, ...) -> str   # 단계 실행 → 결과 요약 1줄
```

- 단계 구성(순서 고정): ① 재무 수집 = `core.dart.financial_api.collect_financials`
  (CLI collect-financials와 동일 인자 조립) ② 시장 수집 = `core.market.collector.
  collect_market_data`(PykrxSource, 종료일 = KST 어제 — CLI 규칙 동일) ③ 재무 빌드 =
  `core.financials.pipeline.build_financial_datasets` ④(옵션, include_xbrl=True일 때만)
  XBRL 수집 = `download_xbrl_filings`(CLI §4.5와 동일 필터) ⑤(옵션) 대조 =
  `reconcile_all`. **이미 준비된 단계는 계획에서 제외**(멱등 — 준비 검사 로직 재사용:
  metrics·daily·calendar 존재 여부. 재무 수집·시장 수집은 각자 캐시가 있어 재실행도
  안전하지만, 전부 준비돼 있으면 준비 UI 자체를 띄우지 않는다).
- 실패 처리: 단계 실패 시 즉시 중단, 해당 단계의 예외 메시지를 st.error로(이후 단계
  미실행 표시). ConfigError(키 없음)는 준비 시작 전에 검사해 안내(DART 키 필수;
  KRX 자격증명 없으면 **부분 수집 모드 경고**를 미리 표시 — CLI collect-market의
  경고 문구 재사용 — 하고 진행은 허용).

## 2. 화면 ① UX (`app/ui/screens.py`)

- 기존: create-run 시 데이터 미비 → 에러 텍스트로 끝. **변경**: 미비 항목 안내 아래에
  준비 패널을 띄운다 — 수집 옵션(시작 연도 기본 2015 — PROJECT_SPEC §6.4 데이터 경계,
  XBRL+대조 체크박스 기본 off "정합성 검증용 · 수 분 추가") + **[데이터 준비 실행]**
  버튼.
- 실행 UI: `st.status`(또는 st.progress 조합, expanded=True)로 **단계별 진행**을
  표시한다. 각 단계 라벨 형식(사용자 요구 — 예상 시간 표기):
  `③ 재무 데이터셋 빌드 — 예상 ~10초 … 완료 (7초)` 처럼
  **단계명 + 사전 예상 + 실측 경과**를 남기고, 진행 중 단계는 spinner + "예상 ~N분
  (전체 남은 예상 ~M분)". 전체 계획 요약(단계 수·총 예상)을 시작 전에 표시.
- 완료 시: 성공 요약(단계별 결과 1줄 — 수집 행수·빌드 fact 수 등) 후 **run 생성을
  자동 재시도**해 화면 ②로 이어지게 한다.
- 화면 ②·⑤의 LLM 버튼에도 예상 시간 라벨 추가: ② "AI 후보 생성 (예상 2~5분)",
  ⑤ "전략 초안 생성 (예상 ~1분)". 실행 중 spinner에 경과 시간 표시(가능한 범위).

## 3. 예상 시간 산식 (state.py 또는 actions.py — 결정적, 문서화)

- 재무 수집: 요청수 R = (to_year−from_year+1)×4(보고서)×2(scope);
  예상 = R × (dart min_interval 0.1s + 평균 응답 ~0.7s) — "최대" 기준(캐시 히트는
  더 빠름을 라벨에 명시: "캐시가 있으면 훨씬 빨리 끝납니다").
- 시장 수집: pykrx가 연 단위 페이징 — 예상 = 연수 × 2(가격·수급) × ~1.5s + 10s
  오버헤드(로그인). 지수·캘린더는 캐시 히트(전역 공유)임을 반영해 계획에서 제외하지
  말고 "캐시" 라벨로 0초 처리 가능(설계 재량 — 실측으로 보정).
- 빌드: 고정 ~10초. XBRL(옵션): 건당 ~10초 × 예상 건수((연수+1)×4 상한). 대조(옵션):
  ~60초 + 파싱 잔여.
- **산식 상수는 live 스모크 실측으로 보정**하고 근거를 docstring에 기록(§5).

## 4. 테스트 (`tests/unit/ui/`)

- 오케스트레이션 unit: 준비 완료 상태 → 계획 0단계(패널 미표시), 미비 상태 → 계획
  구성·순서, 옵션 on/off에 따른 단계 증감, 단계 실패 시 중단·이후 단계 미실행
  (collector들은 monkeypatch — 실호출 금지), KRX 자격증명 없음 → 부분 수집 경고.
- 산식 unit: 연수에 단조 증가, 경계값.
- AppTest: 데이터 미비 픽스처에서 준비 패널·버튼·옵션 렌더 확인(실행 클릭은
  monkeypatch된 가짜 collector로 1케이스 — run 자동 재시도까지).
- `make check` 클린 (기존 837 기준 유지).

## 5. live 스모크 (DoD — LLM 아님, DART·KRX 실호출)

메인 레포 `.env` 주입 + `DATA_DIR=/Users/baemingyu/project/MC_investment_homework/data`
(이번엔 **신규 기업 데이터가 실제로 생성됨** — 의도된 동작): 오케스트레이션 함수를
직접 호출해 **네이버(035420, corp 00266961)** 를 2015~2025로 준비 — 단계별 실측
시간을 캡처해 §3 상수를 보정하고, 완료 후 `financial_metrics.parquet`·`daily.parquet`
존재 + build 검증 3종 pass를 확인. (Streamlit 실행 화면 검증은 AppTest로 갈음.)
실측·보정 내역을 최종 보고에 포함. **LLM 호출 예산 0회.**

## 6. 보고·관례

W3c와 동일(트레일러 `Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`, 명세
이탈 기록, `git diff --stat main` 소유권 확인). 병합·미러 동기화·푸시는 메인 세션.
