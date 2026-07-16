"""pipeline 단위 테스트 — 집계·리포트 파일·파싱 실패 처리 (명세 §2·§5·§6).

오프라인: A4 normalized_facts는 소형 합성 parquet, XBRL은 미리 저장한 parquet
(R1, 정상)과 instance가 없는 extracted(RBAD, 파싱 실패)로 두 경로를 만든다.
registry는 실 ``configs/account_registry.yaml``을 소비만 한다(레포 루트 실행 전제).
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from research_backtest.core.constants import FsDiv
from research_backtest.core.reconciliation.compare import ReconciliationStatus
from research_backtest.core.reconciliation.pipeline import (
    FAILURES_FILENAME,
    REPORT_FILENAME,
    reconcile_all,
    reconciliation_out_dir,
)
from research_backtest.core.xbrl.models import ParsedXbrl
from research_backtest.core.xbrl.store import store_parsed_xbrl, xbrl_normalized_dir

from .conftest import (
    IFRS_NS,
    SCOPE_AXIS_QNAME,
    make_context,
    make_fact,
    make_units,
    reported_row,
    write_normalized_facts,
)

CORP = "00000000"
CONS = "ifrs-full:ConsolidatedMember"

V_ASSETS = 96386474000000
V_REVENUE = 42997792000000
V_LIAB = 34195416000000
V_NET = 9616188000000


def _setup(tmp: Path) -> None:
    """R1(정상 parquet) + RBAD(파싱 실패) + A4 normalized_facts 합성."""
    # A4 normalized_facts (기준 목록): 4 계정 x 2023 연간, CFS.
    rows = [
        reported_row(
            canonical_id="total_assets",
            sj_div="BS",
            fiscal_year=2023,
            period_end=date(2023, 12, 31),
            value=V_ASSETS,
            rcept_no="R1",
        ),
        reported_row(
            canonical_id="revenue",
            sj_div="CIS",
            fiscal_year=2023,
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            value=V_REVENUE,
            rcept_no="R1",
        ),
        reported_row(
            canonical_id="total_liabilities",
            sj_div="BS",
            fiscal_year=2023,
            period_end=date(2023, 12, 31),
            value=V_LIAB,
            rcept_no="R1",
        ),
        reported_row(
            canonical_id="net_income",
            sj_div="CIS",
            fiscal_year=2023,
            period_start=date(2023, 1, 1),
            period_end=date(2023, 12, 31),
            value=V_NET,
            rcept_no="RBAD",
        ),
    ]
    write_normalized_facts(
        rows, tmp / "normalized" / "financials" / CORP / "normalized_facts.parquet"
    )

    # R1 XBRL: Assets(=MATCH), Revenue(=MATCH), Liabilities(ROUNDING: -1e6) — 연결 단독 context.
    contexts = [
        make_context(
            "bs", period_type="instant", instant="2023-12-31", dimensions=[(SCOPE_AXIS_QNAME, CONS)]
        ),
        make_context(
            "is",
            period_type="duration",
            start="2023-01-01",
            end="2023-12-31",
            dimensions=[(SCOPE_AXIS_QNAME, CONS)],
        ),
    ]
    facts = [
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="bs", raw_value=str(V_ASSETS)),
        make_fact(
            namespace=IFRS_NS, local_name="Revenue", context_id="is", raw_value=str(V_REVENUE)
        ),
        make_fact(
            namespace=IFRS_NS,
            local_name="Liabilities",
            context_id="bs",
            raw_value=str(V_LIAB - 1_000_000),  # 정확히 허용 오차 경계 → ROUNDING
        ),
    ]
    store_parsed_xbrl(
        ParsedXbrl(facts=facts, contexts=contexts, units=make_units()),
        xbrl_normalized_dir(tmp, CORP, "R1"),
    )
    # 두 rcept 모두 extracted 디렉토리가 있어야 파싱 보장 단계가 순회한다.
    (tmp / "raw" / "dart" / "xbrl" / CORP / "R1" / "extracted").mkdir(parents=True)
    # RBAD: extracted에 instance가 없어 parse_extracted가 XbrlParseError → 실패로 기록.
    (tmp / "raw" / "dart" / "xbrl" / CORP / "RBAD" / "extracted").mkdir(parents=True)


def test_status_aggregation_and_report_files(tmp_path: Path) -> None:
    _setup(tmp_path)
    report = reconcile_all(CORP, data_dir=tmp_path, scopes=(FsDiv.CFS,))

    assert report.total == 4
    assert report.by_status == {
        ReconciliationStatus.MATCH: 2,
        ReconciliationStatus.ROUNDING_DIFFERENCE: 1,
        ReconciliationStatus.REQUIRES_REVIEW: 1,
    }
    # 연간 버킷 match_rate = (MATCH+ROUNDING)/총 = 3/4.
    assert report.annual.total == 4
    assert report.annual.match_rate == 0.75

    out = reconciliation_out_dir(tmp_path, CORP)
    assert (out / REPORT_FILENAME).exists()
    assert (out / FAILURES_FILENAME).exists()

    saved = json.loads((out / REPORT_FILENAME).read_text(encoding="utf-8"))
    assert saved["by_status"] == report.by_status
    assert "records" not in saved  # JSON은 요약만(records 제외)
    assert saved["account_year_matrix"]["total_assets"]["2023"]["CFS"] == "MATCH"


def test_parse_failure_recorded_and_skipped(tmp_path: Path) -> None:
    _setup(tmp_path)
    report = reconcile_all(CORP, data_dir=tmp_path, scopes=(FsDiv.CFS,))

    # R1은 이미 parquet 존재 → already_parsed, RBAD는 파싱 실패 → failed에 기록.
    assert report.parse.already_parsed == ["R1"]
    assert [f.rcept_no for f in report.parse.failed] == ["RBAD"]

    # RBAD 소스 행(net_income)은 중단 없이 REQUIRES_REVIEW로 분류된다.
    net = [r for r in report.records if r.canonical_account_id == "net_income"]
    assert len(net) == 1
    assert net[0].status == ReconciliationStatus.REQUIRES_REVIEW
    assert net[0].reason is not None and "RBAD" in net[0].reason


def test_failures_csv_excludes_passing_rows(tmp_path: Path) -> None:
    _setup(tmp_path)
    reconcile_all(CORP, data_dir=tmp_path, scopes=(FsDiv.CFS,))

    csv_path = reconciliation_out_dir(tmp_path, CORP) / FAILURES_FILENAME
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    # MATCH·ROUNDING 제외 → REQUIRES_REVIEW(net_income) 1건만.
    assert len(rows) == 1
    assert rows[0]["canonical_account_id"] == "net_income"
    assert rows[0]["status"] == ReconciliationStatus.REQUIRES_REVIEW


def test_idempotent_reparse_skips_existing(tmp_path: Path) -> None:
    _setup(tmp_path)
    reconcile_all(CORP, data_dir=tmp_path, scopes=(FsDiv.CFS,))
    # 두 번째 실행: R1 parquet이 이미 있으니 재파싱 없이 already_parsed.
    report2 = reconcile_all(CORP, data_dir=tmp_path, scopes=(FsDiv.CFS,))
    assert report2.parse.newly_parsed == []
    assert report2.parse.already_parsed == ["R1"]
