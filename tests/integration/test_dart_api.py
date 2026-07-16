"""DART 실 API integration 테스트 (명세 A1 §6) — DART_API_KEY 없으면 skip.

실행: 레포 루트에서 ``pytest -m integration`` (.env의 DART_API_KEY 사용).
"""

from collections.abc import Iterator
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import CorpCodeRegistry, load_corp_code_registry
from research_backtest.core.dart.disclosure_search import find_periodic_filings, latest_filing

pytestmark = pytest.mark.integration

KST = ZoneInfo("Asia/Seoul")
SK_HYNIX_CORP_CODE = "00164779"  # README §8.2 예시
SK_HYNIX_STOCK_CODE = "000660"


@pytest.fixture(scope="module")
def dart_client() -> Iterator[DartClient]:
    settings = get_settings()
    if not settings.dart_api_key:
        pytest.skip("DART_API_KEY 미설정 — integration 테스트 생략")
    with DartClient(settings.dart_api_key) as client:
        yield client


@pytest.fixture(scope="module")
def registry(dart_client: DartClient, tmp_path_factory: pytest.TempPathFactory) -> CorpCodeRegistry:
    """고유번호 파일 실다운로드 — 임시 캐시 디렉토리 사용(로컬 캐시 오염 방지)."""
    cache_dir = tmp_path_factory.mktemp("dart_corp_code_cache")
    return load_corp_code_registry(dart_client, cache_dir, refresh_days=7)


def test_resolve_sk_hynix_by_name(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("SK하이닉스")
    assert result.matched is not None
    assert result.matched.corp_code == SK_HYNIX_CORP_CODE
    assert result.matched.stock_code == SK_HYNIX_STOCK_CODE


def test_resolve_sk_hynix_by_stock_code(registry: CorpCodeRegistry) -> None:
    result = registry.resolve(SK_HYNIX_STOCK_CODE)
    assert result.method == "STOCK_CODE"
    assert result.matched is not None
    assert result.matched.corp_code == SK_HYNIX_CORP_CODE


def test_periodic_filings_include_recent_annual_report(dart_client: DartClient) -> None:
    as_of = datetime.now(KST).date()
    filings = find_periodic_filings(
        dart_client, SK_HYNIX_CORP_CODE, as_of_date=as_of, lookback_years=2
    )
    assert filings, "최근 2년 정기보고서가 1건 이상 있어야 한다"
    assert all(f.rcept_dt <= as_of for f in filings)

    annual = latest_filing(filings, PeriodicReportType.ANNUAL)
    assert annual is not None, "최근 사업보고서가 존재해야 한다"
    assert annual.report_type == PeriodicReportType.ANNUAL
    assert annual.rcept_no
