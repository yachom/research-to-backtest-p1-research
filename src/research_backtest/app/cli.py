"""Research-to-Backtest CLI (README §26).

resolve-company(A1)·collect-financials(A2)는 구현되었다. 나머지 명령의
구현 시점은 docs/MILESTONES.md의 Phase 표를 따른다.
"""

from collections import Counter
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from research_backtest import __version__
from research_backtest.app.commands.backtest_cmd import register as register_backtest
from research_backtest.app.commands.data_pipeline import register as register_data_pipeline
from research_backtest.app.commands.hitl_flow import (
    _create_run_impl,
    run_generate_candidates,
)
from research_backtest.app.commands.hitl_flow import (
    register as register_hitl_flow,
)
from research_backtest.core.config import (
    Settings,
    get_settings,
    load_dart_config,
    load_market_config,
)
from research_backtest.core.constants import FsDiv, PeriodicReportType, ReprtCode, StatementType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import (
    CorpCodeRegistry,
    corp_code_cache_dir,
    load_corp_code_registry,
)
from research_backtest.core.dart.disclosure_search import find_periodic_filings, latest_filing
from research_backtest.core.dart.financial_api import (
    JSONL_FILENAME,
    MIN_SUPPORTED_YEAR,
    CollectionSummary,
    financials_out_dir,
)
from research_backtest.core.dart.financial_api import (
    collect_financials as run_collect_financials,
)
from research_backtest.core.dart.models import DartFiling, ResolveMethod, ResolveResult
from research_backtest.core.dart.xbrl_downloader import (
    XbrlDownloadOutcome,
    download_xbrl_filings,
    xbrl_filing_dir,
)
from research_backtest.core.exceptions import (
    ConfigError,
    DartApiError,
    DartTransportError,
    DataValidationError,
    MarketAuthError,
)
from research_backtest.core.market.collector import (
    MarketCollectionSummary,
    market_calendar_path,
    market_normalized_stock_dir,
    market_raw_index_dir,
    market_raw_stock_dir,
)
from research_backtest.core.market.collector import (
    collect_market_data as run_collect_market_data,
)
from research_backtest.core.market.source import PykrxSource
from research_backtest.core.models import DartCorporation

app = typer.Typer(
    name="r2b",
    help="AI 기반 기업 리서치 및 투자전략 검증 시스템 (OpenDART · XBRL · 백테스트)",
    no_args_is_help=True,
    # 예외 트레이스의 로컬 변수 출력에 인증키가 노출되지 않도록 비활성화 (README §30.2)
    pretty_exceptions_show_locals=False,
)

# 서브커맨드 모듈 등록 (명세 CLI-integration §4.6·§5.8).
register_data_pipeline(app)
register_backtest(app)
register_hitl_flow(app)

console = Console()

KST = ZoneInfo("Asia/Seoul")

RESOLVE_FAILURE_EXIT_CODE = 1
CONFIG_ERROR_EXIT_CODE = 3


@app.command()
def version() -> None:
    """버전을 출력한다."""
    console.print(__version__)


@app.command("resolve-company")
def resolve_company(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    as_of_date: Annotated[
        str | None,
        typer.Option("--as-of-date", help="분석 기준일 YYYY-MM-DD (기본: 오늘 KST)"),
    ] = None,
    refresh_corp_codes: Annotated[
        bool,
        typer.Option("--refresh-corp-codes", help="고유번호 파일 캐시를 강제로 갱신"),
    ] = False,
) -> None:
    """기업명·종목코드로 DART 법인(corp_code)을 식별하고 최근 정기보고서를 찾는다.

    README §19.1~19.2(P1-01/02), §31 Milestone 1 완료 조건에 해당한다.
    종료 코드: 0 성공 / 1 NOT_FOUND·AMBIGUOUS·DART 오류 / 3 설정 오류(키 미설정 등).
    """
    try:
        settings = get_settings()
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    try:
        as_of = date.fromisoformat(as_of_date) if as_of_date else datetime.now(KST).date()
    except ValueError as err:
        raise typer.BadParameter("--as-of-date는 YYYY-MM-DD 형식이어야 합니다.") from err

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
                force=refresh_corp_codes,
            )
            corp, method = _resolve_or_exit(registry, company)
            filings = find_periodic_filings(
                client, corp.corp_code, as_of_date=as_of, lookback_years=2
            )
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err

    _print_company(corp, method)
    annual = latest_filing(filings, PeriodicReportType.ANNUAL)
    interim = _latest_interim(filings)
    _print_filings(annual, interim)


