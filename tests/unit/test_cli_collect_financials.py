"""collect-financials CLI 단위 테스트 — DART 계층 전부 mock (명세 A2 §5~6)."""

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from research_backtest.app import cli
from research_backtest.core.config import DartConfig, Settings
from research_backtest.core.constants import FsDiv, PeriodicReportType, ReprtCode
from research_backtest.core.dart.corp_code import CorpCodeRegistry
from research_backtest.core.dart.financial_api import CollectionSummary, RequestOutcome
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dart.xbrl_downloader import XbrlDownloadOutcome
from research_backtest.core.models import DartCorporation

runner = CliRunner()

SK_HYNIX = DartCorporation(
    corp_code="00164779",
    corp_name="SK하이닉스",
    corp_eng_name="SK hynix Inc.",
    stock_code="000660",
    modify_date="20250102",
)

FAKE_SUMMARY_OUTCOMES = [
    RequestOutcome(
        bsns_year="2024",
        reprt_code=ReprtCode.ANNUAL,
        fs_div=FsDiv.CFS,
        result="FETCHED",
        row_count=312,
        sj_div_counts={"BS": 120, "IS": 40, "CIS": 12, "CF": 90, "SCE": 50},
        rcept_nos=["20250320001234"],
    ),
    RequestOutcome(
        bsns_year="2025",
        reprt_code=ReprtCode.ANNUAL,
        fs_div=FsDiv.OFS,
        result="NO_DATA",
        row_count=0,
        sj_div_counts={},
        rcept_nos=[],
    ),
]


def _make_settings(tmp_path: Path, api_key: str) -> Settings:
    return Settings(_env_file=None, dart_api_key=api_key, data_dir=tmp_path / "data")


@pytest.fixture
def collect_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def patched_layers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, collect_calls: list[dict[str, Any]]
) -> Settings:
    """설정·고유번호·수집기 계층을 전부 오프라인 mock으로 대체한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path, api_key="unit-test-key")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "load_dart_config", lambda: DartConfig())

    registry = CorpCodeRegistry([SK_HYNIX])

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

    def fake_collect(
        client: Any,
        corp_code: str,
        *,
        from_year: int,
        to_year: int,
        fs_divs: tuple[FsDiv, ...] = (FsDiv.CFS, FsDiv.OFS),
        out_dir: Path,
        force: bool = False,
        min_interval_seconds: float = 0.1,
    ) -> CollectionSummary:
        collect_calls.append(
            {
                "corp_code": corp_code,
                "from_year": from_year,
                "to_year": to_year,
                "fs_divs": tuple(fs_divs),
                "out_dir": out_dir,
                "force": force,
                "min_interval_seconds": min_interval_seconds,
            }
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "financial_api_raw.jsonl").write_text(
            '{"n":1}\n{"n":2}\n{"n":3}\n', encoding="utf-8"
        )
        return CollectionSummary(
            corp_code=corp_code,
            fetched_at="2026-07-14T10:00:00+09:00",
            outcomes=FAKE_SUMMARY_OUTCOMES,
        )

    monkeypatch.setattr(cli, "run_collect_financials", fake_collect)
    return settings


BASE_ARGS = ["collect-financials", "--company", "SK하이닉스", "--from-year", "2021"]


def test_collect_happy_path_exits_0_with_table(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2025"])
    assert result.exit_code == 0, result.output
    assert "FETCHED" in result.output
    assert "NO_DATA" in result.output
    assert "financial_api_raw.jsonl" in result.output
    assert "3라인" in result.output

    assert len(collect_calls) == 1
    call = collect_calls[0]
    assert call["corp_code"] == "00164779"
    assert call["from_year"] == 2021
    assert call["to_year"] == 2025
    assert call["fs_divs"] == (FsDiv.CFS, FsDiv.OFS)  # 기본은 둘 다
    assert call["force"] is False
    assert call["min_interval_seconds"] == DartConfig().min_interval_seconds
    expected_out = patched_layers.data_dir / "raw" / "dart" / "financials" / "00164779"
    assert call["out_dir"] == expected_out


def test_collect_scopes_single_and_force(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(
        cli.app, [*BASE_ARGS, "--to-year", "2025", "--scopes", "OFS", "--force-download"]
    )
    assert result.exit_code == 0, result.output
    assert collect_calls[0]["fs_divs"] == (FsDiv.OFS,)
    assert collect_calls[0]["force"] is True


def test_collect_rejects_invalid_scope(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2025", "--scopes", "XFS"])
    assert result.exit_code == 2  # typer.BadParameter → usage error
    assert collect_calls == []


def _make_filing(rcept_no: str, rcept_dt: date, report_nm: str) -> DartFiling:
    return DartFiling(
        corp_code="00164779",
        corp_name="SK하이닉스",
        stock_code="000660",
        report_nm=report_nm,
        rcept_no=rcept_no,
        flr_nm="SK하이닉스",
        rcept_dt=rcept_dt,
        rm=None,
        report_type=PeriodicReportType.ANNUAL,
    )


def test_collect_include_xbrl_downloads_and_filters(
    patched_layers: Settings,
    collect_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--include-xbrl은 B1 다운로더를 실연결하고 [from_year, to_year+1]로 필터한다 (명세 §4.5)."""
    filings = [
        _make_filing("20260315000001", date(2026, 3, 15), "사업보고서 (2025.12)"),  # in-range
        _make_filing("20250320000002", date(2025, 3, 20), "사업보고서 (2024.12)"),  # in-range
        _make_filing("20190315000003", date(2019, 3, 15), "사업보고서 (2018.12)"),  # 범위 밖
    ]
    monkeypatch.setattr(cli, "find_periodic_filings", lambda *a, **k: filings)

    download_calls: list[list[DartFiling]] = []

    def fake_download(
        client: Any, selected: list[DartFiling], **kwargs: Any
    ) -> list[XbrlDownloadOutcome]:
        download_calls.append(selected)
        return [
            XbrlDownloadOutcome(
                rcept_no=f.rcept_no,
                reprt_code="11011",
                report_name=f.report_nm,
                result="FETCHED",
                sha256="abc123",
            )
            for f in selected
        ]

    monkeypatch.setattr(cli, "download_xbrl_filings", fake_download)

    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2025", "--include-xbrl"])
    assert result.exit_code == 0, result.output
    assert len(collect_calls) == 1
    # from_year=2021, to_year=2025 → [2021, 2026] 범위 → 2026·2025만, 2019 제외
    assert len(download_calls) == 1
    selected_years = sorted(f.rcept_dt.year for f in download_calls[0])
    assert selected_years == [2025, 2026]
    assert "XBRL 원본 수집 결과" in result.output
    assert "FETCHED" in result.output


