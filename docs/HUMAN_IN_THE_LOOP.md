# Human-in-the-Loop 파이프라인 명세 (요구사항 v2, 2026-07-14)

> **정본 원문: `1804_FEEDBACK.md`** (사용자 작성). 이 문서는 원문의 구현 관점
> 요약이며, 충돌 시 원문이 우선한다.
> 배경: 과제 1은 "본인의 시각과 근거"가 필수인데, 기존 구조(AI 기업분석 → AI
> 투자 가설)는 분석이 AI의 판단처럼 보인다. 따라서 AI를 보조 도구로 재배치한다.
> Python이 산출한 수치는 LLM이 다시 계산하지 않는다(원문 §3.1).

## 1. 핵심 원칙

**AI는 사실과 후보 관계를 정리하고 사용자의 가설을 구조화하는 보조 도구다.**
분석 관점, 핵심 논지, 근거 선택, 투자 가설, 전략 승인, 결과 해석은 **사용자**가 담당한다.

## 2. 변경된 전체 흐름

```text
기업명·분석 기준일 입력
→ 데이터 수집 및 계산                    (Python)
→ AI가 분석 후보와 상충 근거 정리          (AI: CandidateAnalysis)
→ 사용자가 분석 질문과 핵심 논지 작성       (사용자: AnalystView)
→ 사용자가 사용할 근거와 제외할 근거 선택    (사용자: AnalystView)
→ 사용자가 투자 가설 작성                 (사용자: HumanInvestmentHypothesis)
→ AI가 가설을 측정 가능한 전략 DSL 초안으로 변환  (AI: strategy_draft)
→ 사용자가 전략 규칙을 검토·수정·승인       (사용자: StrategyReview)
→ Python 백테스트                       (Python)
→ 사용자가 결과를 해석하고 가설을 채택·수정·기각 (사용자: BacktestInterpretation)
```

## 3. 파이프라인 상태와 승인 게이트

```text
DATA_READY
CANDIDATE_ANALYSIS_READY
AWAITING_ANALYST_VIEW
ANALYST_VIEW_APPROVED
HYPOTHESIS_DRAFT
HYPOTHESIS_APPROVED
STRATEGY_DRAFT_READY
AWAITING_STRATEGY_REVIEW
STRATEGY_APPROVED
BACKTEST_COMPLETE
AWAITING_INTERPRETATION
COMPLETE
```

- 상태는 위 순서로만 전진한다(수정 시 되돌림 전이는 허용: 예 REVISED 가설 → HYPOTHESIS_DRAFT).
- **게이트**: ① AnalystView 검증 통과 없이 가설 단계 진입 불가 ② `status=APPROVED`가 아닌 가설은 전략 변환에 전달 금지 ③ StrategyReview(승인 기록) 없이 백테스트 실행 금지 ④ BacktestInterpretation 없이 COMPLETE 불가.
- 승인되지 않은 단계를 자동으로 건너뛰지 않는다. 단 개발·자동화 테스트용 옵션은 허용:
  `--use-fixture-analyst-view`, `--use-fixture-hypothesis`, `--auto-approve-for-test`
  — 사용 시 상태 기록에 `auto_approved: true`와 사유가 **명시적으로 남아야 한다**.

## 4. 산출물 (outputs/{run_id}/)

전체 파일 목록과 모델 필드 정의는 `docs/OUTPUT_SCHEMA.md` §0~§8 (원문 §20과 일치).
핵심 분리 원칙: AI 참고용 후보(`hypothesis_candidates.json`, HypothesisCandidate)와
사용자 가설(`human_investment_hypothesis.json`)은 **다른 모델·다른 파일**이다.
모든 AI 호출은 `ai_usage_log.jsonl`(AIUsageRecord)에 기록한다 — 기록 대상: 분석 후보,
관계 후보, 전략 DSL 초안, 백테스트 결과 설명 초안(원문 §12).

## 5. AI 프롬프트 제약 (원문 §4 그대로)

