"""README §3 입력 모델 테스트."""

from datetime import date

import pytest
from pydantic import ValidationError

from research_backtest.core.models import ResearchOptions, ResearchRequest


def test_research_request_parses_iso_date() -> None:
    req = ResearchRequest.model_validate({"company": "SK하이닉스", "as_of_date": "2025-12-31"})
    assert req.as_of_date == date(2025, 12, 31)


def test_research_request_strips_company() -> None:
    req = ResearchRequest(company="  000660  ", as_of_date=date(2025, 12, 31))
    assert req.company == "000660"


def test_research_request_rejects_blank_company() -> None:
    with pytest.raises(ValidationError):
        ResearchRequest(company="   ", as_of_date=date(2025, 12, 31))


def test_research_options_defaults() -> None:
    opts = ResearchOptions()
    assert opts.market == "KR"
    assert opts.lookback_years == 5
    assert opts.financial_statement_scope == "AUTO"
    assert opts.investment_horizon == "medium_term"
    assert opts.benchmark == "KOSPI"
    assert opts.strategy_style == "long_cash"
    assert opts.include_investor_flow is True


def test_research_options_rejects_invalid_scope() -> None:
    with pytest.raises(ValidationError):
        ResearchOptions.model_validate({"financial_statement_scope": "BOTH"})
