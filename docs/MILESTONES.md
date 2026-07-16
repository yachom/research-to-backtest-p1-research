# 개발 마일스톤 v1.1 — 수직 슬라이스 재구성

- 원본 계획: 기술 명세 §31 (v1.0). 명세 전문은 C3' 문서 재편(2026-07-15)으로
  `docs/PROJECT_SPEC.md`에 보존됐다 — 이 문서와 코드의 "README §nn" 참조는 전부
  PROJECT_SPEC의 해당 절을 뜻한다. 이 문서는 실행 순서와 결정 사항만 재구성한다.
- 각 마일스톤의 상세 스키마·완료 조건은 README 해당 절을 따른다.

## 1. 왜 순서를 바꾸는가

v1.0은 M0→M10 직렬(깊이 우선) 구성이라 **M9에 도달해야 처음으로 백테스트가 돈다.**
채용 과제 특성상 가장 큰 리스크는 "XBRL 파서는 훌륭한데 제출물(보고서+백테스트)이 없는 상태"다.

1. **일정 불확실성이 가장 큰 구간이 XBRL(M3~M5)이다.** 기업 확장계정, 제출 파일 구성 편차, dimension 처리 등 예측 불가능한 비용이 몰려 있다.
2. **시장 데이터(가격·수급·벤치마크)를 다루는 마일스톤이 없다.** M9 안에 암묵적으로 포함되어 있으나 실제로는 독립 작업량이다 — 수정주가 처리, KRX 거래일 캘린더(available_from 계산의 전제), 투자자 수급 수집.
3. **전체 재무제표 API(M2)만으로도 백테스트에 필요한 핵심 계정은 확보된다.** XBRL은 "검증·보존·세부계정" 계층으로 뒤에 붙여도 아키텍처가 성립한다 — README §5.1의 계층 설계 그대로다.

따라서 **Phase A에서 얇게 끝까지 관통**시키고, Phase B(XBRL 깊이)와 Phase C(AI 리서치)를 그 위에 쌓는다. 어느 시점에 멈춰도 제출 가능한 상태를 유지한다.

## 2. Phase 구성

### Phase A — 관통 (엔드투엔드 스켈레톤)