1. 최종 투자 의견을 확정하지 않는다.
2. 제공된 evidence package 외의 사실을 사용하지 않는다.
3. 모든 finding에 evidence_id를 연결한다.
4. 사실, 해석 후보, 관계 가설 후보를 구분한다.
5. 주장과 충돌하는 근거도 함께 제시한다.
6. 데이터가 없는 경우 추정하지 않는다.
7. 사용자가 선택할 수 있도록 복수 후보를 제시한다.

프롬프트는 버전 관리되는 파일로 저장한다(과제 2 증빙, 경로는 D3·D7 레이아웃 적용):
`src/research_backtest/research/prompts/{candidate_analysis,hypothesis_candidate}_v1.txt`,
`src/research_backtest/quant/prompts/{strategy_translation,result_explanation}_v1.txt`

## 5.1 CLI (원문 §14 — `r2b`로 적용, D3)

`generate-candidates` → `create-analyst-view --input` → `create-hypothesis --input`
→ `generate-strategy-draft` → `approve-strategy --review` → `backtest --run-id`
→ `submit-interpretation --input` → `generate-report` (모두 `--run-id` 기반,
현재 상태 표시 포함)

구현 상태(2026-07-15, docs/specs/CLI-integration.md): 8종 전부 CLI로 존재하며
사용자 단계 5종(create-analyst-view·create-hypothesis·approve-strategy·backtest·
submit-interpretation)은 완전 구현, AI·보고서 3종(generate-candidates·
generate-strategy-draft·generate-report)은 게이트·상태 검사까지 수행하는
상태 인지형 스텁(C1'·C2'·C3'에서 실구현). 보강 명령: `create-run --company
--as-of-date`(run 생성 진입점 — §2의 "기업명·분석 기준일 입력" 단계),
`status --run-id`·`runs`(원문 §13의 상태 표시). 승인 게이트 차단은 종료 코드 4.

## 6. 최종 보고서 구조 (15개 섹션, 작성 주체 표기)

1. 분석 대상과 기준일 / 2. 분석 질문(사용자) / 3. 핵심 결론(사용자) /
4. 주요 재무·산업 근거 / 5. 선택한 근거와 이유(사용자) / 6. 제외한 근거와 이유(사용자) /
7. 반대 논리와 불확실성(사용자) / 8. 투자 가설(사용자 작성·승인) /
9. 전략 규칙과 사용자 수정 내역 / 10. 백테스트 결과 / 11. 가설에 유리한 결과 /
12. 가설에 불리한 결과 / 13. 최종 판단(사용자) / 14. 가설 채택·수정·기각 여부 /
15. AI가 수행한 작업과 사용자가 수행한 작업

보고서 제목은 기업명 나열이 아니라 사용자의 논지가 드러나게 구성 가능해야 한다.
예: "SK하이닉스: 실적 회복보다 중요한 것은 시장 기대의 확인이다"

## 7. 기존 코드 마이그레이션 원칙 (v2 원문 유지)

- DART·XBRL 수집, 재무 계산 코드 **유지**. Evidence Store **재사용**.
- 기존 CompanyAnalysis가 있다면 삭제 대신 호환 계층. (현재 미구현 — 해당 없음)
- 기존 AI 투자 가설 생성기 → **HypothesisCandidateGenerator**로 개명·역할 축소. (현재 미구현 — 신규 설계에 반영)
- 전략 생성기는 **승인된 인간 가설만** 입력. 백테스트 엔진 수정 최소화.
- 보고서 생성기는 새 산출물 조합으로 변경.

## 8. Streamlit 화면 (원문 §15 — 7개 화면, C3'에서 구현)

① 기업·분석 기준일 입력(기간·초점 포함) ② AI 분석 후보 검토(재무/산업/촉매/위험/상충
근거 + 선택·제외 체크박스) ③ 본인의 분석 관점 작성 ④ 투자 가설 작성·승인
⑤ 전략 초안 검토(진입·청산 규칙, 체결 시점, 임계값 수정, 수정 이유, 승인)
⑥ 백테스트 결과(성과지표·차트·거래내역·조건 제거·벤치마크) ⑦ 최종 해석(유리·불리한
결과, 판정·이유, 수정 가설, 추가 검증).
UI에는 저작 구분 태그를 노출하지 않아도 되지만 내부 모델에는 content_origin을 항상 저장한다.
