"""DART 식별 계층 도메인 모델 (README §6.1~6.2, §19.1~19.2).

날짜 필드는 전부 :class:`datetime.date`로 다루고 API 경계(YYYYMMDD 문자열)
변환은 corp_code·disclosure_search 모듈이 담당한다.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.models import DartCorporation

ResolveMethod = Literal["STOCK_CODE", "EXACT_NAME", "SUBSTRING", "NOT_FOUND", "AMBIGUOUS"]


class ResolveResult(BaseModel):
    """기업 식별 결과 (README §19.1).

    matched가 None이면 method는 NOT_FOUND 또는 AMBIGUOUS이며, AMBIGUOUS일 때
    candidates에 상장 우선으로 정렬된 후보(최대 10)를 담는다.
    """

    matched: DartCorporation | None
    candidates: list[DartCorporation]
    method: ResolveMethod


class DartFiling(BaseModel):
    """공시검색 API(list.json)의 정기보고서 1건 (README §6.2, §19.2).

    파생 필드(report_type·fiscal_period_end·is_correction·correction_kind)는
    report_nm 파싱 결과이며 disclosure_search.parse_filing이 채운다.
    """

    corp_code: str
    corp_name: str
    stock_code: str | None
    report_nm: str
    rcept_no: str
    flr_nm: str
    rcept_dt: date  # API의 YYYYMMDD를 date로 변환
    rm: str | None

    # 파생 필드 (report_nm 파싱)
    report_type: PeriodicReportType | None = None  # 정기보고서가 아니면 None
    fiscal_period_end: date | None = None  # "(2024.12)" → 2024-12-31 (해당 월 말일)
    is_correction: bool = False  # "[…정정…]" 프리픽스 여부
    correction_kind: str | None = None  # 예: "기재정정", "첨부정정"
