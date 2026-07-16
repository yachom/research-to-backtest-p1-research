"""core/llm/client.py 단위 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.2).

두 부분으로 나뉜다:

1. **인증 규칙** — Settings 조합별 ConfigError·os.environ 주입/보존을
   검증한다. 실제 네트워크 호출은 없다.
2. **메시지 조립 로직** — ``claude_agent_sdk.query``를 monkeypatch해 스모크
   실측 케이스(빈 첫 AssistantMessage, ResultMessage.result 우선, is_error
   절단)를 네트워크 없이 재현한다.

**환경변수 격리에 관한 주의**: ``ClaudeAgentSdkClient.__init__``(정확히는
``_inject_auth_env``)는 명세 §2.2에 따라 **os.environ에 직접** 값을 쓴다.
``monkeypatch.setenv``/``delenv``는 monkeypatch **자신**을 통해 이뤄진
변경만 자동 복구하므로, 프로덕션 코드가 직접 수행한 이 주입은 monkeypatch로
추적되지 않고 프로세스 전역에 남아 다른 테스트 파일(특히
``tests/integration/test_llm_live.py``)로 새어나갈 수 있다(실측: 이 파일의
초기 버전에서 실제로 재현됨). 그래서 autouse 픽스처는 monkeypatch가 아니라
os.environ을 직접 스냅샷·복원한다 — "누가 어떻게 값을 바꿨는지"와 무관하게
테스트 종료 시 정확히 원래 상태로 되돌린다.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import pytest
from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, TextBlock

import research_backtest.core.llm.client as client_module
from research_backtest.core.config import Settings
from research_backtest.core.exceptions import ConfigError, DataValidationError
from research_backtest.core.llm.client import ClaudeAgentSdkClient, create_llm_client
from research_backtest.core.llm.config import LlmConfig

# 테스트 전용 더미 값 — 실제 자격증명이 아니다(명세 §2.7 "토큰 값 비노출" 확인용).
_FAKE_OAUTH_TOKEN = "fake-oauth-token-should-never-leak"
_FAKE_API_KEY = "fake-api-key-should-never-leak"

_AUTH_ENV_KEYS = ("ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN")


@pytest.fixture(autouse=True)
def clean_llm_auth_env() -> Iterator[None]:
    """LLM 인증 env var를 테스트 전후로 스냅샷·강제 복원한다 (모듈 docstring 참고).

    프로덕션 코드가 os.environ을 직접 변경하므로 monkeypatch만으로는 격리가
    보장되지 않는다 — 시작 전 값을 기억해 두고, 테스트가 무엇을 했든 끝나면
    ``finally``에서 그 값으로(또는 원래 없었다면 삭제로) 되돌린다.
    """
    snapshot = {key: os.environ.get(key) for key in _AUTH_ENV_KEYS}
    for key in _AUTH_ENV_KEYS:
        os.environ.pop(key, None)
    try:
        yield
    finally:
        for key, value in snapshot.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path / "data", **overrides)


def _config() -> LlmConfig:
    return LlmConfig()


# --- 인증 규칙 --------------------------------------------------------------------


def test_both_auth_values_set_raises_config_error(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, anthropic_api_key=_FAKE_API_KEY, claude_code_oauth_token=_FAKE_OAUTH_TOKEN
    )
    with pytest.raises(ConfigError, match="동시에 설정할 수 없습니다"):
        ClaudeAgentSdkClient(_config(), settings)
    # 검증이 주입보다 먼저 실행되어 os.environ이 오염되지 않는다
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


def test_no_auth_values_raises_config_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(ConfigError, match="인증 정보가 없습니다"):
        ClaudeAgentSdkClient(_config(), settings)


def test_oauth_token_only_injects_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(tmp_path, claude_code_oauth_token=_FAKE_OAUTH_TOKEN)
    ClaudeAgentSdkClient(_config(), settings)
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == _FAKE_OAUTH_TOKEN
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_api_key_only_injects_env(tmp_path: Path) -> None:
    settings = _settings(tmp_path, anthropic_api_key=_FAKE_API_KEY)
    ClaudeAgentSdkClient(_config(), settings)
    assert os.environ["ANTHROPIC_API_KEY"] == _FAKE_API_KEY
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in os.environ


def test_existing_env_value_is_preserved_not_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "already-set-value")
    settings = _settings(tmp_path, claude_code_oauth_token="different-value-from-settings")
    ClaudeAgentSdkClient(_config(), settings)
    assert os.environ["CLAUDE_CODE_OAUTH_TOKEN"] == "already-set-value"


def test_config_error_messages_never_contain_secret_values(tmp_path: Path) -> None:
    """DoD: 토큰 값이 예외 메시지에 없음을 직접 확인한다."""
    settings_both = _settings(
        tmp_path, anthropic_api_key=_FAKE_API_KEY, claude_code_oauth_token=_FAKE_OAUTH_TOKEN
    )
    with pytest.raises(ConfigError) as excinfo_both:
        ClaudeAgentSdkClient(_config(), settings_both)
    assert _FAKE_API_KEY not in str(excinfo_both.value)
    assert _FAKE_OAUTH_TOKEN not in str(excinfo_both.value)

    settings_none = _settings(tmp_path)
    with pytest.raises(ConfigError) as excinfo_none:
        ClaudeAgentSdkClient(_config(), settings_none)
    assert _FAKE_API_KEY not in str(excinfo_none.value)
    assert _FAKE_OAUTH_TOKEN not in str(excinfo_none.value)


def test_create_llm_client_dispatches_claude_agent_sdk(tmp_path: Path) -> None:
    settings = _settings(tmp_path, claude_code_oauth_token=_FAKE_OAUTH_TOKEN)
    client = create_llm_client(LlmConfig(provider="claude_agent_sdk"), settings)
    assert isinstance(client, ClaudeAgentSdkClient)


def test_create_llm_client_openrouter_raises_not_implemented(tmp_path: Path) -> None:
    settings = _settings(tmp_path)  # 인증 전혀 없어도 openrouter 분기는 먼저 막는다
    with pytest.raises(ConfigError, match="미구현"):
        create_llm_client(LlmConfig(provider="openrouter"), settings)


# --- 메시지 조립 로직 (query() monkeypatch, 네트워크 없음) --------------------------


def _fake_query_factory(messages: list[Any]) -> Any:
    async def _fake_query(
        *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
    ) -> AsyncIterator[Any]:
        for message in messages:
            yield message

    return _fake_query


def _client(tmp_path: Path) -> ClaudeAgentSdkClient:
    settings = _settings(tmp_path, claude_code_oauth_token=_FAKE_OAUTH_TOKEN)
    return ClaudeAgentSdkClient(_config(), settings)


def test_result_message_result_field_takes_priority_over_assistant_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 스모크 실측①: 첫 AssistantMessage는 빈 텍스트일 수 있다
    messages = [
        AssistantMessage(content=[TextBlock(text="")], model="claude-haiku-4-5-20251001"),
        AssistantMessage(
            content=[TextBlock(text='```json\n{"ok": true}\n```')],
            model="claude-haiku-4-5-20251001",
        ),
        ResultMessage(
            subtype="success",
            duration_ms=850,
            duration_api_ms=800,
            is_error=False,
            num_turns=1,
            session_id="sess-1",
            result='{"ok": true}',
            usage={"input_tokens": 120, "output_tokens": 15},
            total_cost_usd=0.0007,
        ),
    ]
    monkeypatch.setattr(client_module, "query", _fake_query_factory(messages))

    text, metadata = _client(tmp_path).complete_text(system_prompt="sys", user_prompt="1+1")

    assert text == '{"ok": true}'
    assert metadata.model == "claude-haiku-4-5-20251001"
    assert metadata.num_attempts == 1
    assert metadata.duration_ms == 850
    assert metadata.input_tokens == 120
    assert metadata.output_tokens == 15
    assert metadata.cost_usd_notional == pytest.approx(0.0007)


def test_falls_back_to_concatenated_assistant_text_when_result_is_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = [
        AssistantMessage(content=[TextBlock(text="")], model="claude-haiku-4-5-20251001"),
        AssistantMessage(content=[TextBlock(text='{"ok"')], model="claude-haiku-4-5-20251001"),
        AssistantMessage(content=[TextBlock(text=": true}")], model="claude-haiku-4-5-20251001"),
        ResultMessage(
            subtype="success",
            duration_ms=500,
            duration_api_ms=480,
            is_error=False,
            num_turns=1,
            session_id="sess-2",
            result=None,
            usage=None,
            total_cost_usd=None,
        ),
    ]
    monkeypatch.setattr(client_module, "query", _fake_query_factory(messages))

    text, metadata = _client(tmp_path).complete_text(system_prompt="sys", user_prompt="1+1")

    assert text == '{"ok": true}'
    assert metadata.input_tokens is None
    assert metadata.output_tokens is None
    assert metadata.cost_usd_notional is None


def test_is_error_raises_data_validation_error_with_truncated_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    long_text = "오류 " * 300  # 500자를 훌쩍 넘김
    messages = [
        AssistantMessage(content=[TextBlock(text=long_text)], model="claude-haiku-4-5-20251001"),
        ResultMessage(
            subtype="error",
            duration_ms=10,
            duration_api_ms=5,
            is_error=True,
            num_turns=1,
            session_id="sess-3",
            stop_reason="rate_limit",
            result=None,
            usage=None,
            total_cost_usd=None,
        ),
    ]
    monkeypatch.setattr(client_module, "query", _fake_query_factory(messages))

    with pytest.raises(DataValidationError) as excinfo:
        _client(tmp_path).complete_text(system_prompt="sys", user_prompt="u")

    message = str(excinfo.value)
    assert "rate_limit" in message
    # 원문 300*2=600자 전체가 그대로 노출되지는 않는다(절단)
    assert long_text not in message


def test_missing_result_message_raises_data_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = [
        AssistantMessage(
            content=[TextBlock(text="끝나지 않은 스트림")], model="claude-haiku-4-5-20251001"
        )
    ]
    monkeypatch.setattr(client_module, "query", _fake_query_factory(messages))

    with pytest.raises(DataValidationError, match="ResultMessage"):
        _client(tmp_path).complete_text(system_prompt="sys", user_prompt="u")


def test_observed_model_defaults_to_config_model_when_no_assistant_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    messages = [
        ResultMessage(
            subtype="success",
            duration_ms=5,
            duration_api_ms=4,
            is_error=False,
            num_turns=0,
            session_id="sess-4",
            result="{}",
            usage=None,
            total_cost_usd=None,
        ),
    ]
    monkeypatch.setattr(client_module, "query", _fake_query_factory(messages))

    _, metadata = _client(tmp_path).complete_text(system_prompt="sys", user_prompt="u")
    assert metadata.model == _config().model


def test_call_exceeding_timeout_seconds_raises_data_validation_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """config.timeout_seconds가 asyncio.wait_for로 실제 강제되는지 검증한다."""

    async def _slow_query(
        *, prompt: str, options: ClaudeAgentOptions | None = None, transport: Any = None
    ) -> AsyncIterator[Any]:
        await asyncio.sleep(0.2)
        yield ResultMessage(
            subtype="success",
            duration_ms=200,
            duration_api_ms=190,
            is_error=False,
            num_turns=1,
            session_id="sess-slow",
            result="{}",
            usage=None,
            total_cost_usd=None,
        )

    monkeypatch.setattr(client_module, "query", _slow_query)

    settings = _settings(tmp_path, claude_code_oauth_token=_FAKE_OAUTH_TOKEN)
    fast_timeout_config = LlmConfig(timeout_seconds=0.01)
    client = ClaudeAgentSdkClient(fast_timeout_config, settings)

    with pytest.raises(DataValidationError, match="timeout_seconds"):
        client.complete_text(system_prompt="sys", user_prompt="u")
