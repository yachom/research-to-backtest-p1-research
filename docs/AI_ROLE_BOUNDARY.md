# AI 역할 경계 (요구사항 v2, 2026-07-14)

본 프로젝트는 AI가 투자 판단을 대신하는 시스템이 아니다. AI·사용자·Python의
경계를 코드와 데이터 모델 수준에서 강제한다 (docs/HUMAN_IN_THE_LOOP.md와 한 쌍).

## 1. 역할 분담

| 주체 | 담당 | 금지 |
|---|---|---|
| **Python** | 데이터 수집·정규화·지표 계산·백테스트·성과지표 | — |
| **AI** | ① 사실·후보 관계·상충 근거 정리(CandidateAnalysis) ② 참고용 가설 후보(hypothesis_candidates — 승인 가설 아님) ③ 승인된 인간 가설의 전략 DSL **초안** ④ 백테스트 결과의 사실 요약 초안 | 최종 투자 의견 확정, evidence 밖 사실 사용, 근거 없는 주장, 가설·전략·해석의 최종 확정 |
| **사용자** | 분석 질문·핵심 논지, 근거 선택/제외와 그 이유, 투자 가설 작성·승인, 전략 검토·수정·승인, 결과 해석·가설 판정 | — |

## 2. 저작 구분 (content_origin)

모든 서술형 콘텐츠는 내부적으로 저작 출처를 저장한다:

```text
SOURCE_FACT               # 공시·데이터 원문 사실
PYTHON_CALCULATION        # 엔진 계산값
AI_CANDIDATE              # AI가 정리한 후보(참고용)
HUMAN_ANALYSIS            # 사용자 분석(AnalystView)
HUMAN_HYPOTHESIS          # 사용자 가설
AI_DRAFT_HUMAN_APPROVED   # AI 초안을 사용자가 검토·승인
HUMAN_INTERPRETATION      # 사용자 결과 해석
```

`AuthoredContent` 모델(content, content_origin, author, source_ids, ai_usage_id)로
운반하며, AI 산출물에는 프롬프트 버전 파일과 호출 기록(ai_usage_id)을 연결한다.

## 3. 코드 수준 강제 장치

1. AI 생성 후보와 인간 작성 가설은 **다른 모델·다른 파일**에 저장한다
   (hypothesis_candidates.json ≠ human_investment_hypothesis.json).
2. 전략 변환기는 `HumanInvestmentHypothesis.status == APPROVED`만 입력으로 받는다
   — 아니면 예외. AI 초안이라도 최종 승인 주체(approved_by)와 시각(approved_at)을 저장한다.
3. 백테스트는 StrategyReview(사용자 승인 기록) 없이 실행되지 않는다.
   AI 초안과 최종 전략의 차이는 StrategyModification으로 필드 단위 기록한다.
4. 상태 머신(run_state.json)이 게이트를 강제하고, 테스트용 자동 승인은
   `auto_approved: true`로 산출물에 명시된다.
5. AI 프롬프트에는 §HUMAN_IN_THE_LOOP.md 5절의 제약을 포함하고, 프롬프트는
   버전 파일로 저장한다(과제 2 "무엇을 어떤 도구·프롬프트로 해결했는지"의 증빙).

## 4. 과제 대응 관계

- **과제 1 (기업·산업 분석, 본인의 시각 필수)**: AnalystView·HumanInvestmentHypothesis·
  BacktestInterpretation이 "본인의 시각"의 실체이며, 보고서 15개 섹션 중 사용자
  작성 섹션(2·3·5·6·7·8·13·14)이 이를 드러낸다.
- **과제 2 (AI 활용 검증)**: CandidateAnalysis·전략 초안·프롬프트 버전 파일·
  StrategyModification(초안 대비 수정 이력)·보고서 15절(AI/사용자 작업 구분)이 증빙이다.
