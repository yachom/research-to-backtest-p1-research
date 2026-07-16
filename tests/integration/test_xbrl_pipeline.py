"""XBRL 수집·파싱 파이프라인 integration 테스트 (명세 §3) — DART_API_KEY 없으면 skip.

실 다운로드는 ``settings.data_dir``(integration 실행 시 메인 data/로 지정)에 저장한다 —
이후 마일스톤(B3)의 실데이터가 된다(명세 §3, §4). 파싱 산출물(parquet) 검증만
tmp_path를 쓴다. 실행: 레포 루트에서
``DATA_DIR=<메인 data> pytest -m integration tests/integration/test_xbrl_pipeline.py``.

명세 이탈 기록: 명세 §3은 "자산총계(Assets, dimension 없는 context) numeric > 0"을
요구하나, SK하이닉스 실 XBRL은 기본 재무제표에도 ConsolidatedAndSeparate 축을
명시 차원으로 부여해 **차원 없는 Assets context가 없다**(연결 자산총계는
ConsolidatedMember 차원 아래). 따라서 연결(ConsolidatedMember) Assets numeric > 0으로
검증한다 — 아래 test_consolidated_assets_positive 참조(DATA_NOTES 후보).
"""

from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.constants import PERIODIC_REPORT_TO_REPRT_CODE, PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.disclosure_search import find_periodic_filings, latest_filing
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dart.xbrl_downloader import (
    CHECKSUM_FILENAME,
    EXTRACTED_DIRNAME,
    MANIFEST_FILENAME,
    XbrlManifest,
    download_xbrl_filing,
    xbrl_filing_dir,
)
from research_backtest.core.xbrl.models import ParsedXbrl
from research_backtest.core.xbrl.parser import parse_extracted
from research_backtest.core.xbrl.store import (
    CONTEXTS_FILENAME,
    FACTS_FILENAME,
    load_xbrl_table,
    store_parsed_xbrl,
)

pytestmark = pytest.mark.integration

SK_HYNIX_CORP_CODE = "00164779"  # README §8.2 예시
IFRS_FULL_TOKEN = "ifrs-full"
CONSOLIDATED_MEMBER = "ifrs-full:ConsolidatedMember"


@pytest.fixture(scope="module")
def dart_client() -> Iterator[DartClient]:
    settings = get_settings()
    if not settings.dart_api_key:
        pytest.skip("DART_API_KEY 미설정 — integration 테스트 생략")
    with DartClient(settings.dart_api_key) as client:
        yield client


@pytest.fixture(scope="module")
def data_dir() -> Path:
    return get_settings().data_dir


@pytest.fixture(scope="module")
def latest_annual(dart_client: DartClient) -> DartFiling:
    """SK하이닉스 최근 사업보고서 1건을 실 공시검색으로 확보한다 (A1 실호출)."""
    filings = find_periodic_filings(
        dart_client, SK_HYNIX_CORP_CODE, as_of_date=date.today(), lookback_years=2
    )
    annual = latest_filing(filings, PeriodicReportType.ANNUAL)
    if annual is None:
        pytest.skip("최근 사업보고서를 찾지 못했습니다")
    return annual


@pytest.fixture(scope="module")
def downloaded(dart_client: DartClient, latest_annual: DartFiling, data_dir: Path) -> DartFiling:
    """최근 사업보고서 XBRL을 메인 data/에 다운로드한다 (재실행 시 CACHED)."""
    reprt = PERIODIC_REPORT_TO_REPRT_CODE[PeriodicReportType.ANNUAL]
    outcome = download_xbrl_filing(dart_client, latest_annual, reprt_code=reprt, data_dir=data_dir)
    assert outcome.result in {"FETCHED", "CACHED"}
    return latest_annual


@pytest.fixture(scope="module")
def parsed(downloaded: DartFiling, data_dir: Path) -> ParsedXbrl:
    extracted = (
        xbrl_filing_dir(data_dir, downloaded.corp_code, downloaded.rcept_no) / EXTRACTED_DIRNAME
    )
    return parse_extracted(extracted)


# --- 다운로드 (명세 §3, README §19.4) ----------------------------------------


