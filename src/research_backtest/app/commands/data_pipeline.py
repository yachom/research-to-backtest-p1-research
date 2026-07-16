"""데이터 파이프라인 서브커맨드 — build-financials·parse-xbrl·reconcile-financials.

docs/specs/CLI-integration.md §4.1~§4.3(T1)의 구현. 각 명령은 이미 수집된 로컬
산출물(A2 raw jsonl·B1 XBRL 원본·A4 정규화)을 소비해 재무 데이터셋 빌드·XBRL
파싱·API↔XBRL 대조를 수행한다. ``register(app)``으로 루트 앱에 등록되며,
``app.cli``를 import하지 않는다(순환 금지 — 명세 §3).

종료 코드(명세 §3): 0 성공 / 1 실행·검증·데이터 오류 / 3 설정 오류(ConfigError).
회사 식별 헬퍼는 cli.py ``_resolve_or_exit``·``_print_resolve_failure``를 복제한다
(T1·T2 파일 간 import 금지 — 명세 §3, 통합 정리는 병합 후 메인 세션 후속).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from research_backtest.core.config import Settings, get_settings, load_dart_config
from research_backtest.core.constants import FsDiv
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import corp_code_cache_dir, load_corp_code_registry
from research_backtest.core.dart.models import ResolveResult
from research_backtest.core.dart.xbrl_downloader import EXTRACTED_DIRNAME, xbrl_filing_dir
from research_backtest.core.exceptions import (
    ConfigError,
    DartApiError,
    DartTransportError,
    DataValidationError,
    XbrlParseError,
)
from research_backtest.core.financials.pipeline import (
    BUILD_REPORT_FILENAME,
    FinancialBuildReport,
    build_financial_datasets,
    financials_out_dir,
)
from research_backtest.core.models import DartCorporation
from research_backtest.core.reconciliation.compare import PASSING_STATUSES, ReconciliationStatus
from research_backtest.core.reconciliation.pipeline import (
    FAILURES_FILENAME,
    REPORT_FILENAME,
    ReconciliationRecord,
    ReconciliationReport,
    reconcile_all,
    reconciliation_out_dir,
)
from research_backtest.core.xbrl.parser import dimension_rows, parse_extracted
from research_backtest.core.xbrl.store import store_parsed_xbrl, xbrl_normalized_dir

console = Console()

# 종료 코드 (명세 §3 — 각 모듈 로컬 상수, 값 고정).
DATA_ERROR_EXIT_CODE = 1
CONFIG_ERROR_EXIT_CODE = 3

# --report 표시 필터 → 단독분기 번호 (annual은 fiscal_quarter=None). 명세 §4.3.
_REPORT_QUARTER: dict[str, int] = {"q1": 1, "half": 2, "q3": 3}
_REPORT_CHOICES = ("annual", "half", "q1", "q3")

# 비정합 상태 3종 — 기본 실행에서 exit 1을 유발한다 (명세 §4.3).
_MISMATCH_STATUSES = (
    ReconciliationStatus.CONTEXT_MISMATCH,
    ReconciliationStatus.SCOPE_MISMATCH,
    ReconciliationStatus.ACCOUNT_MAPPING_MISMATCH,
)


# --- 공통 헬퍼 (cli.py 복제 — 명세 §3) ---------------------------------------


def _parse_scopes(scopes: list[str] | None) -> tuple[FsDiv, ...]:
    """--scopes 값을 검증·중복 제거 — CFS/OFS 외는 BadParameter (cli.py _parse_scopes 복제)."""
    if not scopes:
        return (FsDiv.CFS, FsDiv.OFS)
    parsed: dict[FsDiv, None] = {}
    for raw in scopes:
        try:
            parsed[FsDiv(raw.strip().upper())] = None
        except ValueError as err:
            raise typer.BadParameter(f"--scopes는 CFS/OFS만 허용합니다: {raw!r}") from err
    return tuple(parsed)


def _resolve_corp(company: str, settings: Settings) -> DartCorporation:
    """--company를 DART 고유번호 파일로 식별한다 (명세 §3 공통 규약).

    cli.py ``_resolve_or_exit``와 동일 동작: 키 미설정은 exit 3, AMBIGUOUS는 후보
    테이블·NOT_FOUND는 안내 후 exit 1, DART 호출 실패도 exit 1. 고유번호 캐시가
    신선하면 실제 API 호출 없이 로컬 캐시로 식별한다(build/reconcile은 로컬 소비).
    """
    try:
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err
    try:
        with DartClient(
            api_key,
            timeout=dart_config.timeout_seconds,
            max_attempts=dart_config.retry.max_attempts,
            backoff_seconds=dart_config.retry.backoff_seconds,
        ) as client:
            registry = load_corp_code_registry(
                client,
                corp_code_cache_dir(settings.data_dir),
                refresh_days=dart_config.corp_code_cache.refresh_days,
            )
            result = registry.resolve(company)
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    if result.matched is None:
        _print_resolve_failure(company, result)
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)
    return result.matched


def _print_resolve_failure(query: str, result: ResolveResult) -> None:
    """AMBIGUOUS 후보 테이블·NOT_FOUND 안내 (cli.py _print_resolve_failure 복제)."""
    if result.method == "AMBIGUOUS":
        console.print(f"[yellow]'{query}'에 대한 후보가 여러 개입니다 (AMBIGUOUS).[/yellow]")
        table = Table(title="후보 기업 (상장 우선, 최대 10)")
        for column in ("corp_code", "stock_code", "corp_name"):
            table.add_column(column)
        for corp in result.candidates:
            table.add_row(corp.corp_code, corp.stock_code or "-", corp.corp_name)
        console.print(table)
    else:
        console.print(f"[red]'{query}'에 해당하는 기업을 찾지 못했습니다 (NOT_FOUND).[/red]")
    console.print("6자리 종목코드로 다시 시도하면 정확히 식별됩니다 (예: --company 000660).")


# --- build-financials (명세 §4.1) --------------------------------------------


def build_financials(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    scopes: Annotated[
        list[str] | None,
        typer.Option("--scopes", help="재무제표 구분 (CFS/OFS, 반복 지정 가능 — 기본 둘 다)"),
    ] = None,
) -> None:
    """수집된 재무 raw를 정규화·단독분기·지표·검증·저장한다 (명세 §4.1, A4 §7~§8).

    ``build_financial_datasets``가 jsonl→정규화→지표를 관통해 parquet 4종 +
    build_report.json을 산출한다(``data/normalized/financials/{corp_code}/``).
    회계식·available_from 위반은 DataValidationError로 exit 1이며, raw jsonl·거래일
    캘린더 부재는 예외 메시지에 준비 명령 안내가 포함된다(collect-financials·
    collect-market). 종료 코드: 0 성공 / 1 검증·데이터 오류 / 3 설정 오류.
    """
    fs_divs = _parse_scopes(scopes)
    settings = get_settings()
    corp = _resolve_corp(company, settings)
    try:
        report = build_financial_datasets(
            corp.corp_code, data_dir=settings.data_dir, scopes=fs_divs
        )
    except (DataValidationError, FileNotFoundError) as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    _print_build_report(report, corp, financials_out_dir(settings.data_dir, corp.corp_code))


def _print_build_report(report: FinancialBuildReport, corp: DartCorporation, out_dir: Path) -> None:
    """빌드 결과: 요약·검증·커버리지·저장 경로 (명세 §4.1)."""
    summary = Table(title=f"재무 데이터셋 빌드 — {corp.corp_name} ({report.corp_code})")
    for column in ("항목", "값"):
        summary.add_column(column)
    summary.add_row("scopes", ", ".join(report.scopes))
    summary.add_row("fact_count", str(report.fact_count))
    summary.add_row("normalized_facts 행수", str(report.files.normalized_facts_rows))
    summary.add_row("quarterly_financials 행수", str(report.files.quarterly_financials_rows))
    summary.add_row("annual_financials 행수", str(report.files.annual_financials_rows))
    summary.add_row("financial_metrics 행수", str(report.files.financial_metrics_rows))
    console.print(summary)

    checks = Table(title="검증 (명세 A4 §8)")
    for column in ("항목", "검사수", "결과", "위반"):
        checks.add_column(column)
    for check in report.validations:
        status = "[green]pass[/green]" if check.passed else "[red]fail[/red]"
        checks.add_row(check.name, str(check.checked), status, str(len(check.violations)))
    console.print(checks)

    coverage = report.coverage
    console.print(
        "커버리지 — 연간 필수계정: "
        + ("[green]완비[/green]" if coverage.annual_required_complete else "[yellow]부족[/yellow]")
        + " · 최근 분기 단독손익: "
        + (
            "[green]완비[/green]"
            if coverage.recent_quarters_income_complete
            else "[yellow]부족[/yellow]"
        )
    )
    if coverage.missing_annual_required:
        console.print(f"  누락 연간계정: {', '.join(coverage.missing_annual_required)}")
    if coverage.missing_recent_quarter_income:
        console.print(f"  누락 분기손익: {', '.join(coverage.missing_recent_quarter_income)}")

    console.print(f"저장 경로: {out_dir}")
    console.print(f"빌드 리포트: {out_dir / BUILD_REPORT_FILENAME}")


# --- parse-xbrl (명세 §4.2) --------------------------------------------------


def parse_xbrl(
    corp_code: Annotated[str, typer.Option("--corp-code", help="DART 8자리 법인코드")],
    rcept_no: Annotated[str, typer.Option("--rcept-no", help="공시 접수번호")],
) -> None:
    """XBRL 원본에서 Fact·Context·Unit·Dimension을 추출·정규화 저장한다 (명세 §4.2, B2).

    ``raw/dart/xbrl/{corp_code}/{rcept_no}/extracted/``를 파싱해
    ``normalized/xbrl/{corp_code}/{rcept_no}/``에 parquet 4종을 저장한다. 원본이
    없으면 exit 1(collect-financials --include-xbrl 안내), XBRL 파싱 실패도 exit 1.
    """
    settings = get_settings()
    extracted_dir = xbrl_filing_dir(settings.data_dir, corp_code, rcept_no) / EXTRACTED_DIRNAME
    if not extracted_dir.is_dir():
        console.print(f"[red]XBRL 원본이 없습니다: {extracted_dir}[/red]")
        console.print("r2b collect-financials --include-xbrl 로 원본을 먼저 수집하세요.")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)
    try:
        parsed = parse_extracted(extracted_dir)
    except XbrlParseError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err

    out_dir = xbrl_normalized_dir(settings.data_dir, corp_code, rcept_no)
    paths = store_parsed_xbrl(parsed, out_dir)

    table = Table(title=f"XBRL 파싱 결과 — {corp_code} / {rcept_no}")
    for column in ("테이블", "행수", "저장 경로"):
        table.add_column(column)
    counts = {
        "facts": len(parsed.facts),
        "contexts": len(parsed.contexts),
        "units": len(parsed.units),
        "dimensions": len(dimension_rows(parsed.contexts)),
    }
    filenames = {
        "facts": "xbrl_facts.parquet",
        "contexts": "xbrl_contexts.parquet",
        "units": "xbrl_units.parquet",
        "dimensions": "xbrl_dimensions.parquet",
    }
    for key in ("facts", "contexts", "units", "dimensions"):
        table.add_row(key, str(counts[key]), str(paths[filenames[key]]))
    console.print(table)
    console.print(f"저장 경로: {out_dir}")


# --- reconcile-financials (명세 §4.3) ----------------------------------------


def reconcile_financials(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    year: Annotated[
        int | None, typer.Option("--year", help="표시 필터: 사업연도 (대조는 항상 전량)")
    ] = None,
    report: Annotated[
        str | None,
        typer.Option("--report", help="표시 필터: 보고서 종류 (annual/half/q1/q3)"),
    ] = None,
    scopes: Annotated[
        list[str] | None,
        typer.Option("--scopes", help="재무제표 구분 (CFS/OFS, 반복 지정 가능 — 기본 둘 다)"),
    ] = None,
    strict: Annotated[
        bool,
        typer.Option("--strict", help="MATCH·ROUNDING 외 상태가 하나라도 있으면 exit 1"),
    ] = False,
) -> None:
    """전체 재무제표 API와 XBRL 원본의 대표계정을 교차검증한다 (명세 §4.3, B3).

    **대조 자체는 항상 전량**이다(``reconcile_all`` — 전 XBRL 파싱 보장 포함,
    멱등). ``--year``·``--report``는 README §26.4의 필수 인자를 **표시 필터**로
    재해석한 것으로(B3 파이프라인이 전량 대조·저장 구조), 지정 시 해당 레코드
    상세만 추가 출력한다.

    종료 코드: CONTEXT_MISMATCH+SCOPE_MISMATCH+ACCOUNT_MAPPING_MISMATCH > 0 → 1;
    ``--strict``이면 PASSING_STATUSES(MATCH·ROUNDING) 밖 전부(>0) → 1; 그 외 0.
    현 실데이터 기대: 총 290 = 연간 70 MATCH + 분기 190 MATCH·30 REQUIRES_REVIEW →
    기본 exit 0, --strict exit 1.
    """
    fs_divs = _parse_scopes(scopes)
    report_filter = _parse_report_filter(report)
    settings = get_settings()
    corp = _resolve_corp(company, settings)
    try:
        recon = reconcile_all(corp.corp_code, data_dir=settings.data_dir, scopes=fs_divs)
    except (DataValidationError, FileNotFoundError, XbrlParseError) as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err

    _print_reconciliation(
        recon,
        corp,
        year=year,
        report_filter=report_filter,
        out_dir=reconciliation_out_dir(settings.data_dir, corp.corp_code),
    )

    if _reconciliation_failed(recon, strict=strict):
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)


def _parse_report_filter(report: str | None) -> str | None:
    """--report 표시 필터를 검증한다 — annual/half/q1/q3 외 값은 BadParameter."""
    if report is None:
        return None
    normalized = report.strip().lower()
    if normalized not in _REPORT_CHOICES:
        raise typer.BadParameter("--report는 annual/half/q1/q3 중 하나여야 합니다.")
    return normalized


def _reconciliation_failed(recon: ReconciliationReport, *, strict: bool) -> bool:
    """종료 코드 결정 (명세 §4.3)."""
    if strict:
        passing = sum(v for k, v in recon.by_status.items() if k in PASSING_STATUSES)
        return (recon.total - passing) > 0
    return sum(recon.by_status.get(status, 0) for status in _MISMATCH_STATUSES) > 0


def _matches_report(record: ReconciliationRecord, report_filter: str) -> bool:
    """레코드가 --report 표시 필터에 부합하는지 (annual → 연간, 그 외 → 단독분기)."""
    if report_filter == "annual":
        return record.fiscal_quarter is None
    return record.fiscal_quarter == _REPORT_QUARTER[report_filter]


def _print_reconciliation(
    recon: ReconciliationReport,
    corp: DartCorporation,
    *,
    year: int | None,
    report_filter: str | None,
    out_dir: Path,
) -> None:
    """대조 결과: parse 요약·상태 분포·match_rate·필터 상세·failures 경로 (명세 §4.3)."""
    parse = recon.parse
    console.print(
        f"XBRL 파싱 보장 — 신규 {len(parse.newly_parsed)} · 기존 {len(parse.already_parsed)} · "
        f"실패 {len(parse.failed)}"
    )
    if parse.failed:
        for failure in parse.failed:
            console.print(f"  [yellow]파싱 실패 rcept={failure.rcept_no}: {failure.error}[/yellow]")

    status_table = Table(
        title=f"정합성 대조 상태 분포 — {corp.corp_name} ({recon.corp_code}), 총 {recon.total}건"
    )
    for column in ("상태", "건수"):
        status_table.add_column(column)
    for status, count in recon.by_status.items():
        status_table.add_row(status, str(count))
    console.print(status_table)
    console.print(
        f"match_rate — 연간 {recon.annual.match_rate:.3f} "
        f"({recon.annual.total}건) · 분기 {recon.quarterly.match_rate:.3f} "
        f"({recon.quarterly.total}건)"
    )

    if year is not None or report_filter is not None:
        _print_filtered_records(recon.records, year=year, report_filter=report_filter)

    console.print(f"대조 리포트: {out_dir / REPORT_FILENAME}")
    console.print(f"실패 CSV: {out_dir / FAILURES_FILENAME}")


def _print_filtered_records(
    records: list[ReconciliationRecord], *, year: int | None, report_filter: str | None
) -> None:
    """--year·--report 표시 필터에 부합하는 레코드 상세 테이블 (명세 §4.3)."""
    filtered = [
        r
        for r in records
        if (year is None or r.fiscal_year == year)
        and (report_filter is None or _matches_report(r, report_filter))
    ]
    label = f"필터 상세 (year={year}, report={report_filter}) — {len(filtered)}건"
    table = Table(title=label)
    for column in ("계정", "scope", "연도", "분기", "상태", "API", "XBRL"):
        table.add_column(column)
    for r in filtered:
        table.add_row(
            r.canonical_account_id,
            r.fs_scope,
            str(r.fiscal_year),
            "연간" if r.fiscal_quarter is None else f"Q{r.fiscal_quarter}",
            r.status,
            "-" if r.api_value is None else str(r.api_value),
            "-" if r.xbrl_value is None else str(r.xbrl_value),
        )
    console.print(table)
    if not filtered:
        console.print("[yellow]필터에 부합하는 레코드가 없습니다.[/yellow]")


# --- 등록 (명세 §3·§4.6) -----------------------------------------------------


def register(app: typer.Typer) -> None:
    """data_pipeline 3종 명령을 루트 앱에 등록한다 (명세 §3 register 패턴)."""
    app.command("build-financials")(build_financials)
    app.command("parse-xbrl")(parse_xbrl)
    app.command("reconcile-financials")(reconcile_financials)
