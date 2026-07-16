"""data_pipeline CLI 단위 테스트 — build-financials·parse-xbrl·reconcile-financials.

명세 CLI-integration §4.1~§4.3·§7. core 계층(build_financial_datasets·reconcile_all·
parse_extracted)은 mock하고 CLI의 옵션 파싱·게이트·종료 코드·출력만 검증한다.
Settings 주입은 test_cli_collect_financials.py 관례를 따른다(data_pipeline 모듈의
get_settings·load_corp_code_registry를 monkeypatch).
"""

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from research_backtest.app import cli
from research_backtest.app.commands import data_pipeline
from research_backtest.core.config import DartConfig, Settings
from research_backtest.core.dart.corp_code import CorpCodeRegistry
from research_backtest.core.dart.xbrl_downloader import EXTRACTED_DIRNAME, xbrl_filing_dir
from research_backtest.core.exceptions import DataValidationError, XbrlParseError
from research_backtest.core.financials.pipeline import (
    CoverageReport,
    FileSummary,
    FinancialBuildReport,
    MatchingReport,
    ValidationCheck,
)
from research_backtest.core.models import DartCorporation
from research_backtest.core.reconciliation.pipeline import (
    BucketSummary,
    ParseSummary,
    ReconciliationRecord,
    ReconciliationReport,
)
from research_backtest.core.xbrl.models import (
    ParsedXbrl,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlUnit,
)

runner = CliRunner()

SK_HYNIX = DartCorporation(
    corp_code="00164779",
    corp_name="SK하이닉스",
    corp_eng_name="SK hynix Inc.",
    stock_code="000660",
    modify_date="20250102",
)


def _make_settings(tmp_path: Path, api_key: str = "unit-test-key") -> Settings:
    return Settings(_env_file=None, dart_api_key=api_key, data_dir=tmp_path / "data")


