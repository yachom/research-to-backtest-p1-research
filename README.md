> **⚠️ 미러 레포(읽기 전용) — Project 1 — 기업 리서치·투자 가설 (미러)**
> 정본·실행·이슈 관리는 상위 레포 [research-to-backtest](https://github.com/yachom/research-to-backtest)에서 한다.
> 이 미러는 해당 프로젝트 관점의 코드·문서·제출물만 담은 뷰이며(반대편 프로젝트 디렉토리 제외),
> 일부 모듈이 상위의 공용 코드를 참조하므로 **단독 실행은 지원하지 않는다**.
> 범위: Evidence Store → AI 분석·가설 후보 → 사용자 관점·가설 → 15-섹션 보고서

# Research-to-Backtest

DART·XBRL·시장 데이터 기반 기업 리서치에서 Point-in-Time 백테스트까지를 하나의
파이프라인으로 잇는 채용 과제 프로젝트다. **AI는 후보 정리와 초안만 담당하고,
분석 관점·근거 선택·투자 가설·전략 승인·결과 해석은 전부 사용자가 수행한다**
(Human-in-the-Loop v2 — 정본: `1804_FEEDBACK.md`).

- **Project 1 (research/)** — 기업 리서치: 공시·재무 데이터를 수집·정규화해 Evidence를
  만들고, AI가 분석 후보(CandidateAnalysis)와 참고용 가설 후보를 정리하면 사용자가
  관점(AnalystView)과 투자 가설(HumanInvestmentHypothesis)을 작성·승인한다.
- **Project 2 (quant/)** — 전략 검증: 승인된 가설을 AI가 전략 DSL 초안으로 변환하고,
  사용자가 검토·수정·승인(StrategyReview)한 전략만 Point-in-Time 백테스트로 검증한다.
- 두 프로젝트는 **core/**(공용 데이터 플랫폼: DART·XBRL·시장 데이터·재무 정규화·HITL
  상태 머신·LLM 클라이언트)를 공유하며, P1→P2 계약은 코드가 아닌 **산출물**(승인 가설
  JSON + PIT 데이터셋)이다. 기술 명세 전문(v1.0 원본)은 `docs/PROJECT_SPEC.md`.

## 1. 설치·환경

```bash
make install          # python3 venv + pip install -e ".[dev]"
cp .env.example .env  # 키 입력 (아래)
```

| 키 | 용도 | 필수 |
|---|---|---|
| `DART_API_KEY` | OpenDART 수집·기업 식별 | ✅ |
| `KRX_ID` / `KRX_PW` | 투자자 수급·지수·거래일 캘린더 (없으면 가격만 부분 수집) | 권장 |
| `CLAUDE_CODE_OAUTH_TOKEN` | LLM(Claude Agent SDK, 구독 인증) — AI 후보·전략 초안·결과 설명 | AI 단계만 |

LLM은 `configs/llm.yaml`의 `claude-haiku-4-5-20251001`로 고정되어 있고(저가형,
재현성 핀), `ANTHROPIC_API_KEY`와 **동시 설정 금지**(의도치 않은 과금 방지 — 코드가
차단한다). 모든 AI 호출은 `outputs/{run_id}/ai_usage_log.jsonl`에 기록된다.

## 2. 데이터 준비 (한 번)

```bash
r2b collect-financials --company 000660 --from-year 2015 --to-year 2025 --include-xbrl
r2b collect-market     --company 000660                  # KRX 캘린더·수급·지수 포함
r2b build-financials   --company 000660                  # 정규화·단독분기·지표·available_from
r2b reconcile-financials --company 000660                # API↔XBRL 교차검증 (연간 100% MATCH)
r2b parse-xbrl --corp-code 00164779 --rcept-no <접수번호> # 개별 파싱(선택 — reconcile이 전량 보장)
```

모든 재무값에는 `available_from`(접수일 다음 거래일, KRX 실캘린더)이 붙고, 백테스트는
as-of join으로만 결합한다 — **분석 기준일 이후의 공시는 어떤 단계에도 유입되지 않는다.**

## 3. HITL 리서치 → 백테스트 (run 단위)

```bash
r2b research --company 000660 --as-of-date 2025-12-31   # run 생성 + Evidence + AI 후보 (LLM)
r2b status --run-id <run_id>                            # 상태·전이 이력·산출물 체크리스트
r2b create-analyst-view --run-id <run_id> --input analyst_view.json          # 사용자
r2b create-hypothesis   --run-id <run_id> --input hypothesis.json            # 사용자(승인 포함)
r2b generate-strategy-draft --run-id <run_id>           # 승인 가설 → DSL 초안 (LLM)
r2b approve-strategy    --run-id <run_id> --review strategy_review.json      # 사용자
r2b backtest            --run-id <run_id>               # 승인 전략만 실행 (PIT)
r2b submit-interpretation --run-id <run_id> --input interpretation.json      # 사용자
r2b generate-report     --run-id <run_id>               # 15-섹션 보고서 + 강건성 분석
```

- 승인되지 않은 단계는 건너뛸 수 없다 — 12-상태 머신과 승인 게이트가 강제하며, 게이트
  위반은 **종료 코드 4**로 구분된다. `r2b runs`로 전체 실행 목록을 본다.
- (선택) 같은 흐름을 열람용 Streamlit 뷰어로도 볼 수 있다 —
  `python -m streamlit run src/research_backtest/app/streamlit_app.py` (CLI와 동일 게이트,
  신규 기업은 화면 ①에서 수집·빌드 원클릭). 정식 실행 경로는 위 CLI다.

## 4. 품질 게이트·테스트

```bash
make check   # ruff + format --check + mypy(strict) + pytest
# live LLM·실 API 포함 전체:
set -a && source .env && set +a && DATA_DIR=$PWD/data .venv/bin/python -m pytest
```

인증·실데이터가 없으면 해당 integration 테스트는 자동 skip된다. 룩어헤드 방어는
3중(available_from as-of join 방어선 + 지표 레벨 no-lookahead property + 절단 불변)이며
위반 시 `LookaheadError`로 실행이 중단된다.

## 5. 레포 구성 (멀티레포)

이 레포가 **정본**이며 실행·관리는 전부 여기서 한다. 프로젝트 1·2는 과제별 열람용
**미러 레포**로도 제공되고, 아래 서브모듈로 상위에서 하나로 관리된다:

| 레포 | 역할 |
|---|---|
| [research-to-backtest](https://github.com/yachom/research-to-backtest) | **정본 모노레포** (이 레포) — 유일한 실행·수정 지점 |
| [research-to-backtest-p1-research](https://github.com/yachom/research-to-backtest-p1-research) | Project 1 미러 — 리서치·가설 관점 뷰 (`projects/project1-research`) |
| [research-to-backtest-p2-backtest](https://github.com/yachom/research-to-backtest-p2-backtest) | Project 2 미러 — DSL·백테스트 관점 뷰 (`projects/project2-backtest`) |

미러는 읽기 전용 스냅샷(반대편 프로젝트 디렉토리 제외)이며 정본 갱신 시
`zsh scripts/sync_mirror.sh p1 <url>` / `p2 <url>`로 재생성한다. 제출물은
`submission/`(과제 1 보고서·과제 2 AI 활용 검증, PDF 포함).

## 6. 레이아웃·문서 맵

```text
src/research_backtest/
├── core/      # 공용: DART·XBRL·시장 데이터 ETL, 재무 정규화, HITL 상태·게이트, LLM
├── research/  # Project 1: Evidence Store → AI 후보 생성 → 보고서
├── quant/     # Project 2: 전략 DSL·컴파일러 → 백테스트 엔진 → 강건성
└── app/       # 통합 CLI(r2b) + Streamlit
```

| 문서 | 내용 |
|---|---|
| `docs/PROJECT_SPEC.md` | 기술 명세 전문(v1.0 원본 보존, §1.1만 v2 개정) |
| `1804_FEEDBACK.md` | 요구사항 v2(HITL) 원문 |
| `docs/HUMAN_IN_THE_LOOP.md` · `AI_ROLE_BOUNDARY.md` · `OUTPUT_SCHEMA.md` | v2 구현 관점 정리 |
| `docs/MILESTONES.md` | 실행 계획·결정 기록(D1~D9)·정오표 |
| `docs/PROGRESS.md` | 웨이브별 진행 스냅샷(최신이 위) |
| `docs/DATA_NOTES.md` | 설계를 바꾼 실데이터 관찰 기록 |
| `docs/specs/` | 마일스톤별 구현 계약 |

## 7. 원칙 요약

1. **Point-in-Time**: 재무값은 `available_from` 이후에만, as-of join 외 병합 금지.
2. **승인 게이트**: 미승인 가설·전략은 실행 불가(`core/hitl/gates.py`) — 코드로 강제.
3. **AI/인간 저작 분리**: AI 후보와 인간 가설은 다른 모델·다른 파일, `content_origin` 저장.
4. **증빙**: 모든 LLM 호출은 프롬프트 버전 파일(`*/prompts/*_v1.txt`)과
   `ai_usage_log.jsonl`로 재구성 가능하다.

## 8. 향후 확장 (로드맵)

현재는 **정형 데이터**(가격·수급·XBRL 재무)를 근거로 하고, 사용자가 구조화된 형태(관점·가설·
전략 DSL)로 논지를 입력한다. 자연어 아이디어만으로 전략을 구성하기에는 근거 데이터와 판단
계층이 아직 부족하다. 목표는 **"자연어 투자 아이디어 → 근거 수집 → 에이전트 전략 제시 →
백테스트 → 실집행"** 전 과정의 자동화이며, 다음 순서로 확장한다.

1. **비정형 데이터 수집** — 뉴스·공시 원문·산업 리포트 수집·요약으로 정성 촉매를 근거화.
2. **에이전트 판단 계층** — 정성+정량 근거 종합 후 전략 후보 자동 제시(현재는 사람이 DSL 변환).
3. **자연어 지시 인터페이스** — 자연어 전략 지시를 DSL로 자동 번역(현재는 구조화 입력 필요).
4. **실주문 집행** — 검증 전략을 증권사 주문 API로 실집행(토스증권 키 발급 완료, 데이터
   어댑터와 동일 패턴; 현재 플랫폼은 체결 시뮬레이션까지).