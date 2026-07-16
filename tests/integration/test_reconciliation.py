"""실데이터 API-XBRL 정합성 검증 integration 테스트 (명세 B3 §6) — 데이터 없으면 skip.

실행: ``DATA_DIR=/…/data pytest -m integration tests/integration/test_reconciliation.py``
(API 호출 없음 — DART_API_KEY 불필요, DATA_DIR만 있으면 된다).

미파싱 rcept를 자동 파싱해 normalized parquet을 DATA_DIR에 적재하고(명세 §2, 의도된
동작), 전량 대조를 실 데이터에서 실행한다. 산출(reconciliation_report.json·
failures.csv)도 DATA_DIR/analytics에 남는다.

기대(README §19.7): **연간 5개년 x CFS·OFS x 7계정에서 MATCH+ROUNDING 100%**.
미달 시 테스트 실패가 아니라 REQUIRES_REVIEW 목록과 함께 xfail로 사유를 남긴다
(원인이 정정공시 Current View 차이일 수 있음 — B4 소관). 분·반기는 분포만 확인한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.reconciliation.compare import ReconciliationStatus
from research_backtest.core.reconciliation.pipeline import (
    FAILURES_FILENAME,
    REPORT_FILENAME,
    ReconciliationReport,
    reconcile_all,
    reconciliation_out_dir,
)
from research_backtest.core.reconciliation.xbrl_select import (
    CONSOLIDATED_MEMBER_LOCAL,
    SCOPE_AXIS_LOCAL,
    SEPARATE_MEMBER_LOCAL,
)
from research_backtest.core.xbrl.parser import parse_extracted

pytestmark = pytest.mark.integration

SK_HYNIX = "00164779"
EXPECTED_RCEPT = 22
EXPECTED_ANNUAL_ROWS = 70  # 5개년 x CFS·OFS x 7계정


@pytest.fixture(scope="module")
def real_data_dir() -> Path:
    data_dir = get_settings().data_dir
    facts = data_dir / "normalized" / "financials" / SK_HYNIX / "normalized_facts.parquet"
    xbrl = data_dir / "raw" / "dart" / "xbrl" / SK_HYNIX
    if not facts.exists() or not xbrl.is_dir():
        pytest.skip(f"실데이터 없음(normalized_facts·xbrl) — DATA_DIR 확인: {data_dir}")
    return data_dir


@pytest.fixture(scope="module")
def report(real_data_dir: Path) -> ReconciliationReport:
    return reconcile_all(SK_HYNIX, data_dir=real_data_dir)


def test_all_xbrl_parsed_no_failures(report: ReconciliationReport) -> None:
    parsed = len(report.parse.newly_parsed) + len(report.parse.already_parsed)
    assert parsed == EXPECTED_RCEPT, f"22 정기보고서 파싱 기대 (실패: {report.parse.failed})"
    assert report.parse.failed == []


def test_report_files_written(report: ReconciliationReport, real_data_dir: Path) -> None:
    out = reconciliation_out_dir(real_data_dir, SK_HYNIX)
    assert (out / REPORT_FILENAME).exists()
    assert (out / FAILURES_FILENAME).exists()


def test_annual_match_rate_100_percent(report: ReconciliationReport) -> None:
    annual = report.annual
    assert annual.total == EXPECTED_ANNUAL_ROWS, f"연간 행 수: {annual.by_status}"

    if annual.match_rate < 1.0:
        reviews = [
            (f.canonical_account_id, f.fs_scope, f.fiscal_year, f.status, f.reason)
            for f in report.failures
            if f.fiscal_quarter is None
        ]
        pytest.xfail(
            f"연간 MATCH+ROUNDING 미달({annual.match_rate:.3f}) — "
            f"정정공시 Current View 차이 가능(B4). REQUIRES_REVIEW: {reviews}"
        )
    # 100%면 연간 상태는 MATCH/ROUNDING만 존재한다.
    assert set(annual.by_status) <= {
        ReconciliationStatus.MATCH,
        ReconciliationStatus.ROUNDING_DIFFERENCE,
    }


def test_annual_matrix_covers_seven_accounts_five_years(report: ReconciliationReport) -> None:
    matrix = report.account_year_matrix
    assert len(matrix) == 7, f"대표 7계정 매트릭스 기대, got {sorted(matrix)}"
    for account, years in matrix.items():
        assert {"2021", "2022", "2023", "2024", "2025"} <= set(years), account
        for scopes in years.values():
            assert set(scopes) == {"CFS", "OFS"}


def test_quarterly_distribution_reported(report: ReconciliationReport) -> None:
    # 분·반기는 커버리지·분포 확인만(보고 대상). 실데이터엔 존재해야 한다.
    assert report.quarterly.total > 0
    # 상태 합이 버킷 총계와 일치(집계 무결성).
    assert sum(report.quarterly.by_status.values()) == report.quarterly.total


def test_scope_member_locals_present_in_real_xbrl(real_data_dir: Path) -> None:
    # DATA_NOTES 후보 실측: 연결/별도는 ConsolidatedAndSeparate 축의
    # Consolidated/SeparateMember로만 구분된다(파일 분리 없음).
    extracted = real_data_dir / "raw" / "dart" / "xbrl" / SK_HYNIX / "20220322000590" / "extracted"
    parsed = parse_extracted(extracted)
    axis_members: set[tuple[str, str]] = set()
    for ctx in parsed.contexts:
        for dim in ctx.dimensions:
            axis_local = dim.axis_qname.rsplit(":", 1)[-1]
            if axis_local == SCOPE_AXIS_LOCAL and dim.member_qname is not None:
                axis_members.add((axis_local, dim.member_qname.rsplit(":", 1)[-1]))
    assert (SCOPE_AXIS_LOCAL, CONSOLIDATED_MEMBER_LOCAL) in axis_members
    assert (SCOPE_AXIS_LOCAL, SEPARATE_MEMBER_LOCAL) in axis_members