def test_download_creates_manifest_and_checksum(downloaded: DartFiling, data_dir: Path) -> None:
    fdir = xbrl_filing_dir(data_dir, downloaded.corp_code, downloaded.rcept_no)
    manifest = XbrlManifest.model_validate_json((fdir / MANIFEST_FILENAME).read_text("utf-8"))
    assert manifest.rcept_no == downloaded.rcept_no
    assert manifest.reprt_code == "11011"
    assert manifest.source == "OPEN_DART_XBRL"
    assert manifest.sha256 is not None
    # checksum 파일 == manifest.sha256
    assert (fdir / CHECKSUM_FILENAME).read_text("utf-8").strip() == manifest.sha256
    assert (fdir / EXTRACTED_DIRNAME).is_dir()


def test_second_run_is_cached(
    dart_client: DartClient, latest_annual: DartFiling, data_dir: Path, downloaded: DartFiling
) -> None:
    reprt = PERIODIC_REPORT_TO_REPRT_CODE[PeriodicReportType.ANNUAL]
    fdir = xbrl_filing_dir(data_dir, latest_annual.corp_code, latest_annual.rcept_no)
    zip_mtime_before = (fdir / "response.zip").stat().st_mtime_ns
    outcome = download_xbrl_filing(dart_client, latest_annual, reprt_code=reprt, data_dir=data_dir)
    assert outcome.result == "CACHED"
    # 재다운로드 없음 — response.zip mtime 불변
    assert (fdir / "response.zip").stat().st_mtime_ns == zip_mtime_before


# --- 파싱 (명세 §3, README §19.5) --------------------------------------------


def test_parse_has_over_1000_facts(parsed: ParsedXbrl) -> None:
    assert len(parsed.facts) > 1000
    assert len(parsed.contexts) > 0
    assert len(parsed.units) > 0


def test_ifrs_full_namespace_present(parsed: ParsedXbrl) -> None:
    assert any(IFRS_FULL_TOKEN in f.concept_namespace for f in parsed.facts)
    # 기업 확장계정(entity extension) 네임스페이스도 존재 (README §6.5 목적 2)
    assert any(f"entity{SK_HYNIX_CORP_CODE}" in f.concept_namespace for f in parsed.facts)


def test_entity_identifier_contains_corp_code(parsed: ParsedXbrl) -> None:
    identifiers = {c.entity_identifier for c in parsed.contexts}
    assert SK_HYNIX_CORP_CODE in identifiers


def test_instant_and_duration_contexts_coexist(parsed: ParsedXbrl) -> None:
    period_types = {c.period_type for c in parsed.contexts}
    assert "instant" in period_types
    assert "duration" in period_types


def test_consolidated_assets_positive(parsed: ParsedXbrl) -> None:
    # 명세 §3의 "Assets numeric > 0"을 실데이터에 맞게 연결(ConsolidatedMember)로 검증.
    # (실 XBRL에는 차원 없는 Assets context가 없다 — 모듈 docstring의 명세 이탈 기록)
    ctx_by_id = {c.context_id: c for c in parsed.contexts}
    consolidated_assets = [
        f
        for f in parsed.facts
        if f.concept_local_name == "Assets"
        and f.numeric_value is not None
        and any(d.member_qname == CONSOLIDATED_MEMBER for d in ctx_by_id[f.context_id].dimensions)
        and ctx_by_id[f.context_id].period_type == "instant"
    ]
    assert consolidated_assets, "연결 자산총계(ConsolidatedMember, instant) fact가 없습니다"
    assert max(float(f.numeric_value) for f in consolidated_assets) > 0  # type: ignore[arg-type]


# --- 저장·결정성 (명세 §2.3, README M4) — parquet은 tmp_path 검증 --------------


def test_store_round_trip_and_determinism(parsed: ParsedXbrl, tmp_path: Path) -> None:
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    store_parsed_xbrl(parsed, out1)
    store_parsed_xbrl(parsed, out2)

    facts1 = load_xbrl_table(out1, FACTS_FILENAME)
    assert len(facts1) == len(parsed.facts)
    # 동일 입력 → 동일 parquet bytes(결정성, README M4)
    assert (out1 / FACTS_FILENAME).read_bytes() == (out2 / FACTS_FILENAME).read_bytes()
    assert (out1 / CONTEXTS_FILENAME).read_bytes() == (out2 / CONTEXTS_FILENAME).read_bytes()

    contexts = load_xbrl_table(out1, CONTEXTS_FILENAME)
    assert contexts["dimension_count"].max() >= 1  # 차원 있는 context 존재