def _resolve_or_exit(
    registry: CorpCodeRegistry, company: str
) -> tuple[DartCorporation, ResolveMethod]:
    """기업 식별 성공 시 (기업, 매칭 방법)을 반환하고, 실패 시 후보 출력 후 exit 1.

    resolve-company·collect-financials가 공유하는 규칙이다(명세 A2 §0, §5) —
    AMBIGUOUS는 후보 테이블, NOT_FOUND는 안내 메시지를 출력한다.
    """
    result = registry.resolve(company)
    if result.matched is None:
        _print_resolve_failure(company, result)
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE)
    return result.matched, result.method


def _latest_interim(filings: Sequence[DartFiling]) -> DartFiling | None:
    """분기·반기보고서 중 최신 1건 (README §31 M1 완료 조건의 두 번째 행)."""
    interim_types = (PeriodicReportType.HALF, PeriodicReportType.Q1, PeriodicReportType.Q3)
    interim = [f for f in filings if f.report_type in interim_types]
    return max(interim, key=lambda f: (f.rcept_dt, f.rcept_no), default=None)


def _print_company(corp: DartCorporation, method: str) -> None:
    table = Table(title="기업 식별 결과 (README §19.1)")
    table.add_column("항목")
    table.add_column("값")
    table.add_row("corp_code", corp.corp_code)
    table.add_row("corp_name", corp.corp_name)
    table.add_row("stock_code", corp.stock_code or "-")
    table.add_row("상장 여부", "상장" if corp.stock_code else "비상장")
    table.add_row("매칭 방법", method)
    console.print(table)


def _print_filings(annual: DartFiling | None, interim: DartFiling | None) -> None:
    table = Table(title="최근 정기보고서 (README §19.2)")
    for column in ("구분", "rcept_no", "rcept_dt", "report_nm"):
        table.add_column(column)
    for label, filing in (("최근 사업보고서", annual), ("최근 분기·반기보고서", interim)):
        if filing is None:
            table.add_row(label, "-", "-", "없음")
        else:
            table.add_row(label, filing.rcept_no, filing.rcept_dt.isoformat(), filing.report_nm)
    console.print(table)


def _print_resolve_failure(query: str, result: ResolveResult) -> None:
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


@app.command("collect-financials")
def collect_financials(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    from_year: Annotated[
        int, typer.Option("--from-year", help=f"수집 시작 사업연도 ({MIN_SUPPORTED_YEAR} 이상)")
    ],
    to_year: Annotated[int, typer.Option("--to-year", help="수집 종료 사업연도")],
    scopes: Annotated[
        list[str] | None,
        typer.Option("--scopes", help="재무제표 구분 (CFS/OFS, 반복 지정 가능 — 기본 둘 다)"),
    ] = None,
    force_download: Annotated[
        bool, typer.Option("--force-download", help="캐시를 무시하고 재수집 (README §8.3)")
    ] = False,
    include_xbrl: Annotated[
        bool, typer.Option("--include-xbrl", help="XBRL 원본 ZIP 함께 수집 (Milestone B1)")
    ] = False,
) -> None:
    """DART 전체 재무제표 API로 연도별 CFS·OFS raw를 수집한다 (README §19.3, §31 M2).

    캐시된 요청은 재호출하지 않으며(멱등), 미제출 보고서(013)는 NO_DATA로
    기록된다 — 실패가 아니다. 종료 코드: 0 성공 / 1 식별 실패·DART 오류 /
    3 설정 오류.
    """
    if from_year > to_year:
        raise typer.BadParameter(
            f"--from-year({from_year})는 --to-year({to_year})보다 클 수 없습니다."
        )
    if from_year < MIN_SUPPORTED_YEAR:
        raise typer.BadParameter(
            f"전체 재무제표 API는 {MIN_SUPPORTED_YEAR}년 이후 사업연도만 제공합니다 (README §6.4)."
        )
    fs_divs = _parse_scopes(scopes)

    try:
        settings = get_settings()
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    xbrl_outcomes: list[XbrlDownloadOutcome] | None = None
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
            corp, _method = _resolve_or_exit(registry, company)
            out_dir = financials_out_dir(settings.data_dir, corp.corp_code)
            summary = run_collect_financials(
                client,
                corp.corp_code,
                from_year=from_year,
                to_year=to_year,
                fs_divs=fs_divs,
                out_dir=out_dir,
                force=force_download,
                min_interval_seconds=dart_config.min_interval_seconds,
            )
            if include_xbrl:
                xbrl_outcomes = _collect_xbrl_filings(
                    client,
                    corp,
                    from_year=from_year,
                    to_year=to_year,
                    data_dir=settings.data_dir,
                    force=force_download,
                    min_interval_seconds=dart_config.min_interval_seconds,
                )
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err

    _print_collection(corp, summary, out_dir)
    if xbrl_outcomes is not None:
        _print_xbrl_collection(xbrl_outcomes, settings.data_dir, corp.corp_code)
        if any(outcome.result == "FAILED" for outcome in xbrl_outcomes):
            raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE)


