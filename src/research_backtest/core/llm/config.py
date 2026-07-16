"""LLM 호출 설정 — configs/llm.yaml 로더 (명세 docs/specs/W3a-llm-evidence.md §2.1).

``configs/llm.yaml``을 :class:`LlmConfig`로 검증한다. 파일 부재·형식 오류·값
오류를 :class:`ConfigError`로 통일하는 것은 ``core.config.load_dart_config``/
``load_market_config``, ``quant.backtest.costs.load_backtest_config``와 동일한
로더 패턴이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from research_backtest.core.exceptions import ConfigError

DEFAULT_LLM_CONFIG_PATH = Path("configs/llm.yaml")

#: Settings.llm_provider(core/config.py)와 동일한 값 공간을 공유한다.
LlmProvider = Literal["claude_agent_sdk", "openrouter"]


class LlmConfig(BaseModel):
    """LLM 호출 파라미터 (configs/llm.yaml, 명세 §2.1).

    - ``provider``: openrouter는 **예약만**(D2 — 키 미확보) — 클라이언트
      factory가 사용 시 "폴백 미구현" ConfigError를 던진다(core/llm/client.py).
    - ``model``: 전체 모델 ID로 고정한다(재현성) — Settings.claude_model처럼
      별칭(sonnet/opus/haiku)을 쓰지 않는다.
    - ``max_turns``: ``ClaudeAgentOptions.max_turns``에 그대로 전달한다.
    - ``max_attempts``: JSON 파싱·검증 실패 재시도 상한(core/llm/json_call.py).
      API 호출 자체의 오류(인증·요금·전송)는 이 재시도 대상이 아니다.
    - ``timeout_seconds``: 호출 타임아웃(초) — ``ClaudeAgentSdkClient``가
      ``asyncio.wait_for``로 전체 호출(스트림 소비 포함)에 직접 강제한다.
    """

    model_config = ConfigDict(extra="forbid")

    provider: LlmProvider = "claude_agent_sdk"
    model: str = "claude-haiku-4-5-20251001"
    max_turns: int = Field(default=1, ge=1)
    max_attempts: int = Field(default=3, ge=1)
    timeout_seconds: float = Field(default=360.0, gt=0)  # 출력 긴 호출 실측 반영 (2026-07-15)


def load_llm_config(path: Path = DEFAULT_LLM_CONFIG_PATH) -> LlmConfig:
    """``configs/llm.yaml``을 읽어 :class:`LlmConfig`로 검증한다.

    파일이 flat mapping이므로(중첩 섹션 없음) ``dart.yaml``/``market.yaml``
    로더와 달리 섹션 추출 없이 그대로 ``model_validate``에 넘긴다. 파일
    부재·형식 오류·값 오류는 모두 :class:`ConfigError`로 통일한다.
    """
    if not path.exists():
        raise ConfigError(f"LLM 설정 파일이 없습니다: {path} (레포 루트에서 실행했는지 확인)")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"LLM 설정 파일 형식이 잘못되었습니다(매핑이 아님): {path}")
    try:
        return LlmConfig.model_validate(raw)
    except ValidationError as err:
        raise ConfigError(f"LLM 설정 값이 잘못되었습니다: {err}") from err
