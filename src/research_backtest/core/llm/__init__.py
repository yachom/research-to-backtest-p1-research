"""LLM 공용 계층 (명세 docs/specs/W3a-llm-evidence.md §2, MILESTONES D2 재개정).

Claude Agent SDK(구독 OAuth 토큰 인증) 기반 텍스트 완성 클라이언트와, JSON을
강제하는 재시도 루프, 버전 관리되는 프롬프트 로더를 제공한다. Wave 3b(C1'
후보 생성기 ∥ C2' 전략 초안)가 이 계층을 소비한다. LLM은 텍스트 정리만
담당하며(docs/AI_ROLE_BOUNDARY.md) Python이 계산한 값을 재계산하지 않는다
(README §18).

- :mod:`.config` — configs/llm.yaml 로더(:class:`LlmConfig`)
- :mod:`.client` — :class:`ClaudeAgentSdkClient`·:func:`create_llm_client`(팩토리)
- :mod:`.json_call` — JSON 추출·파싱·검증 재시도 루프
- :mod:`.prompts` — ``{name}_v{version}.txt`` 프롬프트 로더
- :mod:`.testing` — :class:`FakeLlmClient`(공식 테스트 더블)
"""

from research_backtest.core.llm.client import (
    ClaudeAgentSdkClient,
    LlmCallMetadata,
    LlmTextClient,
    create_llm_client,
)
from research_backtest.core.llm.config import LlmConfig, LlmProvider, load_llm_config
from research_backtest.core.llm.json_call import complete_validated, extract_json
from research_backtest.core.llm.prompts import PromptTemplate, load_prompt
from research_backtest.core.llm.testing import FakeLlmClient

__all__ = [
    "ClaudeAgentSdkClient",
    "FakeLlmClient",
    "LlmCallMetadata",
    "LlmConfig",
    "LlmProvider",
    "LlmTextClient",
    "PromptTemplate",
    "complete_validated",
    "create_llm_client",
    "extract_json",
    "load_llm_config",
    "load_prompt",
]