_REPRT_LABELS: dict[ReprtCode, str] = {
    ReprtCode.Q1: "1분기보고서",
    ReprtCode.HALF: "반기보고서",
    ReprtCode.Q3: "3분기보고서",
    ReprtCode.ANNUAL: "사업보고서",
}


def _parse_scopes(scopes: list[str] | None) -> tuple[FsDiv, ...]:
    """--scopes 값을 검증·중복 제거한다 — CFS/OFS 외 값은 BadParameter (명세 A2 §5)."""
    if not scopes:
        return (FsDiv.CFS, FsDiv.OFS)
    parsed: dict[FsDiv, None] = {}
    for raw in scopes:
        try:
            parsed[FsDiv(raw.strip().upper())] = None
        except ValueError as err:
            raise typer.BadParameter(f"--scopes는 CFS/OFS만 허용합니다: {raw!r}") from err
    return tuple(parsed)


def _format_sj_div_counts(counts: dict[str, int]) -> str:
    """sj_div별 행수 요약 — BS·IS·CIS·CF·SCE 순서, 없는 종류는 생략."""
    if not counts:
        return "-"
    known = [statement.value for statement in StatementType]
    ordered = [key for key in known if key in counts] + [k for k in counts if k not in known]
    return " ".join(f"{key}:{counts[key]}" for key in ordered)


def _count_jsonl_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(encoding="utf-8") as fp:
        return sum(1 for line in fp if line.strip())


def _print_collection(corp: DartCorporation, summary: CollectionSummary, out_dir: Path) -> None:
    """수집 결과 테이블 + 저장 경로·jsonl 라인 수 출력 (명세 A2 §5)."""
    table = Table(title=f"전체 재무제표 수집 결과 — {corp.corp_name} ({summary.corp_code})")
    for column in ("연도", "보고서", "scope", "결과", "행수", "sj_div별 행수"):
        table.add_column(column)
    for outcome in summary.outcomes:
        table.add_row(
            outcome.bsns_year,
            f"{_REPRT_LABELS[outcome.reprt_code]}({outcome.reprt_code.value})",
            outcome.fs_div.value,
            outcome.result,
            str(outcome.row_count),
            _format_sj_div_counts(outcome.sj_div_counts),
        )
    console.print(table)
    line_count = _count_jsonl_lines(out_dir / JSONL_FILENAME)
    console.print(f"저장 경로: {out_dir}")
    console.print(f"병합본 {JSONL_FILENAME}: {line_count}라인")


_XBRL_RESULT_LABELS: dict[str, str] = {
    "FETCHED": "신규 수집",
    "CACHED": "캐시",
    "NO_DATA": "데이터 없음(013/014)",
    "NO_DATA_CACHED": "데이터 없음(캐시)",
    "SKIPPED": "건너뜀",
    "FAILED": "실패",
}


def _collect_xbrl_filings(
    client: DartClient,
    corp: DartCorporation,
    *,
    from_year: int,
    to_year: int,
    data_dir: Path,
    force: bool,
    min_interval_seconds: float,
) -> list[XbrlDownloadOutcome]:
    """수집 재무연도 범위의 정기보고서 XBRL 원본을 함께 수집한다 (명세 §4.5, B1).

    FY 연간보고서는 이듬해 3월에 접수되므로 ``rcept_dt.year ∈ [from_year, to_year+1]``
    로 경계를 포함해 필터한다. 실제 다운로드는 ``download_xbrl_filings``가 건별
    캐시·negative cache·실패 격리를 담당한다(멱등).
    """
    today = datetime.now(KST).date()
    filings = find_periodic_filings(
        client, corp.corp_code, as_of_date=today, lookback_years=today.year - from_year + 1
    )
    selected = [f for f in filings if from_year <= f.rcept_dt.year <= to_year + 1]
    return download_xbrl_filings(
        client,
        selected,
        data_dir=data_dir,
        force=force,
        min_interval_seconds=min_interval_seconds,
    )


