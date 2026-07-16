"""resolve-company CLI 단위 테스트 — DART 계층 전부 mock (명세 A1 §4, §6)."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from research_backtest.app import cli
from research_backtest.core.config import DartConfig, Settings
from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.corp_code import CorpCodeRegistry
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.models import DartCorporation

runner = CliRunner()

SK_HYNIX = DartCorporation(
    corp_code="00164779",
    corp_name="SK하이닉스",
    corp_eng_name="SK hynix Inc.",
    stock_code="000660",
    modify_date="20250102",
)
TWIN_A = DartCorporation(corp_code="00990002", corp_name="쌍둥이상사", modify_date="20230811")
TWIN_B = DartCorporation(corp_code="00990003", corp_name="쌍둥이상사", modify_date="20220301")


def _filing(
    report_nm: str, rcept_no: str, rcept_dt: date, report_type: PeriodicReportType
) -> DartFiling:
    return DartFiling(
        corp_code="00164779",
        corp_name="SK하이닉스",
        stock_code="000660",
        report_nm=report_nm,
        rcept_no=rcept_no,
        flr_nm="SK하이닉스",
        rcept_dt=rcept_dt,
        rm=None,
        report_type=report_type,
    )


ANNUAL_FILING = _filing(
    "사업보고서 (2024.12)", "20250320000200", date(2025, 3, 20), PeriodicReportType.ANNUAL
)
Q1_FILING = _filing(
    "분기보고서 (2025.03)", "20250515000100", date(2025, 5, 15), PeriodicReportType.Q1
)


def _make_settings(tmp_path: Path, api_key: str) -> Settings:
    return Settings(_env_file=None, dart_api_key=api_key, data_dir=tmp_path / "data")


@pytest.fixture
def patched_dart_layers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """설정·고유번호·공시검색 계층을 전부 오프라인 mock으로 대체한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path, api_key="unit-test-key")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_dart_config", lambda: DartConfig())

    registry = CorpCodeRegistry([SK_HYNIX, TWIN_A, TWIN_B])

    def fake_load_registry(
        client: Any,
        cache_dir: Path,
        *,
        refresh_days: int,
        force: bool = False,
        now: datetime | None = None,
    ) -> CorpCodeRegistry:
        return registry

    monkeypatch.setattr(cli, "load_corp_code_registry", fake_load_registry)

    def fake_find_filings(
        client: Any, corp_code: str, *, as_of_date: date, lookback_years: int = 5
    ) -> list[DartFiling]:
        assert corp_code == "00164779"
        assert lookback_years == 2  # 명세 §4 — 최근 2년 검색
        return [Q1_FILING, ANNUAL_FILING]

    monkeypatch.setattr(cli, "find_periodic_filings", fake_find_filings)


def test_resolve_company_success_exits_0(patched_dart_layers: None) -> None:
    result = runner.invoke(cli.app, ["resolve-company", "--company", "SK하이닉스"])
    assert result.exit_code == 0, result.output
    assert "00164779" in result.output
    assert "000660" in result.output
    assert "20250320000200" in result.output  # 최근 사업보고서
    assert "20250515000100" in result.output  # 최근 분기·반기보고서


def test_resolve_company_ambiguous_exits_1_with_candidates(patched_dart_layers: None) -> None:
    result = runner.invoke(cli.app, ["resolve-company", "--company", "쌍둥이상사"])
    assert result.exit_code == 1
    assert "AMBIGUOUS" in result.output
    assert "00990002" in result.output
    assert "00990003" in result.output
    assert "종목코드" in result.output  # 재시도 안내


def test_resolve_company_not_found_exits_1(patched_dart_layers: None) -> None:
    result = runner.invoke(cli.app, ["resolve-company", "--company", "없는회사이름졸라이상한"])
    assert result.exit_code == 1
    assert "NOT_FOUND" in result.output


def test_resolve_company_missing_key_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path, api_key="")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = runner.invoke(cli.app, ["resolve-company", "--company", "SK하이닉스"])
    assert result.exit_code == 3
    assert "DART_API_KEY" in result.output


def test_resolve_company_rejects_bad_as_of_date(patched_dart_layers: None) -> None:
    result = runner.invoke(
        cli.app, ["resolve-company", "--company", "SK하이닉스", "--as-of-date", "2025/01/01"]
    )
    assert result.exit_code == 2  # typer usage error
