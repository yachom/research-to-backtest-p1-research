"""core/llm/config.py 단위 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.1).

configs/llm.yaml 로더 — core.config.load_dart_config/load_market_config와
동일한 로더 패턴(파일 부재·형식 오류·값 오류 → ConfigError)을 검증한다.
"""

from pathlib import Path

import pytest

from research_backtest.core.exceptions import ConfigError
from research_backtest.core.llm.config import LlmConfig, load_llm_config

REPO_LLM_CONFIG_PATH = Path(__file__).resolve().parents[3] / "configs" / "llm.yaml"


def test_repo_llm_yaml_loads_with_expected_defaults() -> None:
    """실제 configs/llm.yaml(명세 §2.1)이 예상 값으로 로드되는지 확인한다."""
    config = load_llm_config(REPO_LLM_CONFIG_PATH)
    assert config.provider == "claude_agent_sdk"
    assert config.model == "claude-haiku-4-5-20251001"
    assert config.max_turns == 1
    assert config.max_attempts == 3
    assert config.timeout_seconds == 360.0  # 출력 긴 호출 실측 반영 (2026-07-15)


def test_load_llm_config_missing_file_raises_config_error(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.yaml"
    with pytest.raises(ConfigError, match="LLM 설정 파일이 없습니다"):
        load_llm_config(missing)


def test_load_llm_config_non_mapping_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "llm.yaml"
    path.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="매핑이 아님"):
        load_llm_config(path)


def test_load_llm_config_rejects_unknown_keys(tmp_path: Path) -> None:
    """LlmConfig(extra='forbid') — 오타·미지원 키를 조용히 무시하지 않는다."""
    path = tmp_path / "llm.yaml"
    path.write_text("provider: claude_agent_sdk\nmodle: typo\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="LLM 설정 값이 잘못되었습니다"):
        load_llm_config(path)


def test_load_llm_config_rejects_invalid_max_attempts(tmp_path: Path) -> None:
    path = tmp_path / "llm.yaml"
    path.write_text("max_attempts: 0\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="LLM 설정 값이 잘못되었습니다"):
        load_llm_config(path)


def test_load_llm_config_rejects_invalid_provider(tmp_path: Path) -> None:
    path = tmp_path / "llm.yaml"
    path.write_text("provider: bedrock\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="LLM 설정 값이 잘못되었습니다"):
        load_llm_config(path)


def test_load_llm_config_applies_defaults_for_partial_file(tmp_path: Path) -> None:
    """일부 키만 있는 파일도 나머지는 기본값으로 채워진다."""
    path = tmp_path / "llm.yaml"
    path.write_text("model: claude-haiku-4-5-20251001\n", encoding="utf-8")
    config = load_llm_config(path)
    assert config.model == "claude-haiku-4-5-20251001"
    assert config.provider == "claude_agent_sdk"
    assert config.max_turns == 1
    assert config.max_attempts == 3
    assert config.timeout_seconds == 360.0  # 출력 긴 호출 실측 반영 (2026-07-15)


def test_llm_config_model_forbids_extra_fields_directly() -> None:
    with pytest.raises(Exception, match="extra"):
        LlmConfig.model_validate({"unexpected_field": 1})
