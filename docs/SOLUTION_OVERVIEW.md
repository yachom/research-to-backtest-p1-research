# 과제 해결 정리 — 어떻게 구현했고 무엇을 해결했는가 (2026-07-15)

> 두 개의 프로젝트(P1 리서치 / P2 백테스트)로 나뉜 하나의 큰 과제를 어떤 구조와
> 순서로 구현·검증했는지의 정리. 상세 근거는 각 절의 링크 문서를 따른다.
> (과제 2 "AI 활용 증빙"의 서사 축이기도 하다 — §6·§7.)

## 0. 과제 해석과 핵심 결정 두 가지

**과제**: ① 기업·산업 리서치와 투자 가설 생성기(P1) ② 가설을 정형 전략으로 바꿔
과거 데이터로 검증하는 백테스트 엔진(P2). 원 명세(v1.0, docs/PROJECT_SPEC.md)는
AI가 분석·가설까지 만드는 구조였다.

- **결정 1 — Human-in-the-Loop 재설계(v2, D9)**: 과제 1은 "본인의 시각과 근거"가
  필수인데 AI가 가설을 확정하면 분석이 AI의 판단처럼 보인다(1804_FEEDBACK.md).
  그래서 AI를 **후보 정리·초안 도구**로 강등하고, 분석 질문·논지·근거 선택·가설·
  전략 승인·결과 해석은 전부 사용자 산출물로 만들었다. 이 역할 분리는 문서 약속이
  아니라 **코드로 강제**된다(§3).
- **결정 2 — 얇게 관통 후 깊이(MILESTONES v1.1)**: M0→M10 직렬 계획은 XBRL 난소화
  구간에서 막히면 제출물이 없다. Phase A(수집→정규화→DSL→백테스트 관통) 먼저,
  Phase B(XBRL 검증 깊이)·Phase C(HITL 리서치)를 그 위에 쌓아 **어느 시점에 멈춰도
  제출 가능한 상태**를 유지했다.

## 1. 하나의 플랫폼, 두 프로젝트 (D7)

```text
core/      공용 데이터 플랫폼 — DART·XBRL·시장 데이터 ETL, 재무 정규화(PIT),
           HITL 상태 머신·승인 게이트·산출물 저장소, LLM 클라이언트
research/  P1 — Evidence Store → AI 분석·가설 후보 → 15-섹션 보고서
quant/     P2 — 전략 DSL 스키마·컴파일러 → 백테스트 엔진 → 강건성 분석
app/       통합 CLI(r2b 19명령) + Streamlit 7화면
```

P1·P2를 물리적으로 쪼개면 ETL이 중복되거나 교차 import가 생긴다. 단일 패키지 +
3분할 서브패키지로 두고, **P1→P2의 계약은 코드가 아닌 산출물**로 정의했다:
승인된 가설 JSON(`human_investment_hypothesis.json`)과 core가 발행한 PIT 데이터셋.
두 프로젝트는 `outputs/{run_id}/` 한 디렉토리에서 만난다(OUTPUT_SCHEMA.md §0).

## 2. 데이터 신뢰성 계층 (P1의 하부 — "검증된 숫자만 위로")

1. **수집**: 전체 재무제표 API(2015~, CFS·OFS, sha256 보존·멱등 캐시) + XBRL 원본
   ZIP(manifest·checksum·zip-slip 방어) + pykrx 시장 데이터(수정주가·수급·KOSPI·
   **KRX 실거래일 캘린더** — 로그인 의무화 실측 대응, D1).
2. **정규화**: registry 기반 11개 표준계정, 누적→단독분기 역산(CF는 누적 의미론
   실측 반영), YoY 등 지표 파생, 그리고 모든 fact에 **`available_from` = 접수일
   다음 거래일** 부여.
3. **교차검증(B3)**: API 수치 ↔ XBRL 원본을 계정·기간·scope·Context 단위로 대조 —
   연간 70건 **100% MATCH**, 분기 220건 중 190 MATCH·30 REQUIRES_REVIEW(사유 분류
   저장). LLM에는 이렇게 검증된 수치만 Evidence로 올라간다.
4. **정정공시 PIT(B4)**: 원본→기재정정 버전 체인을 보존하고 as-of 시점별 가시
   버전을 결정 — 실데이터(2020.12 정정 쌍)로 경계값 재현.

**Point-in-Time은 3중 방어**다: as-of join 방어선(`LookaheadError`) + 지표 레벨
no-lookahead property 테스트 + 워밍업 절단 불변 테스트. Evidence 빌더도 동일
필터를 강제해, 분석 기준일(as_of) 이후 접수된 공시는 **리서치 단계에조차 유입되지
않는다**(as_of=2023-06-30 재실행으로 미래 유입 0건 실증).

## 3. HITL 파이프라인 — 역할 분리를 코드로 강제 (P1↔P2의 연결)

```text
research(기업·기준일) → Evidence 122건(Python) → AI 분석·가설 후보(CandidateAnalysis)
→ [사용자] 관점 작성(AnalystView) → [사용자] 가설 작성·승인 → AI 전략 DSL 초안
→ [사용자] 검토·수정·승인(StrategyReview) → 백테스트(Python) → [사용자] 해석·판정
→ 15-섹션 보고서
```

- **12-상태 머신 + 승인 게이트**(core/hitl): 건너뛰기·비허용 회귀는
  `ApprovalGateError`(CLI 종료 코드 4). 미승인 가설은 전략 변환에 못 들어가고,
  승인 기록 없는 전략은 백테스트 진입점 첫 줄에서 거부된다.