def _print_xbrl_collection(
    outcomes: list[XbrlDownloadOutcome], data_dir: Path, corp_code: str
) -> None:
    """XBRL 원본 수집 결과 테이블 + 결과별 건수 요약 (명세 §4.5)."""
    table = Table(title="XBRL 원본 수집 결과 (README §19.4)")
    for column in ("rcept_no", "보고서", "결과", "경로 또는 사유"):
        table.add_column(column)
    for outcome in outcomes:
        if outcome.result in ("FETCHED", "CACHED"):
            detail = str(xbrl_filing_dir(data_dir, corp_code, outcome.rcept_no))
        else:
            detail = outcome.reason or "-"
        table.add_row(
            outcome.rcept_no,
            outcome.report_name,
            f"{_XBRL_RESULT_LABELS.get(outcome.result, outcome.result)}({outcome.result})",
            detail,
        )
    console.print(table)
    counts = Counter(outcome.result for outcome in outcomes)
    summary = " ".join(f"{key}:{counts[key]}" for key in sorted(counts))
    console.print(f"XBRL 결과 요약: {summary or '수집 대상 없음'}")


@app.command("collect-market")
def collect_market(
    company: Annotated[
        str | None,
        typer.Option("--company", help="기업명 또는 6자리 종목코드 (DART로 식별, 키 필요)"),
    ] = None,
    stock_code: Annotated[
        str | None,
        typer.Option("--stock-code", help="6자리 종목코드 (DART 없이 동작)"),
    ] = None,
    from_date: Annotated[
        str | None,
        typer.Option("--from-date", help="수집 시작일 YYYY-MM-DD (기본: configs/market.yaml)"),
    ] = None,
    to_date: Annotated[
        str | None,
        typer.Option("--to-date", help="수집 종료일 YYYY-MM-DD (기본: KST 어제)"),
    ] = None,
    index: Annotated[
        str | None,
        typer.Option("--index", help="벤치마크 지수 코드 (기본: configs/market.yaml — KOSPI 1001)"),
    ] = None,
    force_download: Annotated[
        bool, typer.Option("--force-download", help="캐시를 무시하고 재수집 (README §8.3)")
    ] = False,
) -> None:
    """pykrx로 수정주가 OHLCV·투자자 수급·지수·거래일 캘린더를 수집한다 (명세 A3, MILESTONES D1).

    수집 종료일 기본값은 **KST 오늘-1일**이다 — 장중 실행 시 미완성 일봉이
    저장·캐시되는 것을 막는다(명세 A3 §6). KRX 자격증명(KRX_ID/KRX_PW)이
    없으면 가격(OHLCV)만 수집하는 부분 수집 모드로 동작하며 exit 0이다.
    종료 코드: 0 성공(부분 수집 포함) / 1 소스·검증 오류 / 3 설정 오류.
    """
    if (company is None) == (stock_code is None):
        raise typer.BadParameter(
            "--company와 --stock-code 중 정확히 하나만 지정하세요 (명세 A3 §6)."
        )

    try:
        settings = get_settings()
        market_config = load_market_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    start = _parse_iso_date_option(from_date, "--from-date") or market_config.default_start_date
    end = _parse_iso_date_option(to_date, "--to-date") or (
        datetime.now(KST).date() - timedelta(days=1)
    )
    if start > end:
        raise typer.BadParameter(f"--from-date({start})는 --to-date({end})보다 클 수 없습니다.")
    index_code = index or market_config.default_index_code

    if stock_code is not None:
        code = stock_code.strip()
        if len(code) != 6 or not code.isdigit():
            raise typer.BadParameter(f"--stock-code는 6자리 숫자여야 합니다: {stock_code!r}")
        display_name = code
    else:
        code, display_name = _resolve_listed_stock_code(company or "", settings)

    source = PykrxSource(krx_id=settings.krx_id, krx_pw=settings.krx_pw)
    try:
        summary = run_collect_market_data(
            source,
            stock_code=code,
            index_code=index_code,
            from_date=start,
            to_date=end,
            data_dir=settings.data_dir,
            force=force_download,
            min_interval_seconds=market_config.min_interval_seconds,
        )
    except (DataValidationError, MarketAuthError) as err:
        console.print(f"[red]시장 데이터 수집 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err

    _print_market_collection(summary, display_name, settings.data_dir, start, end)


def _parse_iso_date_option(value: str | None, option: str) -> date | None:
    """YYYY-MM-DD 옵션 파싱 — 형식 오류는 BadParameter, 미지정(None)은 그대로."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise typer.BadParameter(f"{option}는 YYYY-MM-DD 형식이어야 합니다: {value!r}") from err


def _resolve_listed_stock_code(company: str, settings: Settings) -> tuple[str, str]:
    """--company를 DART 고유번호 파일로 식별해 (종목코드, 기업명)을 반환한다 (명세 A3 §6).

    _resolve_or_exit 재사용 — DART 키 필요(미설정이면 exit 3). 비상장
    법인은 시장 데이터가 없으므로 exit 1.
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
            corp, _method = _resolve_or_exit(registry, company)
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE) from err
    if not corp.stock_code:
        console.print(
            f"[red]'{corp.corp_name}'은(는) 비상장 법인입니다 — "
            "시장 데이터를 수집할 수 없습니다.[/red]"
        )
        raise typer.Exit(code=RESOLVE_FAILURE_EXIT_CODE)
    return corp.stock_code, corp.corp_name


