"""CLI 골격(README §26, 명세 CLI-integration §7) 스모크 테스트."""

import pytest
from typer.testing import CliRunner

from research_backtest import __version__
from research_backtest.app import cli
from research_backtest.app.cli import app
from research_backtest.core.config import Settings

runner = CliRunner()

ALL_COMMANDS = [
    "resolve-company",
    "collect-financials",
    "collect-market",
    "build-financials",
    "parse-xbrl",
    "reconcile-financials",
    "research",
    "backtest",
    # HITL 워크플로 (1804 §14 + 명세 CLI-integration §5)
    "create-run",
    "runs",
    "status",
    "create-analyst-view",
    "create-hypothesis",
    "approve-strategy",
    "submit-interpretation",
    "generate-candidates",
    "generate-strategy-draft",
    "generate-report",
]


def test_help_lists_all_spec_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ALL_COMMANDS:
        assert cmd in result.output


def test_version_command() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_research_is_implemented_and_hits_config_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """research는 더 이상 스텁이 아니다 (C1' 실구현) — DART 키가 없으면 설정 오류(exit 3)."""
    keyless = Settings(_env_file=None, dart_api_key="")
    monkeypatch.setattr(cli, "get_settings", lambda: keyless)
    result = runner.invoke(
        app,
        ["research", "--company", "SK하이닉스", "--as-of-date", "2025-12-31"],
    )
    # 스텁이면 exit 2였다 — 실구현은 run 생성 진입에서 DART 키를 요구해 exit 3.
    assert result.exit_code == 3
