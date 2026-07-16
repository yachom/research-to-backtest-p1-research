"""Human-in-the-Loop 기반 계층 (요구사항 v2, docs/specs/H1-hitl-foundation.md).

AI는 사실과 후보 관계를 정리하고 사용자의 가설을 구조화하는 보조 도구다. 분석
관점·핵심 논지·근거 선택·투자 가설·전략 승인·결과 해석은 사용자가 담당한다
(docs/HUMAN_IN_THE_LOOP.md, docs/AI_ROLE_BOUNDARY.md).

- models: docs/OUTPUT_SCHEMA.md §1~§8 산출물 모델 (CandidateAnalysis·AnalystView·
  HumanInvestmentHypothesis·HypothesisCandidate·StrategyReview·BacktestInterpretation·
  AIUsageRecord·AuthoredContent 등)
- states: PipelineState 12종 + 전이 규칙 + RunState
- gates: 승인 게이트 (AI_ROLE_BOUNDARY.md §3의 코드화)
- store: outputs/{run_id}/ 산출물 저장소 (RunStore)
- validation: AnalystView·HumanInvestmentHypothesis 외부 참조 검증 (EvidenceStore)
- diff: 전략 초안 vs 최종 승인본 dot-path diff

이 계층은 LLM 호출·CLI 연결·Streamlit·보고서 생성을 포함하지 않는다(비범위 —
C1'·C2'·A6·C3'가 이 계층 위에 얹힌다).
"""
