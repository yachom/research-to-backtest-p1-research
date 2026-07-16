"""정기보고서 검색·분류 (README §6.2, §19.2).

- ``last_reprt_at="N"``: 정정 전 원본 공시도 포함한다 — Point-in-Time 재현에 필요.
- ``pblntf_ty="A"``: 정기공시.
- 분기보고서의 Q1/Q3 구분은 회계기간 말월(3월→Q1, 9월→Q3)로 판단한다.
  **12월 결산 가정**이며 비12월 결산 기업의 분기 구분은 지원하지 않는다
  (후순위 — MVP 기업(SK하이닉스)은 12월 결산).
"""

import calendar
import re
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.exceptions import DartApiError, DataValidationError

LIST_API_PATH = "list.json"
PAGE_COUNT = "100"

_PREFIX_RE = re.compile(r"^\s*(?:\[[^\]]*\]\s*)+")
_BRACKET_RE = re.compile(r"\[([^\]]*)\]")
_PERIOD_RE = re.compile(r"\((\d{4})\.(\d{1,2})\)")


def _split_report_name(report_nm: str) -> tuple[list[str], str]:
    """report_nm을 (프리픽스 목록, 본문)으로 분해한다 — 예: "[기재정정]사업보고서 (2023.12)"."""
    matched = _PREFIX_RE.match(report_nm)
    if not matched:
        return [], report_nm.strip()
    prefixes = _BRACKET_RE.findall(matched.group(0))
    return prefixes, report_nm[matched.end() :].strip()


def _fiscal_period_end(report_nm: str) -> date | None:
    """ "(2024.12)" 표기를 해당 월 말일 date로 변환한다 — 예: 2024-12-31."""
    matched = _PERIOD_RE.search(report_nm)
    if not matched:
        return None
    year, month = int(matched.group(1)), int(matched.group(2))
    if not 1 <= month <= 12:
        return None
    return date(year, month, calendar.monthrange(year, month)[1])


def classify_report_type(body: str, fiscal_period_end: date | None) -> PeriodicReportType | None:
    """프리픽스를 제거한 report_nm 본문으로 정기보고서 유형을 분류한다 (README §6.2).

    - 사업보고서→ANNUAL, 반기보고서→HALF, 분기보고서→Q1/Q3
    - 분기 구분은 fiscal_period_end의 월(3월→Q1, 9월→Q3)로 판단 — 12월 결산 가정.
      회계기간 표기가 없거나 3·9월이 아니면 None(분류 불가)으로 둔다.
    """
    if body.startswith("사업보고서"):
        return PeriodicReportType.ANNUAL
    if body.startswith("반기보고서"):
        return PeriodicReportType.HALF
    if body.startswith("분기보고서") and fiscal_period_end is not None:
        if fiscal_period_end.month == 3:
            return PeriodicReportType.Q1
        if fiscal_period_end.month == 9:
            return PeriodicReportType.Q3
    return None


def parse_filing(row: Mapping[str, Any]) -> DartFiling:
    """공시검색 응답 1행을 DartFiling으로 변환한다 (README §6.2, 명세 §3.1~3.2).

    ``[기재정정]``·``[첨부정정]`` 등 "정정"이 포함된 프리픽스가 있으면
    is_correction=True로 표시하고 프리픽스 문자열을 correction_kind에 보존한다
    (정정공시 버전 그래프 완성은 B4 — 여기서는 식별·표시까지만).
    """
    report_nm = str(row.get("report_nm", "")).strip()
    prefixes, body = _split_report_name(report_nm)
    correction_prefixes = [p for p in prefixes if "정정" in p]
    fiscal_end = _fiscal_period_end(report_nm)
    return DartFiling(
        corp_code=str(row.get("corp_code", "")),
        corp_name=str(row.get("corp_name", "")),
        stock_code=str(row.get("stock_code") or "").strip() or None,
        report_nm=report_nm,
        rcept_no=str(row.get("rcept_no", "")),
        flr_nm=str(row.get("flr_nm", "")),
        rcept_dt=_parse_yyyymmdd(str(row.get("rcept_dt", ""))),
        rm=str(row.get("rm") or "").strip() or None,
        report_type=classify_report_type(body, fiscal_end),
        fiscal_period_end=fiscal_end,
        is_correction=bool(correction_prefixes),
        correction_kind=correction_prefixes[0] if correction_prefixes else None,
    )


def find_periodic_filings(
    client: DartClient,
    corp_code: str,
    *,
    as_of_date: date,
    lookback_years: int = 5,
) -> list[DartFiling]:
    """정기공시 목록을 조회한다 (README §6.2, §19.2).

    - 기간: ``as_of_date - lookback_years년`` ~ ``as_of_date``
    - ``total_page``까지 순회해 병합, status 013(조회 데이터 없음)은 빈 리스트
    - PIT 방어 필터: end_de가 보장하더라도 ``rcept_dt > as_of_date`` 항목을
      명시적으로 제거한다 (README §19.2 "분석 기준일 이후 공시 제외")
    - 정렬: rcept_dt 내림차순
    """
    base_params = {
        "corp_code": corp_code,
        "bgn_de": _format_yyyymmdd(_years_before(as_of_date, lookback_years)),
        "end_de": _format_yyyymmdd(as_of_date),
        "last_reprt_at": "N",
        "pblntf_ty": "A",
        "page_count": PAGE_COUNT,
    }
    rows: list[dict[str, Any]] = []
    page_no = 1
    while True:
        try:
            payload = client.get_json(LIST_API_PATH, page_no=str(page_no), **base_params)
        except DartApiError as err:
            if err.is_no_data:
                break
            raise
        page_rows: Any = payload.get("list") or []
        if isinstance(page_rows, list):
            rows.extend(row for row in page_rows if isinstance(row, dict))
        total_page = int(payload.get("total_page") or 1)
        if page_no >= total_page:
            break
        page_no += 1

    filings = [parse_filing(row) for row in rows]
    filings = [f for f in filings if f.rcept_dt <= as_of_date]
    filings.sort(key=lambda f: (f.rcept_dt, f.rcept_no), reverse=True)
    return filings


def latest_filing(
    filings: Sequence[DartFiling], report_type: PeriodicReportType
) -> DartFiling | None:
    """해당 유형의 최신(접수일 기준) 공시 1건 또는 None."""
    matching = [f for f in filings if f.report_type == report_type]
    return max(matching, key=lambda f: (f.rcept_dt, f.rcept_no), default=None)


def _parse_yyyymmdd(value: str) -> date:
    stripped = value.strip()
    if len(stripped) != 8 or not stripped.isdigit():
        raise DataValidationError(f"rcept_dt 형식 오류(YYYYMMDD 아님): {value!r}")
    return date(int(stripped[:4]), int(stripped[4:6]), int(stripped[6:8]))


def _format_yyyymmdd(value: date) -> str:
    return value.strftime("%Y%m%d")


def _years_before(value: date, years: int) -> date:
    """value에서 years년 전 날짜 (2/29는 2/28로 보정)."""
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)
