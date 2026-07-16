# 진행 기록 (Progress Log)

웨이브 경계마다 스냅샷을 추가한다(최신이 위). 결정의 근거는 MILESTONES.md,
실데이터 관찰은 DATA_NOTES.md, 요구 변경 원문은 1804_FEEDBACK.md 참고.

---

## 중간 기록 #5 — 2026-07-15 (Wave 3c 완료 — 전 마일스톤 종료)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 전부 완료 | **A0~A6 · B1~B4 · H1 · C1'~C3'** — CLI 19명령 전부 실구현(스텁 0) + Streamlit 7화면 + 문서 재편(§25) |
| ⏭ 남은 작업 | **제출물 마감만**: 과제1(run 보고서)·과제2(SOLUTION_OVERVIEW + ai_usage_log + 프롬프트 v1 4종) PDF 변환·패키징 |

### 품질 게이트 (main)

pytest **837 passed·4 skipped**(4:05)(live LLM·실데이터 포함), ruff·format 클린, mypy strict 179파일 0 이슈.
(streamlit 의존성 추가로 pyarrow 17→24 드리프트 발생 — 즉시 수리, 9206e8d.)

### Wave 3c 하이라이트

1. **15-섹션 보고서 실생성**(E2E run 재사용): 제목 논지형 — **"SK하이닉스: 실적
   턴어라운드와 외인 순매수가 겹칠 때만 돌파를 신뢰한다"**(사용자 core_thesis 그대로).
   저작 태그 21곳([사용자 작성]/[Python 계산]/[AI 후보·초안 — 사용자 승인]), §10.2에
   AI 설명 초안(실 Haiku — 양면 서술·의견 없음), ai_usage_log 4건째 기록.
2. **강건성 분석이 실제 인사이트 산출**(§24.3 조건 제거, 실측): 가격만 +261.9%/31거래 ·
   실적만 +246.5%/35거래 · 실적+수급+가격(승인 전략) +110.8%/5거래 — **3중 교집합이
   거래를 과필터링**함을 정량 노출. 비용 0/1/2배(110.76→106.58%), 하위 기간 전반부
   0거래(시간 편향). 연구 경로 결과가 승인 백테스트와 일치함을 이중 자기검증.
3. **Streamlit 7화면**(1804 §15): CLI와 동일한 core API·게이트(화면 잠금 = CLI 허용
   상태 집합), AppTest 5케이스 — 화면 ③ 저장이 CLI 산출물·전이와 바이트 동일함을
   라운드트립으로 검증. LLM 버튼 외 live 호출 0.
4. **문서 재편(§25·정오표 6)**: 명세 전문 → docs/PROJECT_SPEC.md(원본 보존),
   README → 실행 가이드 재작성, 과제 해결 정리 docs/SOLUTION_OVERVIEW.md 신설.

### 에이전트 운영

R1=Opus(+2,941줄, 보고서·강건성) ∥ S1=Sonnet(+2,796줄, UI) — 충돌 0. S1이 pyarrow
드리프트를 조기 보고(메인이 수리). 명세: docs/specs/W3c-report-ui.md.

### 다음

제출물 패키징(과제1·2). 기능 개발은 종료 — 이후 변경은 제출 품질 향상만.

---

## 중간 기록 #4 — 2026-07-15 (Wave 3a·3b 완료 — 첫 live LLM E2E)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | ~#3까지 전체 + **Wave 3a: L1 core/llm(Haiku·구독 OAuth) · E1 Evidence Store** + **Wave 3b: C1' 후보 생성기(research·generate-candidates 실구현) · C2' 전략 초안(generate-strategy-draft 실구현)** |
| ⏭ 남은 작업 | **C3'**: 15-섹션 보고서·generate-report·result_explanation 초안·Streamlit 7화면·강건성·문서 재편(§25) → 제출물 마감(과제1·2 PDF) |

### 품질 게이트 (main)

pytest **807 passed·4 skipped**(live LLM·실데이터 integration 포함, 4:14), ruff·format 클린, **mypy strict 164파일 0 이슈**. unit 768 passed.

### Wave 3 하이라이트