| ID | 원 마일스톤 | 내용 | 완료 조건 요약 |
|---|---|---|---|
| A0 | M0 | 프로젝트 기반 구축 | pytest·CLI help·lint·typecheck 통과 — ✅ 완료 (2026-07-14) |
| A1 | M1 | DART 기업·공시 식별 | `resolve-company`로 corp_code·최근 보고서 접수번호 출력 — ✅ 완료 (2026-07-14) |
| A2 | M2 | 전체 재무제표 API 수집 | 5개년 CFS·OFS raw 저장, 캐시·오류코드 처리 — ✅ 완료 (2026-07-14, 실데이터 특성은 docs/DATA_NOTES.md) |
| A3 | (신설) | 시장 데이터 | pykrx로 수정주가 OHLCV·투자자 수급·KOSPI 수집, KRX 거래일 캘린더 — ✅ 완료 (2026-07-14, KRX 계정으로 수급·지수·캘린더 포함 전체 수집, 2015-01-02~2026-07-13 각 2,829행) |
| A4 | M6 축소 | 핵심 계정 정규화·시계열 | registry 기반 11개 계정, 단독분기 역산, YoY, available_from 부여 — ✅ 완료 (2026-07-14, 11계정 전량 매칭·검증 통과, CF 의미론 실측은 DATA_NOTES) |
| A5 | M8 축소 | 전략 DSL 스키마·컴파일러 | README §23 기본 전략을 JSON으로 검증·컴파일 (LLM 없이) — ✅ 완료 (2026-07-14, §23.4 JSON 무수정 통과·no-lookahead 테스트) |
| A6 | M9 | 백테스트 엔진 | 룩어헤드 테스트(§28.3) 통과, 성과지표, Buy & Hold 비교 — ✅ 완료 (2026-07-14, 승인 게이트 내장·첫 실데이터 실행 결과는 DATA_NOTES·PROGRESS #2) |

**Phase A 종료 시점 상태**: §23 기본 전략(실적 YoY + 외인 수급 + 60일 돌파)이 실데이터로 end-to-end 실행된다. 이 시점부터는 언제 멈춰도 "동작하는 백테스트 시스템"을 제출할 수 있다.

### Phase B — 깊이 (데이터 신뢰성 계층)

| ID | 원 마일스톤 | 내용 |
|---|---|---|
| B1 | M3 | XBRL 원본 수집·보존 (manifest, checksum, 오류 XML 탐지) — ✅ 완료 (2026-07-14, 2021~2025 21건 + 기재정정 1건 실수집) |
| B2 | M4 | XBRL Fact·Context·Unit·Dimension 파싱 — ✅ 완료 (2026-07-14, 실측은 docs/DATA_NOTES.md — §10.1 Context 규칙 수정 필요 발견) |
| B3 | M5 | 계정 표준화 고도화 + API-XBRL 정합성 검증(§16.4) — ✅ 완료 (2026-07-14, 연간 70건 100% MATCH·실측은 DATA_NOTES) |
| B4 | §15 | 정정공시 버전 관리 · Point-in-Time View 정식화 — ✅ 완료 (2026-07-14, 실데이터 정정 쌍으로 PIT 경계값 재현) |

### Phase C — Human-in-the-Loop 리서치 및 마무리 (요구사항 v2로 재설계, 2026-07-14)

> 재설계 배경·정본: docs/HUMAN_IN_THE_LOOP.md, docs/AI_ROLE_BOUNDARY.md, docs/OUTPUT_SCHEMA.md.
> AI 단독 기업분석·가설 생성(구 C1·C2)은 폐기하고, AI=후보 정리·초안 / 사용자=관점·가설·승인·해석 구조로 전환.

| ID | 원 마일스톤 | 내용 |
|---|---|---|
| H1 | (신설) | HITL 기반 계층: 산출물 모델(CandidateAnalysis·AnalystView·HumanInvestmentHypothesis·StrategyReview·BacktestInterpretation·AuthoredContent), 파이프라인 상태 머신 12종 + 승인 게이트, run_id 산출물 저장소, 검증 규칙 — ✅ 완료 (2026-07-14, OUTPUT_SCHEMA 1:1·테스트 148개) |
| C1' | M7 개정 | Evidence Store 구축 + **HypothesisCandidateGenerator**(구 가설 생성기 개명·역할 축소): LLM이 CandidateAnalysis·hypothesis_candidates(참고용)만 생성. 프롬프트 버전 파일 + 호출 기록(과제2 증빙) — ✅ 완료 (2026-07-15, Wave 3a E1+3b C1' — evidence 122건 PIT 검증·live Haiku 후보 생성, 명세 W3a·W3b) |
| C2' | M8 개정 | **승인된** HumanInvestmentHypothesis → LLM 전략 DSL 초안 → Validator → 사용자 검토·수정(StrategyModification) → 승인(StrategyReview) → 백테스트 전달 — ✅ 완료 (2026-07-15, Wave 3b — live 초안이 A5 컴파일·§21 화이트리스트 통과) |
| C3' | M10 개정 | 사용자 해석(BacktestInterpretation) 반영 보고서(15개 섹션, 저작 주체 표기, 논지형 제목) · Streamlit 5화면 · 강건성 분석 · 문서 마무리(PROJECT_SPEC.md 이관 포함) |

- B와 C는 Phase A 완료 후 **병렬 진행 가능**하다.
- 일정이 부족하면 B2~B3를 "대표 계정 7개 교차검증"(§16.4 최소선)으로 축소한다.
- XBRL 파싱 범위는 MVP 기업(SK하이닉스)의 정기보고서로 한정한다. 전 기업 일반화는 후순위(§32).

### 병렬 웨이브 계획 (D8 개정, 2026-07-14)

| 웨이브 | 병렬 트랙 | 의존성 |
|---|---|---|
| Wave 1 | **A4**(core/financials — 재무 정규화·시계열) ∥ **A5**(quant/strategy — 전략 DSL) ∥ **B1+B2**(core/xbrl — XBRL 수집·파싱) ∥ **H1**(core/hitl — HITL 기반 계층, v2 요구로 추가) | 상호 독립 (H1은 모델·상태 계층이라 데이터 의존 없음) |
| Wave 2 | **A6**(quant/backtest — A4·A5 계약 + H1 승인 게이트 의존) ∥ **B3**(정합성 검증 — B2+A4 의존) ∥ **B4**(정정공시 PIT) | Wave 1 병합 후 |
| Wave 3 | **C1'·C2'**(HITL 리서치·전략 초안 — H1+A4 의존, live LLM은 OpenRouter 키 확보 후) → **C3'**(보고서·Streamlit·마무리) | Wave 2 병합 후 |

