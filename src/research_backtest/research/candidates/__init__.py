"""AI 후보 생성 (C1' — 명세 docs/specs/W3b-candidates-strategy.md §2, 1804 §4·§7).

Evidence Store가 만든 결정적 재무 Evidence를 LLM에 넘겨, 사용자가 검토·선택할
**후보**만 생성한다 — 최종 투자 판단이 아니다(docs/AI_ROLE_BOUNDARY.md §1):

- :func:`generate_candidate_analysis` — Evidence → :class:`CandidateAnalysis`
  (사실·해석 후보·관계 후보·상충 근거 정리, 1804 §4).
- :func:`generate_hypothesis_candidates` — 위 분석 → 참고용
  :class:`HypothesisCandidate` 목록(승인 가설이 아님, 1804 §7).

두 함수 모두 LLM 출력의 evidence_id가 프롬프트에 제공한 evidence의 부분집합인지
재시도 루프에서 기계적으로 강제하고(1804 §4-2·3), ``generated_by``·
``prompt_version`` 같은 저작 필드는 LLM 출력에서 받지 않고 코드가 주입한다.
"""

from research_backtest.research.candidates.generator import (
    PROMPTS_DIR,
    generate_candidate_analysis,
    generate_hypothesis_candidates,
    select_evidence_for_prompt,
)

__all__ = [
    "PROMPTS_DIR",
    "generate_candidate_analysis",
    "generate_hypothesis_candidates",
    "select_evidence_for_prompt",
]