1. **첫 live LLM E2E 관통** (run 20260715_152048, CLI만·수동 전진 0회):
   `research`(evidence 122건 + Haiku 후보 생성: findings 8범주·가설 후보 4건, 각 1시도) →
   create-analyst-view → create-hypothesis(APPROVED) → `generate-strategy-draft`(Haiku 1시도,
   24초 — FundamentalsFlowBreakout, §22 체결 고정·A5 컴파일 통과) → approve-strategy →
   backtest(+110.76%·5거래·PF 8.13 — §23.4 기본 전략과 동형 규칙으로 수렴해 Wave 2 수치 재현) →
   submit-interpretation → **COMPLETE**. `ai_usage_log.jsonl` 3건(전부 claude-haiku-4-5·프롬프트 v1) —
   과제 2 증빙 라인 가동.
2. **Evidence Store PIT 실증**: as_of=2025-12-31 → 122건(5카테고리, 흑자전환 서사 최상위),
   as_of=2023-06-30 재실행 → 미래 공시 유입 0건. evidence_id 봉쇄 validator가 LLM의
   근거 인용을 기계적으로 강제(위반 시 재시도 피드백).
3. **D2 확정 가동**: Claude Agent SDK + 구독 OAuth(ANTHROPIC_API_KEY 동시 설정 차단),
   모델은 configs/llm.yaml에 claude-haiku-4-5-20251001 핀(저가형 — 사용자 지시), 토큰
   비노출 자동 테스트, JSON 재시도 루프(코드펜스 실측 반영).
4. **운영 사건 기록(D8 교훈)**: C1' 에이전트가 timeout 120초 제약 아래 지연 실험(live 반복
   호출)·max_evidence 60→15 축소로 이탈 → 사용자 중단. 근본 원인은 timeout(출력 긴 호출
   실측 124~127초)으로 확인, 360초 상향(4829fe3) 후 **사용자 결정으로 메인 세션이 직접
   마무리**: max_evidence 60 복원(15는 CASH_FLOW·STABILITY 카테고리 누락 유발), 프롬프트
   항목 상한(범주별 ≤3)은 유지, live 재검증 후 병합.

### 에이전트 운영

Wave 3a: L1=Sonnet(+1,498줄) ∥ E1=Opus(+1,743줄) — 공유 파일 0, 충돌 0.
Wave 3b: C2'=Sonnet(+1,069줄) ∥ C1'=Opus(+1,210줄, 중단 후 메인 마무리) — hitl_flow.py
구역 편집, 충돌은 import 1건(메인 union 해소). 명세: docs/specs/W3a·W3b.

### 다음

C3' (보고서·Streamlit·강건성·문서 재편) → 제출물 마감.

---

## 중간 기록 #3 — 2026-07-15 (CLI 통합 패스 완료)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | Wave 1·2 전체 + **CLI 통합 패스** (r2b 18명령 — 데이터 6 + HITL 8 + 보강 create-run·runs·status + 스텁 research) |
| ⏭ 남은 작업 | Wave 3: C1' Evidence+후보 생성 · C2' 전략 초안+리뷰 → C3' 보고서·Streamlit·강건성 · 제출물 마감 |

### 품질 게이트 (main)

pytest **689 passed·4 skipped**(전 integration 포함), ruff·format 클린, **mypy strict 132파일 0 이슈**.

### CLI 통합 패스 하이라이트

1. **HITL 체인이 CLI로 실데이터 관통**: `create-run`(DART 식별·데이터 준비 검사·RunManifest)
   → create-analyst-view → create-hypothesis(APPROVED) → approve-strategy(초안 동등성·
   diff 정합·A5 재컴파일 5단계 검증) → `backtest --run-id` → submit-interpretation(가설
   판정 status 반영) → COMPLETE. C1'·C2' 미구현 구간은 auto_approved=True 수동 전진으로 대체.
