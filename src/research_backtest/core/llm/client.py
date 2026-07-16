"""Claude Agent SDK 기반 LLM 클라이언트 (명세 docs/specs/W3a-llm-evidence.md §2.2).

Claude Agent SDK(``claude_agent_sdk.query``)를 프로젝트의 동기 코드베이스에
맞게 감싼다. 인증은 Settings의 두 값(``anthropic_api_key``·
``claude_code_oauth_token``) 중 정확히 하나만 허용한다 — 둘 다 있으면 API
키가 우선 적용되어 의도치 않은 과금이 발생하므로(MILESTONES D2) 즉시
:class:`ConfigError`로 막는다.

**스모크 실측(2026-07-15, 명세 §2.2) 반영**:

1. ``AssistantMessage``가 여러 개 오며 첫 개는 빈 텍스트일 수 있다 — 전체
   Assistant 텍스트를 이어붙이되 ``ResultMessage.result``가 있으면 그것을
   우선한다.
2. 모델은 간단한 요청("1+1")에도 마크다운 코드펜스(```json ... ```)로 감싼
   응답을 낸다 — 이 모듈은 원문을 그대로 반환하고, 코드펜스 제거는
   :mod:`research_backtest.core.llm.json_call`\\ 의 ``extract_json``이
   담당한다.
3. ``ResultMessage``는 ``is_error``·``num_turns``·``duration_ms``·``usage``
   (input_tokens/output_tokens)·``total_cost_usd``를 제공한다 — 전부
   :class:`LlmCallMetadata`에 채운다. 모델 ID는 ``ResultMessage``에는 없고
   각 ``AssistantMessage.model``에 있다.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Protocol

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)

from research_backtest.core.config import Settings
from research_backtest.core.exceptions import ConfigError, DataValidationError
from research_backtest.core.llm.config import LlmConfig

#: is_error 응답 본문을 예외 메시지에 담을 때의 최대 길이 (명세 §2.2 — 토큰·env
#: 값이 섞여 나올 가능성을 차단하기 위한 절단).
_ERROR_TEXT_MAX_CHARS = 500


@dataclass(frozen=True)
class LlmCallMetadata:
    """LLM 호출 1회(또는 재시도 누적)의 메타데이터 (명세 §2.2).

    :func:`research_backtest.core.llm.json_call.complete_validated`\\ 가 여러
    번의 물리적 호출을 거치면 이 필드들을 누적한다 — ``num_attempts``=총
    시도 수, ``duration_ms``=합, 토큰·비용은 관측 가능한 시도분만 합산한다
    (§2.3). :class:`ClaudeAgentSdkClient`\\ 가 직접 반환하는 값은 항상
    ``num_attempts=1``(호출 1회=시도 1회)이다.
    """

    model: str
    num_attempts: int
    duration_ms: int
    input_tokens: int | None
    output_tokens: int | None
    cost_usd_notional: float | None


class LlmTextClient(Protocol):
    """텍스트 완성 LLM 클라이언트 인터페이스 (명세 §2.2).

    :class:`ClaudeAgentSdkClient`(실제 호출)와
    :class:`research_backtest.core.llm.testing.FakeLlmClient`(테스트 더블)가
    공유하는 계약이다.
    """

    def complete_text(self, *, system_prompt: str, user_prompt: str) -> tuple[str, LlmCallMetadata]:
        """1회 LLM 호출 — (응답 텍스트, 메타데이터)를 반환한다."""
        ...


def _inject_auth_env(settings: Settings) -> None:
    """Settings의 LLM 인증값을 os.environ에 주입한다 (명세 §2.2).

    ``core.market.source.PykrxSource``와 동일한 패턴: ``anthropic_api_key``·
    ``claude_code_oauth_token`` 중 정확히 하나만 허용하고, 이미 os.environ에
    설정된 값은 보존한다(덮어쓰지 않음). 값 자체는 절대 로그·예외 메시지에
    담지 않는다(CLAUDE.md §3-4).
    """
    has_api_key = bool(settings.anthropic_api_key)
    has_oauth_token = bool(settings.claude_code_oauth_token)

    if has_api_key and has_oauth_token:
        raise ConfigError(
            "ANTHROPIC_API_KEY와 CLAUDE_CODE_OAUTH_TOKEN을 동시에 설정할 수 없습니다 — "
            "API 키가 우선 적용되어 의도치 않은 과금이 발생합니다(MILESTONES D2 재개정). "
            "둘 중 하나만 .env에 남기세요."
        )
    if not has_api_key and not has_oauth_token:
        raise ConfigError(
            "LLM 인증 정보가 없습니다 — .env에 CLAUDE_CODE_OAUTH_TOKEN(구독 계정, "
            "`claude setup-token`으로 발급) 또는 ANTHROPIC_API_KEY 중 하나를 설정하세요."
        )

    if has_api_key and not os.environ.get("ANTHROPIC_API_KEY"):
        os.environ["ANTHROPIC_API_KEY"] = settings.anthropic_api_key
    if has_oauth_token and not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = settings.claude_code_oauth_token


class ClaudeAgentSdkClient:
    """Claude Agent SDK 기반 :class:`LlmTextClient` 구현 (명세 §2.2).

    도구 사용은 항상 금지한다(``allowed_tools=[]``) — LLM은 텍스트 정리만
    담당하며 코드를 읽거나 실행하지 않는다(docs/AI_ROLE_BOUNDARY.md).
    ``system_prompt``는 호출마다 ``complete_text`` 인자로 받아
    ``ClaudeAgentOptions.system_prompt``에 그대로 전달한다(문자열을 그대로
    쓰면 Claude Code CLI 기본 시스템 프롬프트를 대체한다).

    provider 분기(openrouter 폴백 미구현)는 이 클래스가 아니라
    :func:`create_llm_client` 팩토리가 담당한다 — 이 클래스는 항상
    claude_agent_sdk 프로토콜로 호출한다고 가정한다.
    """

    def __init__(self, config: LlmConfig, settings: Settings) -> None:
        _inject_auth_env(settings)
        self._config = config

    def complete_text(self, *, system_prompt: str, user_prompt: str) -> tuple[str, LlmCallMetadata]:
        """1회 LLM 호출 — 내부적으로 asyncio.run()으로 감싼 동기 메서드(명세 §2.2).

        ``config.timeout_seconds``로 전체 호출(스트림 소비 포함)을 강제
        타임아웃한다 — 초과 시 :class:`DataValidationError`(SDK 서브프로세스가
        멎어도 호출자가 무한정 블록되지 않게 한다).
        """
        return asyncio.run(self._complete_text_with_timeout(system_prompt, user_prompt))

    async def _complete_text_with_timeout(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, LlmCallMetadata]:
        try:
            return await asyncio.wait_for(
                self._complete_text_async(system_prompt=system_prompt, user_prompt=user_prompt),
                timeout=self._config.timeout_seconds,
            )
        except TimeoutError as err:
            raise DataValidationError(
                f"LLM 호출이 timeout_seconds={self._config.timeout_seconds}초 안에 "
                "끝나지 않았습니다."
            ) from err

    async def _complete_text_async(
        self, *, system_prompt: str, user_prompt: str
    ) -> tuple[str, LlmCallMetadata]:
        options = ClaudeAgentOptions(
            model=self._config.model,
            max_turns=self._config.max_turns,
            allowed_tools=[],
            system_prompt=system_prompt,
        )

        assistant_text_parts: list[str] = []
        observed_model = self._config.model
        result_message: ResultMessage | None = None

        async for message in query(prompt=user_prompt, options=options):
            if isinstance(message, AssistantMessage):
                if message.model:
                    observed_model = message.model
                for block in message.content:
                    if isinstance(block, TextBlock) and block.text:
                        assistant_text_parts.append(block.text)
            elif isinstance(message, ResultMessage):
                result_message = message

        if result_message is None:
            raise DataValidationError(
                "LLM 호출이 ResultMessage 없이 종료되었습니다 (SDK 응답 스트림 이상)."
            )

        text = result_message.result if result_message.result else "".join(assistant_text_parts)

        if result_message.is_error:
            raise DataValidationError(
                f"LLM 호출이 오류를 반환했습니다(stop_reason={result_message.stop_reason!r}): "
                f"{_truncate_error_text(text)}"
            )

        usage: dict[str, Any] = result_message.usage or {}
        metadata = LlmCallMetadata(
            model=observed_model,
            num_attempts=1,
            duration_ms=result_message.duration_ms,
            input_tokens=_usage_int(usage.get("input_tokens")),
            output_tokens=_usage_int(usage.get("output_tokens")),
            cost_usd_notional=result_message.total_cost_usd,
        )
        return text, metadata


def create_llm_client(config: LlmConfig, settings: Settings) -> LlmTextClient:
    """``config.provider``에 따라 :class:`LlmTextClient` 구현체를 생성한다 (명세 §2.1).

    openrouter는 아직 구현하지 않는다(D2 — 키 미확보) — 선택 시 "폴백
    미구현" ConfigError를 던진다.
    """
    if config.provider == "claude_agent_sdk":
        return ClaudeAgentSdkClient(config, settings)
    raise ConfigError(
        f"LLM provider={config.provider!r} 폴백은 아직 미구현입니다 "
        "(MILESTONES D2 — OpenRouter 키 확보 시 추가 예정). "
        "configs/llm.yaml의 provider를 claude_agent_sdk로 설정하세요."
    )


def _truncate_error_text(text: str) -> str:
    """오류 메시지에 담을 응답 본문을 절단한다 (명세 §2.2 — 토큰·env 값 노출 방지)."""
    if len(text) <= _ERROR_TEXT_MAX_CHARS:
        return text
    return text[:_ERROR_TEXT_MAX_CHARS] + "…(절단)"


def _usage_int(value: object) -> int | None:
    """ResultMessage.usage(dict[str, Any])에서 토큰 수를 안전하게 int로 변환한다."""
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return None