- **AI/인간 저작 분리**: AI 후보(`hypothesis_candidates.json`)와 사용자 가설은
  **다른 pydantic 모델·다른 파일**이라 구조적으로 섞일 수 없고, 서술 콘텐츠에는
  `content_origin`(7종)이 저장된다.
- **근거 강제**: 사용자의 근거 선택(selected/rejected evidence)과 AI의 모든
  finding은 Evidence Store에 실존하는 `evidence_id`여야 하며, LLM이 지어낸 id는
  검증 루프가 위반 목록을 피드백해 재시도시킨다.
- CLI 19명령과 Streamlit 7화면이 **같은 검증·게이트 코드**를 사용한다.

## 4. 전략 DSL과 백테스트 엔진 (P2)

- **DSL(A5)**: 지표 화이트리스트(재무 12·가격 16·수급 4 + `_lagN` 지연 표기),
  조건 트리(all/any/not), 청산 규칙(신호·보유일·손절), 체결 고정
  `signal_time=close → trade_time=next_open`(룩어헤드 차단). 스키마는 strict
  pydantic — 컴파일러가 필요한 지표 컬럼까지 결정한다.
- **엔진(A6)**: financial as-of join(워밍업 보존) → 지표 계산 → [start,end] 절단 →
  t 종가 신호·t+1 시가 체결, 수수료·거래세·슬리피지(configs/backtest.yaml),
  포지션 상태 머신(청산 우선순위 stop>보유일>신호), §24.1 성과지표 전량 +
  KOSPI 벤치마크·Buy&Hold 비교.
- **강건성(C3')**: §24.3 조건 제거(실적/가격/수급 조건 조합 5변형 기여도 비교) +
  거래비용 민감도(0×/1×/2×) + 하위 기간 이분할 — 보고서 §12에 수록.
- 기준 결과(§23.4 기본 전략, SK하이닉스 2016~2025, 비용 반영): 거래 5건, 누적
  **+110.76%**, MDD −16.87%, 승률 60%, PF 8.13, KOSPI 대비 −8.87%p(노출률 5~10%
  — 절대수익보다 조건 충족 구간의 질을 보는 전략임을 해석 단계에서 다룬다).

## 5. LLM 운용 (D2) — 저가형·증빙 가능·안전

- **Claude Agent SDK + 구독 OAuth 토큰**(`CLAUDE_CODE_OAUTH_TOKEN`), API 키와 동시
  설정은 코드가 차단(의도치 않은 과금 방지). 모델은 `configs/llm.yaml`에
  **claude-haiku-4-5-20251001**(저가형)을 전체 ID로 핀 — 재현성.
- 도구 사용 금지(allowed_tools=[])·1턴 고정. JSON 강제는 재시도 루프(코드펜스
  제거 → 파싱 → pydantic/도메인 검증, 실패 사유를 다음 프롬프트에 피드백).
- **증빙 라인**: 프롬프트 4종은 버전 파일(`research/prompts/*_v1.txt`,
  `quant/prompts/*_v1.txt`), 모든 호출은 `ai_usage_log.jsonl`(stage·model·
  prompt_version·입출력 산출물·human_review_required)에 기록. `generated_by` 같은
  신뢰 필드는 LLM 출력이 아니라 코드가 주입한다.
- LLM이 하는 일은 넷뿐: 분석 후보 정리 / 가설 후보 제시 / 가설→DSL 초안 변환 /
  백테스트 결과 설명 초안. **수치 계산·근거 생성·최종 판단은 하지 않는다.**

## 6. 검증 — "동작한다"의 증거

- 품질 게이트: pytest **837 passed·4 skipped**(live LLM·실 API·실데이터 integration 포함),
  ruff·format 클린, **mypy strict 179파일 0 이슈**.
- **live E2E**(run `20260715_152048_SK_HYNIX_INC`, CLI만·수동 개입 0회):
  research(Evidence 122건 + Haiku 후보 생성) → 관점 → 가설 승인 → Haiku 전략 초안
  (1시도에 A5 컴파일 통과) → 승인 → 백테스트(+110.76% — 독립 구현과 수치 일치) →
  해석 → COMPLETE → 15-섹션 보고서. AI 사용 기록 4건.
- 설계를 고친 실측 5건은 전부 docs/DATA_NOTES.md에 기록(CIS 단일 손익, CF 누적
  의미론, XBRL 차원 구분, KRX 로그인 의무화, LLM 장출력 타임아웃).

## 7. 개발 방식 — AI 협업 자체도 과제의 일부 (D8)

메인 세션(설계·명세·병합·품질 게이트·검증)과 워크트리 격리 병렬 구현 에이전트
(마일스톤별 파일 소유권·인터페이스 계약을 명세로 고정)로 분업했다. 4개 웨이브·
12개 병렬 트랙, 병합 충돌은 import 1건(설계 단계에서 차단). 에이전트의 명세 이탈은
전부 사유와 함께 기록·심사했고, 잘못된 방향(지연시간 기준의 품질 파라미터 축소)은
근본 원인(타임아웃)을 고쳐 되돌렸다 — 이 사건 자체가 docs/PROGRESS.md #4에 남아 있다.

## 8. 한계·후속 (조용히 누락하지 않은 것들)

- 공시 원문 분석(P1-09: 사업보고서 텍스트 섹션 추출)·산업/뉴스 Evidence — MVP 범위
  밖(보고서의 산업 서술은 missing_information으로 정직하게 표기됨).
- 강건성 §24.2의 인샘플/아웃오브샘플·파라미터 민감도·시장 국면 — skipped로 기록.
- 분기 대조 REQUIRES_REVIEW 30건(원인 분류 저장) — 후속 규칙 고도화 대상.
- OFS(별도) 기준 Evidence, 다기업 일반화(§32) — 후순위.
