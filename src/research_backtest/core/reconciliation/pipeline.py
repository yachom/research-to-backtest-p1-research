"""배치 대조·리포트 — 전 파싱 보장 → 전량 대조 → 저장 (명세 B3 §2·§5, README §19.7).

:func:`reconcile_all`이 한 기업의 수집된 정기보고서 전체를 관통한다:

1. **전 XBRL 파싱 보장(멱등)** — ``raw/dart/xbrl/{corp}/{rcept}/extracted``마다
   normalized parquet(B2 store)이 없으면 ``parse_extracted → store_parsed_xbrl``
   로 생성한다(이미 있으면 스킵). 파싱 실패는 중단하지 않고 리포트에 기록하고
   계속한다(명세 §2).
2. **대조** — A4 ``normalized_facts.parquet``의 REPORTED 행(7 대표계정 x scope)을
   기준 목록으로, 각 행의 rcept XBRL에서 :func:`select_fact`로 fact를 골라
   :func:`classify`로 상태를 매긴다(명세 §5).
3. **저장** — ``data/analytics/reconciliation/{corp}/reconciliation_report.json``
   (요약: 상태별 카운트·계정x연도 매트릭스)와 ``reconciliation_failures.csv``
   (MATCH·ROUNDING 외 전 행). 실행마다 덮어쓴다(README §19.7).

registry·A4·B2 산출은 **소비만** 한다(명세 §0 파일 소유권).
"""

from __future__ import annotations

import csv
import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel

from research_backtest.core.constants import FsDiv
from research_backtest.core.dart.xbrl_downloader import EXTRACTED_DIRNAME, xbrl_out_dir
from research_backtest.core.exceptions import XbrlParseError
from research_backtest.core.financials.pipeline import (
    NORMALIZED_FACTS_FILENAME,
    financials_out_dir,
)
from research_backtest.core.financials.registry import (
    DEFAULT_REGISTRY_PATH,
    CanonicalAccount,
    load_registry,
)
from research_backtest.core.reconciliation.compare import (
    PASSING_STATUSES,
    ReconciliationResult,
    ReconciliationStatus,
    classify,
)
from research_backtest.core.reconciliation.xbrl_select import XbrlIndex, select_fact
from research_backtest.core.xbrl.parser import parse_extracted
from research_backtest.core.xbrl.store import (
    CONTEXTS_FILENAME,
    DIMENSIONS_FILENAME,
    FACTS_FILENAME,
    load_xbrl_table,
    store_parsed_xbrl,
    xbrl_normalized_dir,
)

logger = logging.getLogger("r2b.reconciliation.pipeline")
KST = ZoneInfo("Asia/Seoul")

#: README §16.4 대표 7계정 — registry canonical_id, 리포트·정렬 순서.
TARGET_ACCOUNTS: tuple[str, ...] = (
    "total_assets",
    "total_liabilities",
    "total_equity",
    "revenue",
    "operating_income",
    "net_income",
    "operating_cash_flow",
)

REPORTED_VALUE_TYPE = "REPORTED"

REPORT_FILENAME = "reconciliation_report.json"
FAILURES_FILENAME = "reconciliation_failures.csv"

FAILURE_CSV_COLUMNS = [
    "canonical_account_id",
    "fs_scope",
    "fiscal_year",
    "fiscal_quarter",
    "period_end",
    "rcept_no",
    "status",
    "api_value",
    "xbrl_value",
    "absolute_difference",
    "relative_difference",
    "reason",
]


# --- 리포트 모델 -------------------------------------------------------------


class ParseFailure(BaseModel):
    rcept_no: str
    error: str


class ParseSummary(BaseModel):
    """전 XBRL 파싱 보장 단계 결과 (명세 §2)."""

    newly_parsed: list[str]
    already_parsed: list[str]
    failed: list[ParseFailure]


class BucketSummary(BaseModel):
    """연간/분기 버킷별 상태 분포 (명세 §5·§7 DoD)."""

    total: int
    by_status: dict[str, int]
    match_rate: float  # (MATCH + ROUNDING) / total


class FailureItem(BaseModel):
    """MATCH·ROUNDING 외 행의 요약(리포트·csv 공통 키)."""

    canonical_account_id: str
    fs_scope: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end: str
    rcept_no: str
    status: str
    reason: str | None


class ReconciliationRecord(BaseModel):
    """대조 1행 — ReconciliationResult + 출처(연도·분기·rcept). 집계·csv의 원천."""

    canonical_account_id: str
    fs_scope: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_end: str
    rcept_no: str
    api_value: Decimal | None
    xbrl_value: Decimal | None
    absolute_difference: Decimal | None
    relative_difference: float | None
    status: str
    reason: str | None


