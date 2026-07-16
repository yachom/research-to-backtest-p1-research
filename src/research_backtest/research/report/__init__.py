"""15-섹션 최종 보고서 생성 (docs/HUMAN_IN_THE_LOOP.md §6, 1804 §16, 명세 W3c §2.2).

run 산출물 전부(manifest·evidence·candidate_analysis·analyst_view·hypothesis·
review·backtest_result·interpretation·ai_usage_log)를 조합해 HITL §6의 15개 섹션
마크다운 보고서를 만든다. 각 섹션·단락에 저작 주체(사용자/Python/AI 초안)를
표기해 "AI 초안 vs 사용자 해석"의 구분을 흐리지 않는다(1804 §16).
"""

from research_backtest.research.report.builder import (
    build_research_report,
    draft_result_explanation,
)

__all__ = ["build_research_report", "draft_result_explanation"]
