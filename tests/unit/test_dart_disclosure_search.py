"""disclosure_search 단위 테스트 — 분류·PIT 필터·페이지네이션 (README §6.2, §19.2, 명세 A1 §3)."""

from collections.abc import Callable
from datetime import date
from pathlib import Path

import httpx

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.disclosure_search import (
    find_periodic_filings,
    latest_filing,
    parse_filing,
)

ClientFactory = Callable[..., DartClient]

AS_OF = date(2025, 5, 20)


def _row(
    report_nm: str, rcept_dt: str = "20250320", rcept_no: str = "20250320000001"
) -> dict[str, str]:
    return {
        "corp_code": "00164779",
        "corp_name": "SK하이닉스",
        "stock_code": "000660",
        "report_nm": report_nm,
        "rcept_no": rcept_no,
        "flr_nm": "SK하이닉스",
        "rcept_dt": rcept_dt,
        "rm": "",
    }


# --- report_nm 분류 (명세 §3.2) ---------------------------------------------


def test_parse_annual_report() -> None:
    filing = parse_filing(_row("사업보고서 (2024.12)"))
    assert filing.report_type == PeriodicReportType.ANNUAL
    assert filing.fiscal_period_end == date(2024, 12, 31)  # 해당 월 말일
    assert filing.is_correction is False
    assert filing.correction_kind is None
    assert filing.rcept_dt == date(2025, 3, 20)
    assert filing.stock_code == "000660"
    assert filing.rm is None  # 빈 문자열 → None


def test_parse_half_report() -> None:
    filing = parse_filing(_row("반기보고서 (2024.06)"))
    assert filing.report_type == PeriodicReportType.HALF
    assert filing.fiscal_period_end == date(2024, 6, 30)


def test_parse_quarter_reports_split_by_fiscal_month() -> None:
    q1 = parse_filing(_row("분기보고서 (2025.03)"))
    q3 = parse_filing(_row("분기보고서 (2024.09)"))
    assert q1.report_type == PeriodicReportType.Q1
    assert q1.fiscal_period_end == date(2025, 3, 31)
    assert q3.report_type == PeriodicReportType.Q3
    assert q3.fiscal_period_end == date(2024, 9, 30)


def test_parse_quarter_report_with_non_standard_month_is_unclassified() -> None:
    # 12월 결산 가정 — 3·9월이 아닌 분기 말월은 분류하지 않는다 (명세 §3.2)
    filing = parse_filing(_row("분기보고서 (2024.02)"))
    assert filing.report_type is None
    assert filing.fiscal_period_end == date(2024, 2, 29)  # 윤년 말일


def test_parse_correction_prefix() -> None:
    filing = parse_filing(_row("[기재정정]사업보고서 (2023.12)"))
    assert filing.report_type == PeriodicReportType.ANNUAL
    assert filing.is_correction is True
    assert filing.correction_kind == "기재정정"


def test_parse_attachment_correction_prefix() -> None:
    filing = parse_filing(_row("[첨부정정] 반기보고서 (2023.06)"))
    assert filing.report_type == PeriodicReportType.HALF
    assert filing.is_correction is True
    assert filing.correction_kind == "첨부정정"


def test_parse_non_correction_prefix_is_stripped_for_classification() -> None:
    filing = parse_filing(_row("[첨부추가]사업보고서 (2023.12)"))
    assert filing.report_type == PeriodicReportType.ANNUAL
    assert filing.is_correction is False
    assert filing.correction_kind is None


def test_parse_non_periodic_report_has_no_type() -> None:
    filing = parse_filing(_row("주요사항보고서(유상증자결정)"))
    assert filing.report_type is None
    assert filing.fiscal_period_end is None


# --- 조회·페이지네이션·PIT 필터 (README §6.2, §19.2) -------------------------


def test_find_periodic_filings_merges_pages_and_applies_pit_filter(
    make_dart_client: ClientFactory, fixtures_dir: Path
) -> None:
    pages = {
        "1": (fixtures_dir / "list_page1.json").read_text(encoding="utf-8"),
        "2": (fixtures_dir / "list_page2.json").read_text(encoding="utf-8"),
    }
    seen_params: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(request.url.params)
        return httpx.Response(200, text=pages[request.url.params["page_no"]])

    with make_dart_client(handler) as client:
        filings = find_periodic_filings(client, "00164779", as_of_date=AS_OF, lookback_years=2)

    # total_page까지 순회
    assert [params["page_no"] for params in seen_params] == ["1", "2"]
    first = seen_params[0]
    assert first["corp_code"] == "00164779"
    assert first["bgn_de"] == "20230520"  # as_of - 2년
    assert first["end_de"] == "20250520"
    assert first["last_reprt_at"] == "N"  # 원본 공시 포함 (PIT 재현)
    assert first["pblntf_ty"] == "A"
    assert first["page_count"] == "100"

    # 6건 중 rcept_dt(2025-08-14) > as_of 1건 제거, rcept_dt 내림차순 정렬
    assert [f.rcept_no for f in filings] == [
        "20250515000100",
        "20250320000200",
        "20241114000500",
        "20240814000400",
        "20240710000300",
    ]
    assert all(f.rcept_dt <= AS_OF for f in filings)

    correction = filings[-1]
    assert correction.is_correction is True
    assert correction.correction_kind == "기재정정"
    assert correction.report_type == PeriodicReportType.ANNUAL


def test_find_periodic_filings_returns_empty_list_on_no_data(
    make_dart_client: ClientFactory,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

    with make_dart_client(handler) as client:
        assert find_periodic_filings(client, "00164779", as_of_date=AS_OF) == []


def test_latest_filing_picks_most_recent_of_type(
    make_dart_client: ClientFactory, fixtures_dir: Path
) -> None:
    pages = {
        "1": (fixtures_dir / "list_page1.json").read_text(encoding="utf-8"),
        "2": (fixtures_dir / "list_page2.json").read_text(encoding="utf-8"),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[request.url.params["page_no"]])

    with make_dart_client(handler) as client:
        filings = find_periodic_filings(client, "00164779", as_of_date=AS_OF, lookback_years=2)

    annual = latest_filing(filings, PeriodicReportType.ANNUAL)
    assert annual is not None
    assert annual.rcept_no == "20250320000200"  # 정정본(2024-07-10)보다 최신 접수일

    half = latest_filing(filings, PeriodicReportType.HALF)
    assert half is not None
    assert half.rcept_no == "20240814000400"  # 2025-08-14 건은 PIT 필터로 제외됨

    assert latest_filing([], PeriodicReportType.Q1) is None