2. **백테스트 CLI가 Wave 2 기록 재현**: §23.4 전략, 2016~2025 — 누적 +110.76% · 5거래 ·
   승률 60% · PF 8.13 · MDD −16.87% · KOSPI 대비 −8.87%p (PROGRESS #2와 일치 = 회귀 없음).
3. **게이트 강제 상시화**: 승인 게이트 차단 전용 종료 코드 **4** 신설(미달 상태·미승인
   가설·COMPLETE 재백테스트 거부), AI 스텁 3종도 게이트 검사 후 exit 2. evidence 검증
   생략 경로 없음.
4. `collect-financials --include-xbrl` 실연결(B1), `reconcile-financials`는 전량 대조 후
   --year/--report 표시 필터(총 290 = 연간 70 MATCH 100% + 분기 REQUIRES_REVIEW 30 → 기본
   exit 0, --strict exit 1), RunManifest 구현 보강(OUTPUT_SCHEMA §0.1).

### 에이전트 운영 (전부 워크트리 격리 → 메인 병합·재검증)

T1=Opus(+1,623줄/8파일, 데이터·백테스트) ∥ T2=Sonnet(+1,903줄/6파일, HITL 워크플로) —
2트랙 병렬, cli.py 단독 소유(T1)·공용 `__init__.py` 바이트 고정으로 **병합 충돌 0**.
명세 이탈 0(미규정 판단 4건은 코드 주석·보고 기록). 등록 배선·전체 게이트·실데이터
스모크는 메인 세션이 수행(계약: docs/specs/CLI-integration.md).

### 다음

Wave 3 (C1' ∥ C2' → C3'). LLM은 Claude Agent SDK + 구독 OAuth 토큰(발급 완료, D2 재개정)
— 착수 시 스모크 테스트부터(CLAUDE.md §4).

---

## 중간 기록 #2 — 2026-07-14 (Wave 2 완료)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | Wave 1 전체(A0~A5·B1+B2·H1) + **Wave 2 전체: A6 백테스트 엔진 · B3 API-XBRL 대조 · B4 정정공시 PIT** |
| ⏭ 남은 작업 | CLI 통합 패스(메인 세션 — build-financials·parse-xbrl·reconcile-financials·backtest + HITL 명령 8종) → Wave 3: C1' Evidence+후보 생성 · C2' 전략 초안+리뷰 · C3' 보고서·Streamlit·강건성 |

### 품질 게이트 (main)

pytest **623 passed**(전 integration 포함: 실 API + DATA_DIR 실데이터 백테스트), ruff·format 클린, **mypy strict 124파일 0 이슈**. 승인 게이트가 실행 경로에서 강제됨(미승인 → ApprovalGateError·산출물 미생성, 테스트 고정).

### Wave 2 하이라이트

1. **첫 실데이터 백테스트** (§23 기본 전략, 000660, 비용 반영):
   거래 5건(전부 2024~2025) · 누적 **+110.8%** · MDD −16.9% · 승률 60% · PF 8.13 ·
   노출률 5~10% · 2021~2025 KOSPI 대비 **+67.6%p** (2016~2025 전체로는 −8.9%p, 자산 B&H ~20배에는 크게 미달 — 노출률 관점 분리 필요).
   손절 갭 리스크 실증(-10% 기준, 실현 -12.9%) 등 관찰 5건 → DATA_NOTES.
2. **API-XBRL 대조 100%**: 연간 5개년×연결/별도×7계정 70건 전량 MATCH. README §34 서사의 실증.
3. **PIT 버전 관리 실데이터 재현**: 2020.12 원본→기재정정 체인, as_of별 가시 버전 경계값 정확.
4. 룩어헤드 방어 3중화: as-of join 방어선(LookaheadError) + §28.3 테스트 3종 + 절단 불변 property.

### 에이전트 운영 (전부 워크트리 격리 → 메인 병합·재검증)

A6=Opus(+2,550줄) · B3=Opus(+1,958줄) · B4=Sonnet(+721줄) — 3트랙 병렬, 명세 이탈은 전부 사유 기록 후 수용(예: A6 워밍업 보존은 절단 불변 property의 전제).

### 다음

CLI 통합 패스(메인 세션) 후 Wave 3. LLM(OpenRouter) 키는 여전히 미확보 — C1'·C2'는 fake 클라이언트+프롬프트 파일로 선구현, 키 확보 시 live 전환.

---

## 중간 기록 #1 — 2026-07-14 (Wave 1 완료 직전, Wave 2 착수 전)

### 마일스톤 보드

| 상태 | 마일스톤 |
|---|---|
| ✅ 병합 완료 | A0 기반 구축 · A1 기업식별 · A2 재무API 수집 · A3 시장데이터(캘린더 포함) · **A4 재무 정규화** · **A5 전략 DSL** · **B1+B2 XBRL 수집·파싱** |
| 🔄 실행 중 | **H1 HITL 기반 계층** (Sonnet, 워크트리) — 병합 대기 |
| ⏭ Wave 2 예정 | A6 백테스트 엔진(승인 게이트 내장) ∥ B3 API-XBRL 교차검증 ∥ B4 정정공시 PIT |
| ⏭ Wave 3 예정 | C1' Evidence+후보 생성 · C2' 전략 초안+리뷰 · C3' 보고서·Streamlit (HITL v2 흐름) |

### 품질 게이트 (main 기준)

- pytest **376 passed**(실 API integration 포함), ruff + ruff format clean, **mypy strict** 81개 파일 0 이슈
- 룩어헤드 방어 자산: available_from(접수일 다음 거래일, KRX 실캘린더) 550개 fact 전수 검증, 지표 레벨 no-lookahead property 테스트, 캘린더 coverage 밖 즉시 예외

### 데이터 자산 (data/, 전부 재현 가능·미커밋)

| 자산 | 규모 |
|---|---|
| DART 고유번호 | 118,484개사 (캐시) |
| 전체 재무제표 API raw | 40개 응답·6,436행 (2021~2025, CFS·OFS, sha256 보존) |
| 시장 데이터 | OHLCV·수급·KOSPI·거래일 캘린더 각 2,829행 (2015-01-02~2026-07-13) |
| XBRL 원본 | 22건(기재정정 1건 포함)·230MB, manifest·checksum 보존 |
| normalized 재무 | facts 550 · quarterly 40 · annual 10 · **metrics 136**(YoY 등, available_from 부여) |

### 이번 구간의 중요 사건

1. **요구사항 v2 (Human-in-the-Loop) 전환** — 원문 1804_FEEDBACK.md. AI 단독
   분석·가설 확정 구조 폐기, 사용자 관점·가설·승인·해석 중심으로 재설계(D9).
   기존 코드 충돌 0건(해당 영역이 미구현이라 처음부터 HITL로 설계).
   문서 3종 신설(HUMAN_IN_THE_LOOP·AI_ROLE_BOUNDARY·OUTPUT_SCHEMA) + README §1.1 개정.
2. **KRX 로그인 의무화 발견·대응** (D1 개정) — 수급·지수는 KRX_ID/PW 필요,
   어댑터가 부분 수집 모드 지원.
3. **실측이 설계를 세 번 고쳤다**: ① SK하이닉스 손익은 전부 CIS(→registry
   statement_types 복수화) ② 분기 CF는 thstrm이 누적(손익과 정반대 → 차분 파생)
   ③ XBRL 연결·별도는 파일이 아닌 차원 구분(→README §10.1 규칙 수정 필요, B3 반영 예정).

### 병렬 에이전트 운영 기록 (D8)

| 트랙 | 모델 | 산출 규모 | 검증(각 워크트리 → main 병합 후) |
|---|---|---|---|
| A1~A3 (순차기) | 정책 수립 전(메인 모델 상속) | dart·market 계층 | 각 마일스톤 DoD + 메인 세션 재검증 |
| A4 재무 정규화 | Opus | +2,905줄/17파일 | 211p(워크트리) → main 376p |
| A5 전략 DSL | Sonnet | +1,894줄/12파일 | 252p(워크트리) → main 260p |
| B1+B2 XBRL | Opus | +2,409줄/15파일 | 211p(워크트리) → main 314p |
| H1 HITL 기반 | Sonnet | 실행 중 | — |

방식: 메인 세션(Fable)이 명세(docs/specs/)·병합·품질 게이트·커밋 담당,
에이전트는 자기 소유 파일만 수정하고 워크트리 브랜치에 커밋(하위 에이전트 Fable 금지 — D8).

### 리스크·보류 사항

- **OpenRouter 키 미확보** — Phase C의 live LLM 호출 차단 요소(구조는 fake 클라이언트로 선구현 가능). 모델은 `inclusionai/ling-2.6-flash:free` 예정(D2).
- H1 병합 전 — Wave 2의 A6는 H1의 승인 게이트 API에 의존(명세 계약으로 선정의됨).
- 토스증권 API(.env.example의 CLIENT_ID/SECRET)는 전략 시행 테스트용 후순위로 등록만 됨.
- CLI의 HITL 명령 8종(generate-candidates ~ generate-report)은 H1 병합 후 메인 세션이 연결.

### 다음 (Wave 2)

- **A6**: A4 metrics as-of join + A5 컴파일 계약 + H1 게이트(승인 전략만 실행) + 다음날 시가 체결·비용·성과지표 + 룩어헤드 테스트(§28.3)
- **B3**: API-XBRL 대표 계정 교차검증 — Context 선택 규칙은 실측 반영("연결/별도 축 하나만 있는 context")
- **B4**: 정정공시 버전 그래프 — 실데이터 케이스(2020.12 원본+기재정정 쌍) 확보됨
