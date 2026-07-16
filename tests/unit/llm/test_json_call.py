"""core/llm/json_call.py 단위 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.3).

``extract_json``은 결정적·순수 함수 — 코드펜스·이중 텍스트(설명문에 둘러싸인
JSON)·비JSON 케이스를 검증한다. ``complete_validated``는 FakeLlmClient(및
메타데이터 누적 검증용 자체 스크립트 더블)로 재시도 루프를 검증한다 — 네트워크
없음.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest
from pydantic import BaseModel

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.llm.client import LlmCallMetadata
from research_backtest.core.llm.json_call import complete_validated, extract_json
from research_backtest.core.llm.testing import FakeLlmClient

# --- extract_json ----------------------------------------------------------------


def test_extract_json_from_fenced_block_with_language_tag() -> None:
    # 스모크 실측(명세 §2.2②): "1+1" 요청에도 ```json ... ``` 코드펜스로 감싼 응답
    text = '여기 결과입니다:\n```json\n{"ok": true}\n```\n'
    assert extract_json(text) == '{"ok": true}'


def test_extract_json_from_fenced_block_without_language_tag() -> None:
    text = '```\n{"ok": true}\n```'
    assert extract_json(text) == '{"ok": true}'


def test_extract_json_dual_text_surrounding_object() -> None:
    """설명문이 JSON 앞뒤를 감싼 "이중 텍스트" 케이스(코드펜스 없음)."""
    text = '알겠습니다, 결과는 다음과 같습니다: {"ok": true} 도움이 되었길 바랍니다.'
    assert extract_json(text) == '{"ok": true}'


def test_extract_json_array_shaped_payload() -> None:
    """최상위가 배열인 경우 — 리스트 검증 함수 사용 케이스(명세 §2.3)."""
    text = "다음은 후보 목록입니다: [1, 2, 3] 이상입니다."
    assert extract_json(text) == "[1, 2, 3]"


def test_extract_json_object_preferred_when_object_starts_first() -> None:
    text = 'prefix {"a": [1, 2]} suffix'
    assert extract_json(text) == '{"a": [1, 2]}'


def test_extract_json_array_preferred_when_array_starts_first() -> None:
    text = 'prefix [{"a": 1}, {"b": 2}] suffix'
    assert extract_json(text) == '[{"a": 1}, {"b": 2}]'


def test_extract_json_no_json_returns_stripped_original() -> None:
    """비JSON 케이스 — 괄호가 전혀 없으면 원문(공백 정리)을 그대로 반환한다."""
    text = "  이것은 그냥 자연어 응답입니다.  "
    assert extract_json(text) == "이것은 그냥 자연어 응답입니다."


def test_extract_json_is_pure_and_deterministic() -> None:
    text = '```json\n{"a": 1}\n```'
    assert extract_json(text) == extract_json(text)


# --- complete_validated: 성공/재시도/소진 ------------------------------------------


class _OkPayload(BaseModel):
    ok: bool


def _validate_ok(payload: object) -> _OkPayload:
    return _OkPayload.model_validate(payload)


def test_complete_validated_succeeds_on_first_attempt() -> None:
    client = FakeLlmClient(['{"ok": true}'])
    result, metadata = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="user",
        validator=_validate_ok,
        max_attempts=3,
    )
    assert result.ok is True
    assert metadata.num_attempts == 1
    assert len(client.calls) == 1
    assert client.calls[0] == ("sys", "user")


def test_complete_validated_retries_after_non_json_then_succeeds() -> None:
    # DoD: Fake로 1차 비JSON → 2차 성공
    client = FakeLlmClient(["이것은 JSON이 아닙니다.", '{"ok": true}'])
    result, metadata = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="원 요청",
        validator=_validate_ok,
        max_attempts=3,
    )
    assert result.ok is True
    assert metadata.num_attempts == 2
    assert len(client.calls) == 2
    # 2차 호출의 user_prompt는 원 요청 + 오류 요지가 덧붙어야 한다
    first_prompt, second_prompt = client.calls[0][1], client.calls[1][1]
    assert first_prompt == "원 요청"
    assert second_prompt.startswith("원 요청")
    assert "이전 응답의 문제" in second_prompt
    assert "코드펜스" in second_prompt


def test_complete_validated_retries_after_validator_failure_then_succeeds() -> None:
    client = FakeLlmClient(['{"ok": "not-a-bool"}', '{"ok": true}'])

    def strict_validator(payload: object) -> _OkPayload:
        assert isinstance(payload, dict)
        if not isinstance(payload.get("ok"), bool):
            raise ValueError("ok는 bool이어야 합니다")
        return _OkPayload.model_validate(payload)

    result, metadata = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="원 요청",
        validator=strict_validator,
        max_attempts=3,
    )
    assert result.ok is True
    assert metadata.num_attempts == 2


def test_complete_validated_exhausts_attempts_and_raises() -> None:
    client = FakeLlmClient(["아니요", "여전히 아니요", "그래도 아니요"])
    with pytest.raises(DataValidationError, match="3회 시도"):
        complete_validated(
            client,
            system_prompt="sys",
            user_prompt="원 요청",
            validator=_validate_ok,
            max_attempts=3,
        )
    # max_attempts를 정확히 소진하고 그 이상은 호출하지 않는다
    assert len(client.calls) == 3


def test_complete_validated_stops_exactly_at_max_attempts_even_with_more_responses_left() -> None:
    client = FakeLlmClient(["나쁨", "나쁨", "나쁨", '{"ok": true}'])
    with pytest.raises(DataValidationError):
        complete_validated(
            client,
            system_prompt="sys",
            user_prompt="원 요청",
            validator=_validate_ok,
            max_attempts=3,
        )
    assert len(client.calls) == 3


def test_complete_validated_rejects_non_positive_max_attempts() -> None:
    client = FakeLlmClient(['{"ok": true}'])
    with pytest.raises(ValueError, match="1 이상"):
        complete_validated(
            client,
            system_prompt="sys",
            user_prompt="원 요청",
            validator=_validate_ok,
            max_attempts=0,
        )


# --- complete_validated: 메타데이터 누적 -------------------------------------------


class _ScriptedClient:
    """LlmTextClient 프로토콜을 구조적으로 구현하는 자체 스크립트 더블.

    FakeLlmClient는 명세 §2.5에 따라 고정 메타데이터(model="fake", 토큰
    None)만 반환하므로, 시도별로 다른 메타데이터(토큰 수 등)를 재생해
    누적 로직 자체를 검증하려면 이 더블이 필요하다.
    """

    def __init__(self, scripted: Sequence[tuple[str, LlmCallMetadata]]) -> None:
        self._scripted = list(scripted)
        self._next_index = 0

    def complete_text(self, *, system_prompt: str, user_prompt: str) -> tuple[str, LlmCallMetadata]:
        text, metadata = self._scripted[self._next_index]
        self._next_index += 1
        return text, metadata


def test_complete_validated_accumulates_duration_and_tokens_across_attempts() -> None:
    client = _ScriptedClient(
        [
            (
                "나쁨",
                LlmCallMetadata(
                    model="claude-haiku-4-5-20251001",
                    num_attempts=1,
                    duration_ms=100,
                    input_tokens=10,
                    output_tokens=5,
                    cost_usd_notional=0.001,
                ),
            ),
            (
                '{"ok": true}',
                LlmCallMetadata(
                    model="claude-haiku-4-5-20251001",
                    num_attempts=1,
                    duration_ms=150,
                    input_tokens=20,
                    output_tokens=8,
                    cost_usd_notional=0.002,
                ),
            ),
        ]
    )
    result, metadata = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="원 요청",
        validator=_validate_ok,
        max_attempts=3,
    )
    assert result.ok is True
    assert metadata.num_attempts == 2
    assert metadata.duration_ms == 250
    assert metadata.input_tokens == 30
    assert metadata.output_tokens == 13
    assert metadata.cost_usd_notional is not None
    assert metadata.cost_usd_notional == pytest.approx(0.003)
    assert metadata.model == "claude-haiku-4-5-20251001"


def test_complete_validated_keeps_none_tokens_when_never_observed() -> None:
    client = _ScriptedClient(
        [
            (
                '{"ok": true}',
                LlmCallMetadata(
                    model="fake",
                    num_attempts=1,
                    duration_ms=0,
                    input_tokens=None,
                    output_tokens=None,
                    cost_usd_notional=None,
                ),
            )
        ]
    )
    _, metadata = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="원 요청",
        validator=_validate_ok,
        max_attempts=1,
    )
    assert metadata.input_tokens is None
    assert metadata.output_tokens is None
    assert metadata.cost_usd_notional is None


def test_complete_validated_uses_json_loads_semantics_via_extract_json() -> None:
    """extract_json이 뽑아낸 문자열이 실제로 json.loads 가능한지까지 통합 검증."""
    client = FakeLlmClient(['설명입니다: ```json\n{"ok": true}\n```'])
    result, _ = complete_validated(
        client,
        system_prompt="sys",
        user_prompt="원 요청",
        validator=_validate_ok,
        max_attempts=1,
    )
    assert result.ok is True
    # extract_json이 순수 함수임을 재확인 (json.loads 입력으로도 안정적)
    assert json.loads(extract_json('{"ok": true}')) == {"ok": True}