파일 소유권: A4=`core/financials/`+`configs/account_registry.yaml`+`tests/*financials*`, A5=`quant/strategy/`+`tests/*strategy*`, B1B2=`core/xbrl/`+`core/dart/xbrl_downloader.py`+`tests/*xbrl*`. `app/cli.py`·`core/exceptions.py` 등 공유 파일은 메인 세션만 수정.

## 3. 결정 기록 (Decision Log)

| # | 결정 | 근거 | 되돌리려면 |
|---|---|---|---|
| D1 | 가격·수급·지수 데이터는 **pykrx** (개정 2026-07-14: KRX 로그인 필요) | 무료·투자자 수급 제공. 단, **KRX가 2025년부터 데이터 조회에 로그인을 의무화** — 수정주가 OHLCV는 무로그인 동작(Naver 경유), 투자자 수급·지수·원주가는 data.krx.co.kr 무료 계정의 `KRX_ID`/`KRX_PW` 필요(pykrx 1.2.8이 환경변수로 자동 로그인). 어댑터는 자격증명 없으면 가격만 수집하는 부분 수집 모드 지원. KIS API는 후순위 | A3의 MarketDataSource 어댑터만 교체 |
| D2 | LLM은 **Claude Agent SDK**(`claude-agent-sdk`, CLI 번들) — 인증은 SDK 환경변수 체인: `CLAUDE_CODE_OAUTH_TOKEN`(별도 구독 계정, `claude setup-token` 발급 1년 토큰) 또는 `ANTHROPIC_API_KEY`(Console 과금, 정책상 가장 명확). **둘 다 설정 금지**(API 키가 우선해 의도치 않은 과금). 폴백: OpenRouter 무료 모델 (재개정 2026-07-15 — OpenRouter 일일 한도가 작아 primary에서 강등) | 사용자 결정: 별도 구독 계정을 프로그래매틱 호출에 사용. 검증 결과(공식 문서): setup-token은 Pro/Max용 1년 OAuth 토큰이며 Agent SDK가 구독 인증을 지원(feature-availability 매트릭스 확인), 제3자 서비스 제공은 금지·본인 로컬 실행은 명시 금지 없음(회색지대 — 과제는 로컬 실행·시연이라 해당 없음, 배포 시 API 키 필수). macOS에서 토큰 방식은 Keychain(주 계정 로그인)과 무간섭. 구현 보완책 유지: JSON 검증+재시도 루프, allowed_tools 최소화, max_turns·비용 상한, AIUsageRecord 기록 | `llm_provider` 설정 1줄 (openrouter 폴백) |
| D3 | 패키지는 `src/research_backtest/` + 콘솔 스크립트 **`r2b`** | §26의 `python -m src.app.cli`는 `src`를 패키지명으로 쓰는 비표준 구조. 내부 서브패키지 구조(§25)는 그대로 유지 | pyproject 스크립트 항목 |
| D4 | `include_news` 기본값 **False** | §3.2(True)와 §32(MVP에 뉴스 없음)의 모순 해소 | `common/models.py` 한 줄 |
| D5 | MVP 대상 기업 **SK하이닉스(000660)** | README 예시 전반과 일치 | 실행 인자일 뿐, 언제든 변경 가능 |
| D6 | `data/`·`outputs/`는 커밋하지 않고 런타임 생성 | 원본 데이터·키 커밋 방지(§30 취지) | .gitignore |
| D7 | 레포는 **단일 패키지 + 3분할 서브패키지**: `core`(공용 데이터 플랫폼) / `research`(Project 1) / `quant`(Project 2). 공용 configs·.env는 루트 | P1·P2 모두 시장 데이터와 재무 시계열을 사용하므로 순수 2분할은 ETL 중복 또는 교차 import를 유발. 설치·재현은 pyproject 1개(`make install` 한 번)로 유지. P1→P2 계약은 코드가 아닌 **산출물**(hypothesis JSON + core가 발행한 PIT 데이터셋) | uv workspace로 물리 분리(필요 시) |
| D9 | **Human-in-the-Loop 아키텍처 채택** (요구사항 v2, 2026-07-14): AI 단독 기업분석·투자가설 확정 구조를 폐기하고, AI=후보 정리(CandidateAnalysis)·초안(전략 DSL), 사용자=분석 질문·논지·근거 선택·가설 작성·전략 승인·결과 해석으로 역할 재배치. 승인 게이트 12-상태 머신으로 강제, 저작 구분(content_origin) 저장. HITL 계층은 `core/hitl/`(P1·P2 공용 워크플로) | 과제 1의 "본인의 시각" 요건 — AI 판단처럼 보이는 구조 회피. 정본: docs/HUMAN_IN_THE_LOOP.md·AI_ROLE_BOUNDARY.md·OUTPUT_SCHEMA.md | Phase C 재설계로 반영(코드는 H1부터) |
| D8 | **진행 방식** (개정 2026-07-14): 메인 세션은 설계·구현 명세(`docs/specs/`)와 통합 담당. 의존성이 없는 마일스톤은 **병렬 에이전트(작업트리 격리)로 동시 진행** — 각 에이전트는 자기 소유 파일만 수정하고 워크트리 브랜치에 커밋, 메인 세션이 병합·CLI 연결·전체 품질 게이트·최종 커밋을 수행. 명세에 파일 소유권과 인터페이스 계약을 명시해 병합 충돌을 설계 단계에서 차단. **모델 정책: 하위 에이전트는 Fable 사용 금지 — Opus(복잡 로직) 또는 Sonnet(명세가 촘촘한 구현)만. Fable은 메인 세션 전용** | 순차 진행은 세션 시간이 병목 (사용자 요청). 계약(스키마)을 명세에 먼저 고정하면 의존 마일스톤(A6)도 대기 없이 착수 가능 | 세션 운영 방식이므로 코드 영향 없음 |