class ReconciliationReport(BaseModel):
    """대조 배치 요약 (명세 §5). ``records``는 전 행(반환용, JSON 파일에선 제외)."""

    corp_code: str
    generated_at: str
    scopes: list[str]
    parse: ParseSummary

    total: int
    by_status: dict[str, int]
    annual: BucketSummary
    quarterly: BucketSummary
    account_year_matrix: dict[str, dict[str, dict[str, str]]]
    failures: list[FailureItem]

    records: list[ReconciliationRecord]


def reconciliation_out_dir(data_dir: Path, corp_code: str) -> Path:
    """대조 산출 디렉토리 — ``{data_dir}/analytics/reconciliation/{corp_code}``."""
    return data_dir / "analytics" / "reconciliation" / corp_code


def reconcile_all(
    corp_code: str,
    *,
    data_dir: Path,
    scopes: Sequence[FsDiv] = (FsDiv.CFS, FsDiv.OFS),
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    write: bool = True,
) -> ReconciliationReport:
    """corp_code의 전 정기보고서 x scope x 7계정을 API-XBRL 대조한다 (명세 §5).

    - 전 XBRL 파싱을 먼저 보장한다(멱등, 실패는 기록 후 계속).
    - A4 ``normalized_facts.parquet``의 REPORTED 행(7 대표계정)을 기준 목록으로 대조.
    - ``write=True``면 리포트 JSON·failures CSV를 저장한다(실행마다 덮어씀).
    """
    registry = load_registry(registry_path)
    accounts = _resolve_target_accounts(registry)
    scope_values = [s.value for s in scopes]

    parse_summary, indexes = _ensure_all_parsed(corp_code, data_dir=data_dir)

    facts_df = _load_reported_targets(corp_code, data_dir=data_dir, scopes=scope_values)
    records = _reconcile_rows(facts_df, accounts=accounts, indexes=indexes)

    report = _build_report(
        corp_code=corp_code,
        scope_values=scope_values,
        parse_summary=parse_summary,
        records=records,
    )

    if write:
        _write_report(report, out_dir=reconciliation_out_dir(data_dir, corp_code))

    logger.info(
        "정합성 대조 완료 corp_code=%s 총 %d행 상태분포=%s (연간 match_rate=%.3f)",
        corp_code,
        report.total,
        report.by_status,
        report.annual.match_rate,
    )
    return report


# --- 1. 전 XBRL 파싱 보장 (명세 §2) ------------------------------------------


def _ensure_all_parsed(
    corp_code: str, *, data_dir: Path
) -> tuple[ParseSummary, dict[str, XbrlIndex]]:
    """rcept마다 normalized parquet을 보장(멱등)하고 rcept→인덱스 맵을 만든다.

    파싱 실패는 :class:`ParseFailure`로 기록하고 그 rcept는 인덱스에서 제외한다
    (대조 시 그 행은 XBRL 부재로 분류된다).
    """
    corp_xbrl_dir = xbrl_out_dir(data_dir, corp_code)
    newly, already, failed = [], [], []
    indexes: dict[str, XbrlIndex] = {}

    for rcept_dir in _iter_rcept_dirs(corp_xbrl_dir):
        rcept_no = rcept_dir.name
        normalized_dir = xbrl_normalized_dir(data_dir, corp_code, rcept_no)
        try:
            if not (normalized_dir / FACTS_FILENAME).exists():
                parsed = parse_extracted(rcept_dir / EXTRACTED_DIRNAME)
                store_parsed_xbrl(parsed, normalized_dir)
                newly.append(rcept_no)
            else:
                already.append(rcept_no)
            indexes[rcept_no] = _load_index(normalized_dir)
        except XbrlParseError as err:
            logger.warning("XBRL 파싱 실패 rcept=%s: %s", rcept_no, err)
            failed.append(ParseFailure(rcept_no=rcept_no, error=str(err)))

    summary = ParseSummary(newly_parsed=newly, already_parsed=already, failed=failed)
    logger.info(
        "XBRL 파싱 보장: 신규 %d · 기존 %d · 실패 %d",
        len(newly),
        len(already),
        len(failed),
    )
    return summary, indexes


def _iter_rcept_dirs(corp_xbrl_dir: Path) -> list[Path]:
    """corp XBRL 디렉토리 아래 rcept 하위 디렉토리를 결정적 순서로 반환한다."""
    if not corp_xbrl_dir.is_dir():
        return []
    return sorted(
        (p for p in corp_xbrl_dir.iterdir() if p.is_dir() and (p / EXTRACTED_DIRNAME).is_dir()),
        key=lambda p: p.name,
    )


