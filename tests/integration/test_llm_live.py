"""LLM 실호출 integration 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.6) — 인증 없으면 skip.

실행: 레포 루트에서 메인 레포 .env를 주입하고 ``pytest -m integration``
(또는 이 파일만 ``pytest tests/integration/test_llm_live.py``). 구독 계정의
rate limit을 고려해 실 호출은 1회로 최소화한다(명세 §2.6).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from research_backtest.core.config import get_settings
from research_backtest.core.llm.client import LlmTextClient, create_llm_client
from research_backtest.core.llm.config import LlmConfig, load_llm_config
from research_backtest.core.llm.json_call import complete_validated

pytestmark = pytest.mark.integration


class _OkResponse(BaseModel):
    """실호출 검증용 최소 스키마 — ``{"ok": true}`` 강제(명세 §2.6)."""

    ok: bool


@pytest.fixture(scope="module")
def llm_config() -> LlmConfig:
    return load_llm_config()


@pytest.fixture(scope="module")
def llm_client(llm_config: LlmConfig) -> LlmTextClient:
    settings = get_settings()
    if not settings.anthropic_api_key and not settings.claude_code_oauth_token:
        pytest.skip(
            "LLM 인증(CLAUDE_CODE_OAUTH_TOKEN 또는 ANTHROPIC_API_KEY) 미설정 — "
            "integration 테스트 생략(명세 §2.6)"
        )
    return create_llm_client(llm_config, settings)


def test_haiku_complete_validated_live_call(
    llm_client: LlmTextClient, llm_config: LlmConfig
) -> None:
    """Haiku 실호출 1회 — JSON 강제·재시도 루프까지 end-to-end로 검증한다."""
    result, metadata = complete_validated(
        llm_client,
        system_prompt=(
            "You are a JSON-only API. Respond with a single valid JSON object and "
            "nothing else — no prose, no markdown code fences."
        ),
        user_prompt='Return exactly this JSON object: {"ok": true}',
        validator=_OkResponse.model_validate,
        max_attempts=llm_config.max_attempts,
    )

    assert result.ok is True
    assert metadata.model.startswith(llm_config.model)
    assert metadata.num_attempts >= 1