def test_collect_include_xbrl_failed_exits_1(
    patched_layers: Settings,
    collect_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """XBRL 수집에 FAILED가 하나라도 있으면 exit 1이다 (명세 §4.5)."""
    filings = [_make_filing("20250320000002", date(2025, 3, 20), "사업보고서 (2024.12)")]
    monkeypatch.setattr(cli, "find_periodic_filings", lambda *a, **k: filings)
    monkeypatch.setattr(
        cli,
        "download_xbrl_filings",
        lambda *a, **k: [
            XbrlDownloadOutcome(
                rcept_no="20250320000002",
                reprt_code="11011",
                report_name="사업보고서 (2024.12)",
                result="FAILED",
                reason="테스트 실패",
            )
        ],
    )
    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2025", "--include-xbrl"])
    assert result.exit_code == 1, result.output
    assert "FAILED" in result.output
    assert len(collect_calls) == 1  # 재무 수집 자체는 성공


def test_collect_rejects_from_year_before_2015(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(
        cli.app,
        [
            "collect-financials",
            "--company",
            "SK하이닉스",
            "--from-year",
            "2014",
            "--to-year",
            "2024",
        ],
    )
    assert result.exit_code == 2
    assert collect_calls == []


def test_collect_rejects_from_year_after_to_year(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2020"])
    assert result.exit_code == 2
    assert collect_calls == []


def test_collect_resolve_failure_exits_1(
    patched_layers: Settings, collect_calls: list[dict[str, Any]]
) -> None:
    result = runner.invoke(
        cli.app,
        [
            "collect-financials",
            "--company",
            "없는회사이름",
            "--from-year",
            "2021",
            "--to-year",
            "2025",
        ],
    )
    assert result.exit_code == 1
    assert "NOT_FOUND" in result.output
    assert collect_calls == []


def test_collect_missing_key_exits_3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path, api_key="")
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    result = runner.invoke(cli.app, [*BASE_ARGS, "--to-year", "2025"])
    assert result.exit_code == 3
    assert "DART_API_KEY" in result.output