def _load_index(normalized_dir: Path) -> XbrlIndex:
    """저장된 B2 parquet 3종에서 조회 인덱스를 만든다."""
    facts = load_xbrl_table(normalized_dir, FACTS_FILENAME)
    contexts = load_xbrl_table(normalized_dir, CONTEXTS_FILENAME)
    dimensions = load_xbrl_table(normalized_dir, DIMENSIONS_FILENAME)
    return XbrlIndex.from_frames(facts, contexts, dimensions)


# --- 2. 대조 (명세 §5) -------------------------------------------------------


def _load_reported_targets(
    corp_code: str, *, data_dir: Path, scopes: Sequence[str]
) -> pd.DataFrame:
    """A4 normalized_facts에서 REPORTED x 7대표계정 x scope 행만 로드한다."""
    path = financials_out_dir(data_dir, corp_code) / NORMALIZED_FACTS_FILENAME
    df = pd.read_parquet(path)
    mask = (
        (df["value_type"] == REPORTED_VALUE_TYPE)
        & (df["canonical_id"].isin(TARGET_ACCOUNTS))
        & (df["fs_scope"].isin(list(scopes)))
    )
    return df.loc[mask].copy()


def _reconcile_rows(
    facts_df: pd.DataFrame,
    *,
    accounts: dict[str, CanonicalAccount],
    indexes: dict[str, XbrlIndex],
) -> list[ReconciliationRecord]:
    """기준 목록의 각 REPORTED 행을 대조해 :class:`ReconciliationRecord`로 만든다."""
    rows = [_row_key(r) for r in facts_df.to_dict("records")]
    rows.sort(key=lambda r: (r.fs_scope, r.fiscal_year, r.quarter_sort, r.account_order))

    records: list[ReconciliationRecord] = []
    for row in rows:
        result = _reconcile_one(row, accounts=accounts, indexes=indexes)
        records.append(
            ReconciliationRecord(
                canonical_account_id=result.canonical_account_id,
                fs_scope=result.fs_scope,
                fiscal_year=row.fiscal_year,
                fiscal_quarter=row.fiscal_quarter,
                period_end=result.period_end,
                rcept_no=row.rcept_no,
                api_value=result.api_value,
                xbrl_value=result.xbrl_value,
                absolute_difference=result.absolute_difference,
                relative_difference=result.relative_difference,
                status=result.status,
                reason=result.reason,
            )
        )
    return records


def _reconcile_one(
    row: _RowKey,
    *,
    accounts: dict[str, CanonicalAccount],
    indexes: dict[str, XbrlIndex],
) -> ReconciliationResult:
    """한 REPORTED 행 → 선택·비교. XBRL 부재(파싱 실패) 시 REQUIRES_REVIEW."""
    account = accounts[row.canonical_id]
    period_end = _to_iso(row.period_end)
    assert period_end is not None  # REPORTED 행은 period_end가 항상 있다
    index = indexes.get(row.rcept_no)
    if index is None:
        return ReconciliationResult(
            canonical_account_id=row.canonical_id,
            period_end=period_end,
            fs_scope=row.fs_scope,
            api_value=row.api_value,
            xbrl_value=None,
            major_account_value=None,
            absolute_difference=None,
            relative_difference=None,
            status=ReconciliationStatus.REQUIRES_REVIEW,
            reason=f"rcept {row.rcept_no} XBRL 파싱 실패/부재로 대조 불가.",
        )

    selection = select_fact(
        index,
        accepted_concepts=account.accepted_concepts,
        scope=FsDiv(row.fs_scope),
        period_type=account.period_type,
        period_start=_to_iso(row.period_start),
        period_end=period_end,
    )
    return classify(
        selection,
        api_value=row.api_value,
        canonical_account_id=row.canonical_id,
        period_end=period_end,
        fs_scope=row.fs_scope,
    )


# --- 3. 집계·저장 (명세 §5, README §19.7) ------------------------------------


def _build_report(
    *,
    corp_code: str,
    scope_values: list[str],
    parse_summary: ParseSummary,
    records: list[ReconciliationRecord],
) -> ReconciliationReport:
    annual = [r for r in records if r.fiscal_quarter is None]
    quarterly = [r for r in records if r.fiscal_quarter is not None]
    failures = [
        FailureItem(
            canonical_account_id=r.canonical_account_id,
            fs_scope=r.fs_scope,
            fiscal_year=r.fiscal_year,
            fiscal_quarter=r.fiscal_quarter,
            period_end=r.period_end,
            rcept_no=r.rcept_no,
            status=r.status,
            reason=r.reason,
        )
        for r in records
        if r.status not in PASSING_STATUSES
    ]
    return ReconciliationReport(
        corp_code=corp_code,
        generated_at=datetime.now(KST).isoformat(),
        scopes=scope_values,
        parse=parse_summary,
        total=len(records),
        by_status=_count_status(records),
        annual=_bucket_summary(annual),
        quarterly=_bucket_summary(quarterly),
        account_year_matrix=_account_year_matrix(annual),
        failures=failures,
        records=records,
    )


