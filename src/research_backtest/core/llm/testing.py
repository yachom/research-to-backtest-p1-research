"""LLM 테스트 더블 — FakeLlmClient (명세 docs/specs/W3a-llm-evidence.md §2.5).

core/llm 자체 단위 테스트와 Wave 3b(C1' 후보 생성기 ∥ C2' 전략 초안) 소비자가
공용으로 쓰는 공식 :class:`~research_backtest.core.llm.client.LlmTextClient`
테스트 더블이다 — 네트워크 호출 없이 등록된 응답을 순서대로 재생한다.
"""

from __future__ import annotations

from collections.abc import Sequence

from research_backtest.core.llm.client import LlmCallMetadata

#: FakeLlmClient가 반환하는 고정 메타데이터 (명세 §2.5 — model="fake", 토큰 None).
FAKE_LLM_METADATA = LlmCallMetadata(
    model="fake",
    num_attempts=1,
    duration_ms=0,
    input_tokens=None,
    output_tokens=None,
    cost_usd_notional=None,
)


class FakeLlmClient:
    """:class:`LlmTextClient`\\ 의 결정적 테스트 더블 (명세 §2.5).

    ``responses``를 생성자에 넘긴 순서대로 ``complete_text`` 호출마다 하나씩
    돌려준다. 응답이 소진되면 ``AssertionError``(테스트가 예상보다 많이
    호출했다는 설계 오류를 조기에 드러낸다). 모든 호출은 ``calls``에
    ``(system_prompt, user_prompt)`` 튜플로 순서대로 기록된다.
    """

    def __init__(self, responses: Sequence[str]) -> None:
        self._responses = list(responses)
        self._next_index = 0
        self.calls: list[tuple[str, str]] = []

    def complete_text(self, *, system_prompt: str, user_prompt: str) -> tuple[str, LlmCallMetadata]:
        self.calls.append((system_prompt, user_prompt))
        if self._next_index >= len(self._responses):
            raise AssertionError(
                f"FakeLlmClient 응답이 소진되었습니다 "
                f"({len(self._responses)}개 등록, {self._next_index + 1}번째 호출됨)."
            )
        response = self._responses[self._next_index]
        self._next_index += 1
        return response, FAKE_LLM_METADATA