_DATASET_LABELS: dict[str, str] = {
    "OHLCV": "수정주가 OHLCV",
    "INVESTOR_VALUE": "투자자 순매수",
    "INDEX": "지수 OHLCV",
    "CALENDAR": "거래일 캘린더",
    "DAILY_MERGED": "일별 병합본",
}


def _print_market_collection(
    summary: MarketCollectionSummary,
    display_name: str,
    data_dir: Path,
    start: date,
    end: date,
) -> None:
    """수집 결과 테이블 + 저장 경로 + 부분 수집 경고 출력 (명세 A3 §6)."""
    table = Table(
        title=(
            f"시장 데이터 수집 결과 — {display_name} ({summary.stock_code}) / "
            f"지수 {summary.index_code} / 요청 {start}~{end}"
        )
    )
    for column in ("데이터셋", "결과", "행수", "기간"):
        table.add_column(column)
    for outcome in summary.outcomes:
        period = (
            f"{outcome.date_min}~{outcome.date_max}"
            if outcome.date_min is not None and outcome.date_max is not None
            else "-"
        )
        table.add_row(
            f"{_DATASET_LABELS.get(outcome.dataset, outcome.dataset)}({outcome.dataset})",
            outcome.result,
            str(outcome.row_count),
            period,
        )
    console.print(table)
    console.print(f"raw 저장 경로: {market_raw_stock_dir(data_dir, summary.stock_code)}")
    console.print(f"지수 raw 저장 경로: {market_raw_index_dir(data_dir, summary.index_code)}")
    console.print(
        f"normalized 저장 경로: {market_normalized_stock_dir(data_dir, summary.stock_code)}"
    )
    console.print(f"거래일 캘린더 경로: {market_calendar_path(data_dir)}")
    if summary.has_skipped_no_auth():
        console.print(
            "[yellow]투자자 수급·지수는 KRX 로그인 필요 — .env에 KRX_ID/KRX_PW 설정 후 "
            "재실행하면 가격 캐시는 유지된 채 나머지만 수집된다.[/yellow]"
        )


@app.command()
def research(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    as_of_date: Annotated[str, typer.Option("--as-of-date", help="분석 기준일 (YYYY-MM-DD)")],
    lookback_years: Annotated[int, typer.Option("--lookback-years", help="분석 대상 연수")] = 5,
) -> None:
    """새 run을 등록하고 AI 분석 후보·가설 후보를 생성한다 (README §26.5, 명세 W3b §2.3).

    v2 HITL 흐름의 시작점이다 — 기존 run을 찾지 않고 항상 새 run을 만든 뒤
    ``generate-candidates`` 로직(Evidence 빌드 + CandidateAnalysis·
    HypothesisCandidate 생성)을 이어서 실행한다. 이후 단계는 사용자가 작성하는
    ``create-analyst-view``다. 종료 코드는 ``generate-candidates``와 같다
    (0 성공 / 1 검증·데이터 / 3 설정·인증 / 4 게이트).
    """
    as_of = _parse_iso_date_option(as_of_date, "--as-of-date")
    if as_of is None:
        raise typer.BadParameter("--as-of-date는 필수입니다.")

    settings = get_settings()
    run_id = _create_run_impl(company, as_of, settings)
    run_generate_candidates(settings, run_id, lookback_years=lookback_years)
    console.print(f"다음 단계: create-analyst-view --run-id {run_id}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