## 4. 레포 레이아웃 v2 (D7)

```text
MC_investment_homework/
├── README.md            # 명세 원본 (v1.0, 사용자 작성)
├── pyproject.toml       # 설치 단위는 하나
├── .env / .env.example  # 공용 환경변수 (루트)
├── configs/             # 공용 설정 (루트)
├── docs/
│   ├── MILESTONES.md    # 실행 계획·결정 기록 (이 문서)
│   └── specs/           # 마일스톤별 구현 명세 (구현의 계약, D8)
├── src/research_backtest/
│   ├── core/            # 공용: 모델·설정·달력·예외 + DART·시장 데이터 ETL + 재무 정규화
│   ├── research/        # Project 1: Evidence → LLM 분석 → 리포트·투자 가설
│   ├── quant/           # Project 2: 전략 DSL → 백테스트 → 강건성
│   └── app/             # 통합 CLI (r2b)
└── tests/               # unit(오프라인) / integration(실 API, 키 없으면 skip)
```

README §25와의 매핑: `common`·`data_sources`·`xbrl`·`financials` → `core`,
`disclosures`·`research` → `research`(P1), `strategy`·`backtest` → `quant`(P2).

## 5. README(v1.0) 정오표 — 다음 개정 시 반영

1. **§21.2 ↔ §23.4 불일치**: 전략 예시가 `rolling_high_60_lag1`을 사용하나 허용 가격지표 목록에는 `rolling_high_60`만 있다. → DSL에 `lag(indicator, n)`을 정식 도입하거나 lagged 지표를 목록에 등록.
2. **§3.2 ↔ §32 모순**: `include_news: True` 기본값 vs MVP 범위에 뉴스 미포함. → 기본 False (D4 반영 완료).
3. **§26 CLI 형태**: `python -m src.app.cli …` → 콘솔 스크립트 `r2b …` (D3 반영 완료).
4. **§31 마일스톤 공백**: 시장 데이터 수집이 어느 마일스톤에도 없음 → A3로 신설.
5. **데이터 경계**: 전체 재무제표 API는 2015년 이후만 제공(§6.4). 백테스트를 2016-01-01에 시작하면 2015년 연간·분기 데이터가 선행 재무로 필요 — 경계 검증 필요. 2015 이전으로 확장하려면 별도 소스가 필요하다.
6. **문서 재편(§25·DoD 20)**: 최종 제출 시 README는 실행 방법·설계 요약으로 재편하고, 현재 명세 전문은 `docs/PROJECT_SPEC.md`로 이동 — C3에서 수행.
