"""core/llm/prompts.py 단위 테스트 (명세 docs/specs/W3a-llm-evidence.md §2.4).

프롬프트 버전 로더(``{name}_v{version}.txt``, docs/HUMAN_IN_THE_LOOP.md §5)와
``render``의 누락/잉여 변수 처리를 tmp_path 픽스처 파일로 검증한다 — 실제
프롬프트 파일(Wave 3b 담당)에는 의존하지 않는다.
"""

from pathlib import Path

import pytest

from research_backtest.core.exceptions import ConfigError, DataValidationError
from research_backtest.core.llm.prompts import PromptTemplate, load_prompt


def test_load_prompt_reads_versioned_file(tmp_path: Path) -> None:
    (tmp_path / "candidate_analysis_v1.txt").write_text(
        "{company}의 {metric} 분석을 수행하라.", encoding="utf-8"
    )
    template = load_prompt(tmp_path, "candidate_analysis", 1)
    assert template.name == "candidate_analysis"
    assert template.version == 1
    assert template.text == "{company}의 {metric} 분석을 수행하라."


def test_load_prompt_missing_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="프롬프트 파일이 없습니다"):
        load_prompt(tmp_path, "missing_prompt", 1)


def test_load_prompt_missing_file_error_includes_path(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="missing_prompt_v1"):
        load_prompt(tmp_path, "missing_prompt", 1)


def test_prompt_version_string_representation() -> None:
    template = PromptTemplate(name="hypothesis_candidate", version=1, text="x")
    assert template.prompt_version == "v1"

    template_v2 = PromptTemplate(name="hypothesis_candidate", version=2, text="x")
    assert template_v2.prompt_version == "v2"


def test_render_substitutes_all_variables() -> None:
    template = PromptTemplate(name="t", version=1, text="{company}의 {metric} 분석을 수행하라.")
    rendered = template.render(company="SK하이닉스", metric="영업이익률")
    assert rendered == "SK하이닉스의 영업이익률 분석을 수행하라."


def test_render_missing_variable_raises_data_validation_error() -> None:
    template = PromptTemplate(name="t", version=1, text="{company}의 {metric} 분석을 수행하라.")
    with pytest.raises(DataValidationError, match="누락"):
        template.render(company="SK하이닉스")


def test_render_missing_variable_error_names_the_missing_key() -> None:
    template = PromptTemplate(name="t", version=1, text="{company}의 {metric} 분석을 수행하라.")
    with pytest.raises(DataValidationError, match="metric"):
        template.render(company="SK하이닉스")


def test_render_surplus_variable_raises_data_validation_error() -> None:
    template = PromptTemplate(name="t", version=1, text="{company} 분석을 수행하라.")
    with pytest.raises(DataValidationError, match="잉여"):
        template.render(company="SK하이닉스", unused_arg="쓰이지 않음")


def test_render_no_variables_needed_ignores_nothing_but_rejects_extras() -> None:
    template = PromptTemplate(name="t", version=1, text="변수가 없는 프롬프트.")
    assert template.render() == "변수가 없는 프롬프트."
    with pytest.raises(DataValidationError, match="잉여"):
        template.render(unused="x")


def test_render_is_deterministic() -> None:
    template = PromptTemplate(name="t", version=1, text="{a}-{b}")
    assert template.render(a="1", b="2") == template.render(a="1", b="2") == "1-2"
