"""시스템 전체 입력 모델 (README §3)."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class ResearchRequest(BaseModel):
    """리서치 실행 필수 입력 (README §3.1)."""

    company: str
    as_of_date: date

    @field_validator("company")
    @classmethod
    def _strip_company(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("company는 빈 문자열일 수 없습니다.")
        return v


class ResearchOptions(BaseModel):
    """리서치 실행 선택 입력 (README §3.2)."""

    market: Literal["KR"] = "KR"
    lookback_years: int = Field(default=5, ge=1, le=10)
    financial_statement_scope: Literal["AUTO", "CFS", "OFS"] = "AUTO"
    investment_horizon: Literal["short_term", "medium_term", "long_term"] = "medium_term"
    benchmark: str = "KOSPI"
    strategy_style: Literal["long_only", "long_cash"] = "long_cash"
    analysis_focus: list[str] = Field(default_factory=list)
    # README §3.2 기본값은 True이나 뉴스는 MVP 범위(§32) 밖 → 기본 비활성 (MILESTONES D4)
    include_news: bool = False
    include_investor_flow: bool = True
    include_industry_data: bool = True


class DartCorporation(BaseModel):
    """DART 고유번호 파일의 기업 항목 (README §6.1)."""

    corp_code: str
    corp_name: str
    corp_eng_name: str | None = None
    stock_code: str | None = None
    modify_date: str