@pytest.fixture
def dp_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Settings:
    """data_pipeline의 설정·고유번호 계층을 오프라인 mock으로 대체한다."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path)
    monkeypatch.setattr(data_pipeline, "get_settings", lambda: settings)
    monkeypatch.setattr(data_pipeline, "load_dart_config", lambda: DartConfig())
    registry = CorpCodeRegistry([SK_HYNIX])
    monkeypatch.setattr(data_pipeline, "load_corp_code_registry", lambda *a, **k: registry)
    return settings


# --- build-financials (명세 §4.1) --------------------------------------------


def _fake_build_report(*, metrics_rows: int = 136) -> FinancialBuildReport:
    return FinancialBuildReport(
        corp_code="00164779",
        generated_at="2026-07-15T10:00:00+09:00",
        scopes=["CFS", "OFS"],
        fact_count=812,
        matching=MatchingReport(
            per_account_matched_rows={"revenue": 40},
            unmatched_row_count=0,
            sce_skipped_count=0,
            processed_row_count=812,
            unresolved=[],
        ),
        derivation_gaps=[],
        validations=[
            ValidationCheck(name="accounting_identity", checked=30, passed=True, violations=[]),
            ValidationCheck(
                name="available_from_gt_period_end", checked=812, passed=True, violations=[]
            ),
        ],
        coverage=CoverageReport(
            annual_required_complete=True,
            recent_quarters_income_complete=True,
            missing_annual_required=[],
            missing_recent_quarter_income=[],
            recent_quarters_checked=["2025Q1"],
        ),
        files=FileSummary(
            normalized_facts_rows=812,
            quarterly_financials_rows=40,
            annual_financials_rows=10,
            financial_metrics_rows=metrics_rows,
        ),
    )


def test_build_financials_happy_path(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """정상 경로: 136 metrics 재산출을 출력하고 exit 0 (명세 §4.1 DoD)."""
    calls: list[dict[str, Any]] = []

    def fake_build(corp_code: str, **kwargs: Any) -> FinancialBuildReport:
        calls.append({"corp_code": corp_code, "scopes": tuple(kwargs.get("scopes", ()))})
        return _fake_build_report(metrics_rows=136)

    monkeypatch.setattr(data_pipeline, "build_financial_datasets", fake_build)
    result = runner.invoke(cli.app, ["build-financials", "--company", "000660"])
    assert result.exit_code == 0, result.output
    assert calls[0]["corp_code"] == "00164779"
    assert calls[0]["scopes"] == data_pipeline._parse_scopes(None)  # 기본 CFS+OFS
    assert "136" in result.output
    assert "financial_metrics" in result.output


def test_build_financials_missing_raw_exits_1(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """raw jsonl 부재(DataValidationError)는 exit 1 + 준비 명령 안내."""

    def fake_build(corp_code: str, **kwargs: Any) -> FinancialBuildReport:
        raise DataValidationError(
            "정규화 입력 jsonl이 없습니다: x (r2b collect-financials로 전체 재무제표를 먼저 수집)"
        )

    monkeypatch.setattr(data_pipeline, "build_financial_datasets", fake_build)
    result = runner.invoke(cli.app, ["build-financials", "--company", "000660"])
    assert result.exit_code == 1, result.output
    assert "collect-financials" in result.output


def test_build_financials_missing_key_exits_3(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DART 키 미설정은 설정 오류(exit 3)."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    settings = _make_settings(tmp_path, api_key="")
    monkeypatch.setattr(data_pipeline, "get_settings", lambda: settings)
    result = runner.invoke(cli.app, ["build-financials", "--company", "000660"])
    assert result.exit_code == 3
    assert "DART_API_KEY" in result.output


def test_build_financials_rejects_invalid_scope(dp_settings: Settings) -> None:
    result = runner.invoke(cli.app, ["build-financials", "--company", "000660", "--scopes", "XFS"])
    assert result.exit_code == 2  # typer.BadParameter


# --- parse-xbrl (명세 §4.2) --------------------------------------------------


def _fake_parsed() -> ParsedXbrl:
    return ParsedXbrl(
        facts=[
            XbrlFact(
                concept_qname="ifrs-full:Assets",
                concept_namespace="http://x",
                concept_local_name="Assets",
                context_id="c1",
                unit_id="KRW",
                raw_value="100",
                numeric_value=Decimal("100"),
                decimals="0",
                scale=None,
                is_nil=False,
                source_file="entity.xbrl",
            )
        ],
        contexts=[
            XbrlContext(
                context_id="c1",
                entity_identifier="00164779",
                entity_scheme="dart",
                period_type="instant",
                instant_date="2024-12-31",
                start_date=None,
                end_date=None,
                segment_dimensions=[
                    XbrlDimension(axis_qname="ax", member_qname="m", typed_member_value=None)
                ],
                scenario_dimensions=[],
            )
        ],
        units=[XbrlUnit(unit_id="KRW", measure="iso4217:KRW", numerator=None, denominator=None)],
    )


def test_parse_xbrl_happy_path(dp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """정상 경로: extracted를 파싱해 parquet 4종을 저장하고 행수 테이블 출력."""
    corp_code, rcept_no = "00164779", "20250319001045"
    extracted = xbrl_filing_dir(dp_settings.data_dir, corp_code, rcept_no) / EXTRACTED_DIRNAME
    extracted.mkdir(parents=True)
    monkeypatch.setattr(data_pipeline, "parse_extracted", lambda _dir: _fake_parsed())

    result = runner.invoke(
        cli.app, ["parse-xbrl", "--corp-code", corp_code, "--rcept-no", rcept_no]
    )
    assert result.exit_code == 0, result.output
    assert "facts" in result.output
    assert "dimensions" in result.output
    # store가 실제로 parquet을 썼는지
    out_dir = dp_settings.data_dir / "normalized" / "xbrl" / corp_code / rcept_no
    assert (out_dir / "xbrl_facts.parquet").exists()


def test_parse_xbrl_missing_source_exits_1(dp_settings: Settings) -> None:
    """extracted 부재는 exit 1 + collect-financials --include-xbrl 안내."""
    result = runner.invoke(
        cli.app, ["parse-xbrl", "--corp-code", "00164779", "--rcept-no", "99999999999999"]
    )
    assert result.exit_code == 1
    assert "--include-xbrl" in result.output


def test_parse_xbrl_parse_error_exits_1(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    corp_code, rcept_no = "00164779", "20250319001045"
    extracted = xbrl_filing_dir(dp_settings.data_dir, corp_code, rcept_no) / EXTRACTED_DIRNAME
    extracted.mkdir(parents=True)

    def fake_parse(_dir: Path) -> ParsedXbrl:
        raise XbrlParseError("instance 없음")

    monkeypatch.setattr(data_pipeline, "parse_extracted", fake_parse)
    result = runner.invoke(
        cli.app, ["parse-xbrl", "--corp-code", corp_code, "--rcept-no", rcept_no]
    )
    assert result.exit_code == 1
    assert "instance 없음" in result.output


# --- reconcile-financials (명세 §4.3) ----------------------------------------


def _recon_record(
    *, account: str, year: int, quarter: int | None, status: str
) -> ReconciliationRecord:
    return ReconciliationRecord(
        canonical_account_id=account,
        fs_scope="CFS",
        fiscal_year=year,
        fiscal_quarter=quarter,
        period_end=f"{year}-12-31" if quarter is None else f"{year}-03-31",
        rcept_no="20250319001045",
        api_value=Decimal("100"),
        xbrl_value=Decimal("100"),
        absolute_difference=Decimal("0"),
        relative_difference=0.0,
        status=status,
        reason=None,
    )


def _recon_report(
    by_status: dict[str, int],
    *,
    total: int,
    records: list[ReconciliationRecord] | None = None,
) -> ReconciliationReport:
    passing = by_status.get("MATCH", 0) + by_status.get("ROUNDING_DIFFERENCE", 0)
    return ReconciliationReport(
        corp_code="00164779",
        generated_at="2026-07-15T10:00:00+09:00",
        scopes=["CFS", "OFS"],
        parse=ParseSummary(newly_parsed=[], already_parsed=["20250319001045"], failed=[]),
        total=total,
        by_status=by_status,
        annual=BucketSummary(total=70, by_status={"MATCH": 70}, match_rate=1.0),
        quarterly=BucketSummary(
            total=total - 70,
            by_status={k: v for k, v in by_status.items()},
            match_rate=(passing - 70) / (total - 70) if total > 70 else 0.0,
        ),
        account_year_matrix={},
        failures=[],
        records=records or [],
    )


def test_reconcile_happy_path_exits_0(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """총 290 = 260 MATCH + 30 REQUIRES_REVIEW → 기본 exit 0 (명세 §4.3 DoD)."""
    report = _recon_report({"MATCH": 260, "REQUIRES_REVIEW": 30}, total=290)
    monkeypatch.setattr(data_pipeline, "reconcile_all", lambda *a, **k: report)
    result = runner.invoke(cli.app, ["reconcile-financials", "--company", "000660"])
    assert result.exit_code == 0, result.output
    assert "MATCH" in result.output
    assert "REQUIRES_REVIEW" in result.output
    assert "290" in result.output


def test_reconcile_strict_exits_1(dp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """--strict은 PASSING(MATCH·ROUNDING) 밖이 있으면 exit 1 (30 REQUIRES_REVIEW)."""
    report = _recon_report({"MATCH": 260, "REQUIRES_REVIEW": 30}, total=290)
    monkeypatch.setattr(data_pipeline, "reconcile_all", lambda *a, **k: report)
    result = runner.invoke(cli.app, ["reconcile-financials", "--company", "000660", "--strict"])
    assert result.exit_code == 1, result.output


def test_reconcile_mismatch_exits_1(dp_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """비정합 상태(CONTEXT_MISMATCH 등)가 있으면 기본에서도 exit 1."""
    report = _recon_report({"MATCH": 285, "CONTEXT_MISMATCH": 5}, total=290)
    monkeypatch.setattr(data_pipeline, "reconcile_all", lambda *a, **k: report)
    result = runner.invoke(cli.app, ["reconcile-financials", "--company", "000660"])
    assert result.exit_code == 1, result.output


def test_reconcile_filter_shows_detail(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--year·--report 지정 시 해당 레코드 상세 테이블을 출력한다."""
    records = [
        _recon_record(account="revenue", year=2024, quarter=None, status="MATCH"),
        _recon_record(account="revenue", year=2024, quarter=1, status="MATCH"),
        _recon_record(account="revenue", year=2023, quarter=None, status="MATCH"),
    ]
    report = _recon_report({"MATCH": 290}, total=290, records=records)
    monkeypatch.setattr(data_pipeline, "reconcile_all", lambda *a, **k: report)
    result = runner.invoke(
        cli.app,
        ["reconcile-financials", "--company", "000660", "--year", "2024", "--report", "annual"],
    )
    assert result.exit_code == 0, result.output
    assert "필터 상세" in result.output
    assert "1건" in result.output  # 2024 annual 1건만


def test_reconcile_rejects_invalid_report(dp_settings: Settings) -> None:
    result = runner.invoke(
        cli.app, ["reconcile-financials", "--company", "000660", "--report", "yearly"]
    )
    assert result.exit_code == 2  # typer.BadParameter


def test_reconcile_missing_data_exits_1(
    dp_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A4 산출 부재(FileNotFoundError)는 exit 1."""

    def fake_reconcile(*a: Any, **k: Any) -> ReconciliationReport:
        raise FileNotFoundError("normalized_facts.parquet 없음")

    monkeypatch.setattr(data_pipeline, "reconcile_all", fake_reconcile)
    result = runner.invoke(cli.app, ["reconcile-financials", "--company", "000660"])
    assert result.exit_code == 1
    assert "normalized_facts" in result.output