def _count_status(records: Sequence[ReconciliationRecord]) -> dict[str, int]:
    counter: Counter[str] = Counter(r.status for r in records)
    return dict(sorted(counter.items()))


def _bucket_summary(records: Sequence[ReconciliationRecord]) -> BucketSummary:
    by_status = _count_status(records)
    passing = sum(v for k, v in by_status.items() if k in PASSING_STATUSES)
    total = len(records)
    return BucketSummary(
        total=total,
        by_status=by_status,
        match_rate=(passing / total) if total else 0.0,
    )


def _account_year_matrix(
    annual: Sequence[ReconciliationRecord],
) -> dict[str, dict[str, dict[str, str]]]:
    """연간 행의 계정 x 연도 x scope → status 매트릭스 (명세 §5)."""
    matrix: dict[str, dict[str, dict[str, str]]] = {}
    for account in TARGET_ACCOUNTS:
        year_map: dict[str, dict[str, str]] = {}
        for r in annual:
            if r.canonical_account_id != account:
                continue
            year_map.setdefault(str(r.fiscal_year), {})[r.fs_scope] = r.status
        if year_map:
            matrix[account] = dict(sorted(year_map.items()))
    return matrix


def _write_report(report: ReconciliationReport, *, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / REPORT_FILENAME).write_text(
        report.model_dump_json(indent=2, exclude={"records"}) + "\n", encoding="utf-8"
    )
    _write_failures_csv(report.records, path=out_dir / FAILURES_FILENAME)


def _write_failures_csv(records: Sequence[ReconciliationRecord], *, path: Path) -> None:
    """MATCH·ROUNDING 외 전 행을 CSV로 (README §19.7). Decimal은 문자열로 보존."""
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FAILURE_CSV_COLUMNS)
        writer.writeheader()
        for r in records:
            if r.status in PASSING_STATUSES:
                continue
            writer.writerow(
                {
                    "canonical_account_id": r.canonical_account_id,
                    "fs_scope": r.fs_scope,
                    "fiscal_year": r.fiscal_year,
                    "fiscal_quarter": "" if r.fiscal_quarter is None else r.fiscal_quarter,
                    "period_end": r.period_end,
                    "rcept_no": r.rcept_no,
                    "status": r.status,
                    "api_value": _decimal_str(r.api_value),
                    "xbrl_value": _decimal_str(r.xbrl_value),
                    "absolute_difference": _decimal_str(r.absolute_difference),
                    "relative_difference": (
                        "" if r.relative_difference is None else repr(r.relative_difference)
                    ),
                    "reason": r.reason or "",
                }
            )


# --- 헬퍼 --------------------------------------------------------------------


class _RowKey(BaseModel):
    """정렬·대조에 쓰는 A4 REPORTED 행의 정규화 뷰."""

    canonical_id: str
    fs_scope: str
    fiscal_year: int
    fiscal_quarter: int | None
    period_start: date | None
    period_end: date | None
    rcept_no: str
    api_value: Decimal | None
    quarter_sort: int
    account_order: int


def _row_key(row: Mapping[Any, Any]) -> _RowKey:
    canonical_id = str(row["canonical_id"])
    quarter = row["fiscal_quarter"]
    fiscal_quarter = None if pd.isna(quarter) else int(quarter)
    value = row["value"]
    api_value = None if pd.isna(value) else Decimal(int(value))
    try:
        account_order = TARGET_ACCOUNTS.index(canonical_id)
    except ValueError:
        account_order = len(TARGET_ACCOUNTS)
    return _RowKey(
        canonical_id=canonical_id,
        fs_scope=str(row["fs_scope"]),
        fiscal_year=int(row["fiscal_year"]),
        fiscal_quarter=fiscal_quarter,
        period_start=_as_date(row["period_start"]),
        period_end=_as_date(row["period_end"]),
        rcept_no=str(row["rcept_no"]),
        api_value=api_value,
        quarter_sort=0 if fiscal_quarter is None else fiscal_quarter,
        account_order=account_order,
    )


def _as_date(value: object) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):  # datetime.datetime 또는 pd.Timestamp(datetime 하위)
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _to_iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _decimal_str(value: Decimal | None) -> str:
    return "" if value is None else str(value)


def _resolve_target_accounts(
    registry: dict[str, CanonicalAccount],
) -> dict[str, CanonicalAccount]:
    """registry에서 7 대표계정을 뽑는다 — 누락 시 즉시 실패(계약 위반)."""
    missing = [a for a in TARGET_ACCOUNTS if a not in registry]
    if missing:
        raise KeyError(f"registry에 대표계정이 없습니다: {missing}")
    return {a: registry[a] for a in TARGET_ACCOUNTS}
