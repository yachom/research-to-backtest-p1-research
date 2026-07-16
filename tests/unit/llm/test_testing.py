"""core/llm/testing.py(FakeLlmClient) 단위 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.5).

Wave 3b가 그대로 소비하는 공식 테스트 더블이므로 계약 자체를 명시적으로
검증한다: 순서대로 재생, 호출 기록, 소진 시 AssertionError, 고정 메타데이터.
"""

import pytest

from research_backtest.core.llm.testing import FakeLlmClient


def test_responses_are_replayed_in_order() -> None:
    client = FakeLlmClient(["first", "second", "third"])
    text1, _ = client.complete_text(system_prompt="sys", user_prompt="u1")
    text2, _ = client.complete_text(system_prompt="sys", user_prompt="u2")
    text3, _ = client.complete_text(system_prompt="sys", user_prompt="u3")
    assert (text1, text2, text3) == ("first", "second", "third")


def test_calls_are_recorded_with_prompts() -> None:
    client = FakeLlmClient(["r1", "r2"])
    client.complete_text(system_prompt="sys-1", user_prompt="user-1")
    client.complete_text(system_prompt="sys-2", user_prompt="user-2")
    assert client.calls == [("sys-1", "user-1"), ("sys-2", "user-2")]


def test_exhausted_responses_raise_assertion_error() -> None:
    client = FakeLlmClient(["only-one"])
    client.complete_text(system_prompt="sys", user_prompt="u")
    with pytest.raises(AssertionError, match="소진"):
        client.complete_text(system_prompt="sys", user_prompt="u2")


def test_empty_responses_raises_immediately() -> None:
    client = FakeLlmClient([])
    with pytest.raises(AssertionError, match="소진"):
        client.complete_text(system_prompt="sys", user_prompt="u")


def test_metadata_is_fixed_fake_value() -> None:
    client = FakeLlmClient(["x"])
    _, metadata = client.complete_text(system_prompt="sys", user_prompt="u")
    assert metadata.model == "fake"
    assert metadata.num_attempts == 1
    assert metadata.input_tokens is None
    assert metadata.output_tokens is None
    assert metadata.cost_usd_notional is None
