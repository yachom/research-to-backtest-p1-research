"""재무 데이터셋 빌드 오케스트레이션 + parquet 저장 (명세 A4 §7~§8, README §16).

``build_financial_datasets(corp_code)``가 jsonl→정규화→단독분기→지표→검증→
저장을 관통한다. 산출물은 ``{data_dir}/normalized/financials/{corp_code}/``에
4개 parquet + ``build_report.json``으로 떨어진다(A6와의 계약, 명세 §7):

1. ``normalized_facts.parquet`` (long) — 단독분기·연간 fact
2. ``quarterly_financials.parquet`` (wide) — 분기 단독손익·CF·BS 기말잔액
3. ``annual_financials.parquet`` (wide) — 연간
4. ``financial_metrics.parquet`` — YoY·영업이익률 (A6가 available_from으로 as-of join)

검증(명세 §8): 회계식(자산=부채+자본)과 available_from>period_end는 **심각
위반**이라 :class:`DataValidationError`로 중단한다. 교차 소스 일관성(반기·3Q
누적)과 커버리지는 build_report에 기록만 한다 — 전자는 Current View 정정으로
정당한 불일치가 가능하고(financial_api.py docstring), 후자는 데이터 공백 보고가
목적이다. 허용 오차는 README §16.3(abs 1e6 KRW or rel 0.1%).
"""

import logging
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pydantic import BaseModel

from research_backtest.core.constants import FsDiv, ReprtCode
from research_backtest.core.dates import TradingCalendar
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.financials.metrics import Metric, compute_metrics
from research_backtest.core.financials.normalizer import (
    NormalizationResult,
    load_raw_rows,
    normalize_financials,
)
from research_backtest.core.financials.quarterly import (
    Fact,
    apply_available_from,
    derive_facts,
    period_bounds,
)
from research_backtest.core.financials.registry import (
    DEFAULT_REGISTRY_PATH,
    CanonicalAccount,
    load_registry,
)
from research_backtest.core.market.calendar import CALENDAR_FILENAME, KrxTradingCalendar

logger = logging.getLogger("r2b.financials.pipeline")
KST = ZoneInfo("Asia/Seoul")

RAW_JSONL_FILENAME = "financial_api_raw.jsonl"

# 커버리지 검증 대상 (명세 §8)
REQUIRED_ANNUAL_ACCOUNTS = [
    "revenue",
    "operating_income",
    "net_income",
    "total_assets",
    "total_liabilities",
    "total_equity",
]
REQUIRED_ANNUAL_YEARS = [2021, 2022, 2023, 2024, 2025]
RECENT_QUARTERS_REQUIRED = 8
INCOME_ACCOUNTS = ["revenue", "operating_income", "net_income"]
CROSS_SOURCE_ACCOUNTS = ["revenue", "operating_income"]

# 허용 오차 (README §16.3)
ABS_TOLERANCE = 1_000_000
REL_TOLERANCE = 0.001

# --- 출력 스키마 (명세 §7, A6 계약) ------------------------------------------

NORMALIZED_FACTS_COLUMNS = [
    "canonical_id",
    "fs_scope",
    "sj_div",
    "fiscal_year",
    "fiscal_quarter",
    "period_start",
    "period_end",
    "value",
    "value_type",
    "rcept_no",
    "rcept_dt",
    "available_from",
    "source_account_id",
    "source_account_nm",
]
_WIDE_LEAD_COLUMNS = [
    "fs_scope",
    "fiscal_year",
    "fiscal_quarter",
    "period_start",
    "period_end",
    "rcept_no",
    "rcept_dt",
    "available_from",
]
METRICS_COLUMNS = [
    "metric_id",
    "fs_scope",
    "fiscal_year",
    "fiscal_quarter",
    "period_end",
    "value",
    "rcept_no",
    "rcept_dt",
    "available_from",
    "inputs_derived",
]

NORMALIZED_FACTS_FILENAME = "normalized_facts.parquet"
QUARTERLY_FILENAME = "quarterly_financials.parquet"
ANNUAL_FILENAME = "annual_financials.parquet"
METRICS_FILENAME = "financial_metrics.parquet"
BUILD_REPORT_FILENAME = "build_report.json"


def financials_out_dir(data_dir: Path, corp_code: str) -> Path:
    """정규화 재무 산출 디렉토리 — ``{data_dir}/normalized/financials/{corp_code}``."""
    return data_dir / "normalized" / "financials" / corp_code


# --- build_report 모델 (명세 §7-⑤) ------------------------------------------


class ValidationCheck(BaseModel):
    name: str
    checked: int
    passed: bool
    violations: list[str]


class CoverageReport(BaseModel):
    annual_required_complete: bool
    recent_quarters_income_complete: bool
    missing_annual_required: list[str]
    missing_recent_quarter_income: list[str]
    recent_quarters_checked: list[str]


class MatchingReport(BaseModel):
    per_account_matched_rows: dict[str, int]
    unmatched_row_count: int
    sce_skipped_count: int
    processed_row_count: int
    unresolved: list[dict[str, Any]]


class FileSummary(BaseModel):
    normalized_facts_rows: int
    quarterly_financials_rows: int
    annual_financials_rows: int
    financial_metrics_rows: int


class FinancialBuildReport(BaseModel):
    corp_code: str
    generated_at: str
    scopes: list[str]
    fact_count: int
    matching: MatchingReport
    derivation_gaps: list[dict[str, Any]]
    validations: list[ValidationCheck]
    coverage: CoverageReport
    files: FileSummary


def build_financial_datasets(
    corp_code: str,
    *,
    data_dir: Path | None = None,
    calendar: TradingCalendar | None = None,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
    scopes: Sequence[FsDiv] = (FsDiv.CFS, FsDiv.OFS),
    write: bool = True,
) -> FinancialBuildReport:
    """corp_code의 재무 데이터셋 4종 + build_report를 빌드한다 (명세 §7~§8).

    - ``data_dir`` 미지정 시 :func:`get_settings().data_dir` 사용(DATA_DIR env).
    - ``calendar`` 미지정 시 ``{data_dir}/normalized/market/calendar``의 KRX
      거래일 캘린더에서 로드한다(A3 산출).
    - 회계식·available_from 위반은 :class:`DataValidationError`로 중단한다.
    - ``write=False``면 parquet을 쓰지 않고 리포트만 반환한다(테스트용).
    """
    if data_dir is None:
        from research_backtest.core.config import get_settings

        data_dir = get_settings().data_dir
    scope_values = [scope.value for scope in scopes]

    registry = load_registry(registry_path)
    jsonl_path = data_dir / "raw" / "dart" / "financials" / corp_code / RAW_JSONL_FILENAME
    raw_rows = load_raw_rows(jsonl_path)
    if calendar is None:
        calendar = KrxTradingCalendar.from_parquet(
            data_dir / "normalized" / "market" / "calendar" / CALENDAR_FILENAME
        )

    normalization = normalize_financials(raw_rows, registry, scopes=scope_values)
    quarterly = derive_facts(normalization, registry)
    apply_available_from(quarterly.facts, calendar)
    metrics = compute_metrics(quarterly.facts)

    facts_df = _build_normalized_facts(quarterly.facts)
    quarterly_df = _build_wide(quarterly.facts, registry, annual=False)
    annual_df = _build_wide(quarterly.facts, registry, annual=True)
    metrics_df = _build_metrics(metrics)

    validations = _run_validations(quarterly.facts, normalization, registry, scope_values)
    coverage = _check_coverage(quarterly.facts, registry)

    report = FinancialBuildReport(
        corp_code=corp_code,
        generated_at=datetime.now(KST).isoformat(),
        scopes=scope_values,
        fact_count=len(quarterly.facts),
        matching=MatchingReport(
            per_account_matched_rows=normalization.matched_row_counts,
            unmatched_row_count=normalization.unmatched_row_count,
            sce_skipped_count=normalization.sce_skipped_count,
            processed_row_count=normalization.processed_row_count,
            unresolved=[_dataclass_to_dict(u) for u in normalization.unresolved],
        ),
        derivation_gaps=[_dataclass_to_dict(g) for g in quarterly.gaps],
        validations=validations,
        coverage=coverage,
        files=FileSummary(
            normalized_facts_rows=len(facts_df),
            quarterly_financials_rows=len(quarterly_df),
            annual_financials_rows=len(annual_df),
            financial_metrics_rows=len(metrics_df),
        ),
    )

    if write:
        out_dir = financials_out_dir(data_dir, corp_code)
        out_dir.mkdir(parents=True, exist_ok=True)
        facts_df.to_parquet(out_dir / NORMALIZED_FACTS_FILENAME, engine="pyarrow", index=False)
        quarterly_df.to_parquet(out_dir / QUARTERLY_FILENAME, engine="pyarrow", index=False)
        annual_df.to_parquet(out_dir / ANNUAL_FILENAME, engine="pyarrow", index=False)
        metrics_df.to_parquet(out_dir / METRICS_FILENAME, engine="pyarrow", index=False)
        (out_dir / BUILD_REPORT_FILENAME).write_text(
            report.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        logger.info(
            "재무 데이터셋 빌드 완료 corp_code=%s facts=%d metrics=%d unresolved=%d gaps=%d",
            corp_code,
            len(facts_df),
            len(metrics_df),
            len(normalization.unresolved),
            len(quarterly.gaps),
        )

    # 심각 위반은 저장 후 예외 — 산출물은 남기되 실패를 은폐하지 않는다 (명세 §8·§27)
    severe = [v for v in validations if v.name in _SEVERE_CHECKS and not v.passed]
    if severe:
        raise DataValidationError(
            "재무 검증 심각 위반: "
            + "; ".join(f"{v.name}({len(v.violations)}건)" for v in severe)
            + f" — 상세는 {BUILD_REPORT_FILENAME}"
        )
    return report


# --- DataFrame 빌드 ----------------------------------------------------------


def _build_normalized_facts(facts: Sequence[Fact]) -> pd.DataFrame:
    """long 형식 normalized_facts (명세 §7-①)."""
    records = [
        {
            "canonical_id": f.canonical_id,
            "fs_scope": f.fs_scope,
            "sj_div": f.sj_div,
            "fiscal_year": f.fiscal_year,
            "fiscal_quarter": f.fiscal_quarter,
            "period_start": f.period_start,
            "period_end": f.period_end,
            "value": f.value,
            "value_type": f.value_type,
            "rcept_no": f.rcept_no,
            "rcept_dt": f.rcept_dt,
            "available_from": f.available_from,
            "source_account_id": f.source_account_id,
            "source_account_nm": f.source_account_nm,
        }
        for f in facts
    ]
    frame = pd.DataFrame(records, columns=NORMALIZED_FACTS_COLUMNS)
    frame = frame.astype(
        {
            "canonical_id": "string",
            "fs_scope": "string",
            "sj_div": "string",
            "fiscal_year": "int64",
            "fiscal_quarter": "Int64",
            "value": "Int64",
            "value_type": "string",
            "rcept_no": "string",
            "source_account_id": "string",
            "source_account_nm": "string",
        }
    )
    return frame


def _build_wide(
    facts: Sequence[Fact], registry: dict[str, CanonicalAccount], *, annual: bool
) -> pd.DataFrame:
    """wide 형식 quarterly/annual (명세 §7-②③).

    (scope, year[, quarter])마다 한 행. canonical 계정은 registry 순서로
    컬럼화한다(Int64). 행 단위 rcept·available_from은 그 기간 fact들의 max
    available_from 기준이다.
    """
    account_columns = list(registry.keys())
    groups: dict[tuple[str, int, int | None], list[Fact]] = {}
    for fact in facts:
        is_annual_fact = fact.fiscal_quarter is None
        if is_annual_fact != annual:
            continue
        key = (fact.fs_scope, fact.fiscal_year, fact.fiscal_quarter)
        groups.setdefault(key, []).append(fact)

    records: list[dict[str, Any]] = []
    for fs_scope, year, quarter in sorted(groups, key=lambda k: (k[0], k[1], k[2] or 0)):
        group = groups[(fs_scope, year, quarter)]
        start, end = period_bounds(year, quarter)
        anchor = max(group, key=lambda f: _require(f.available_from))
        record: dict[str, Any] = {
            "fs_scope": fs_scope,
            "fiscal_year": year,
            "fiscal_quarter": quarter,
            "period_start": start,
            "period_end": end,
            "rcept_no": anchor.rcept_no,
            "rcept_dt": anchor.rcept_dt,
            "available_from": anchor.available_from,
        }
        values = {f.canonical_id: f.value for f in group}
        for canonical_id in account_columns:
            record[canonical_id] = values.get(canonical_id)
        records.append(record)

    columns = list(_WIDE_LEAD_COLUMNS)
    if annual:
        columns.remove("fiscal_quarter")
    columns = columns + account_columns

    frame = pd.DataFrame(records, columns=columns)
    dtypes: dict[str, str] = {
        "fs_scope": "string",
        "fiscal_year": "int64",
        "rcept_no": "string",
    }
    if not annual:
        dtypes["fiscal_quarter"] = "Int64"
    for canonical_id in account_columns:
        dtypes[canonical_id] = "Int64"
    return frame.astype(dtypes)


def _build_metrics(metrics: Sequence[Metric]) -> pd.DataFrame:
    """financial_metrics (명세 §7-④)."""
    records = [
        {
            "metric_id": m.metric_id,
            "fs_scope": m.fs_scope,
            "fiscal_year": m.fiscal_year,
            "fiscal_quarter": m.fiscal_quarter,
            "period_end": m.period_end,
            "value": m.value,
            "rcept_no": m.rcept_no,
            "rcept_dt": m.rcept_dt,
            "available_from": m.available_from,
            "inputs_derived": m.inputs_derived,
        }
        for m in metrics
    ]
    frame = pd.DataFrame(records, columns=METRICS_COLUMNS)
    return frame.astype(
        {
            "metric_id": "string",
            "fs_scope": "string",
            "fiscal_year": "int64",
            "fiscal_quarter": "Int64",
            "value": "float64",
            "rcept_no": "string",
            "inputs_derived": "bool",
        }
    )


# --- 검증 (명세 §8) ----------------------------------------------------------

_SEVERE_CHECKS = frozenset({"accounting_identity", "available_from_gt_period_end"})


def _run_validations(
    facts: Sequence[Fact],
    normalization: NormalizationResult,
    registry: dict[str, CanonicalAccount],
    scopes: Sequence[str],
) -> list[ValidationCheck]:
    return [
        _check_accounting_identity(facts),
        _check_cross_source_consistency(facts, normalization, scopes),
        _check_available_from(facts),
    ]


def _check_accounting_identity(facts: Sequence[Fact]) -> ValidationCheck:
    """자산총계 ≈ 부채총계 + 자본총계 — 각 (scope·연도·분기) (README §16.2, 심각)."""
    by_period: dict[tuple[str, int, int | None], dict[str, int]] = {}
    for fact in facts:
        key = (fact.fs_scope, fact.fiscal_year, fact.fiscal_quarter)
        by_period.setdefault(key, {})[fact.canonical_id] = fact.value

    violations: list[str] = []
    checked = 0
    for (fs_scope, year, quarter), values in sorted(by_period.items(), key=_period_sort):
        if not {"total_assets", "total_liabilities", "total_equity"} <= values.keys():
            continue
        checked += 1
        assets = values["total_assets"]
        liab_equity = values["total_liabilities"] + values["total_equity"]
        if not _within_tolerance(assets, liab_equity):
            violations.append(
                f"{fs_scope} {year}{_q_label(quarter)}: 자산 {assets} ≠ 부채+자본 {liab_equity}"
                f" (차이 {assets - liab_equity})"
            )
    return ValidationCheck(
        name="accounting_identity", checked=checked, passed=not violations, violations=violations
    )


def _check_cross_source_consistency(
    facts: Sequence[Fact], normalization: NormalizationResult, scopes: Sequence[str]
) -> ValidationCheck:
    """반기 add == Q1+Q2 단독, 3Q add == 반기 add + Q3 단독 (매출·영업이익, 기록 전용)."""
    single: dict[tuple[str, str, int, int], int] = {}
    for fact in facts:
        if fact.fiscal_quarter is not None:
            single[(fact.canonical_id, fact.fs_scope, fact.fiscal_year, fact.fiscal_quarter)] = (
                fact.value
            )

    violations: list[str] = []
    checked = 0
    for canonical_id in CROSS_SOURCE_ACCOUNTS:
        for fs_scope in scopes:
            for year in normalization.years(fs_scope):
                half = normalization.get(canonical_id, fs_scope, year, ReprtCode.HALF.value)
                q3 = normalization.get(canonical_id, fs_scope, year, ReprtCode.Q3.value)
                q1v = single.get((canonical_id, fs_scope, year, 1))
                q2v = single.get((canonical_id, fs_scope, year, 2))
                q3v = single.get((canonical_id, fs_scope, year, 3))
                if (
                    half is not None
                    and half.thstrm_add_amount is not None
                    and None not in (q1v, q2v)
                ):
                    checked += 1
                    if not _within_tolerance(half.thstrm_add_amount, q1v + q2v):  # type: ignore[operator]
                        violations.append(
                            f"{fs_scope} {canonical_id} {year} 반기누적 {half.thstrm_add_amount}"
                            f" ≠ Q1+Q2 {q1v + q2v}"  # type: ignore[operator]
                        )
                if (
                    q3 is not None
                    and q3.thstrm_add_amount is not None
                    and half is not None
                    and half.thstrm_add_amount is not None
                    and q3v is not None
                ):
                    checked += 1
                    if not _within_tolerance(q3.thstrm_add_amount, half.thstrm_add_amount + q3v):
                        violations.append(
                            f"{fs_scope} {canonical_id} {year} 3Q누적 {q3.thstrm_add_amount}"
                            f" ≠ 반기누적+Q3 {half.thstrm_add_amount + q3v}"
                        )
    return ValidationCheck(
        name="cross_source_consistency",
        checked=checked,
        passed=not violations,
        violations=violations,
    )


def _check_available_from(facts: Sequence[Fact]) -> ValidationCheck:
    """available_from > period_end — 전 행 (공시는 회계기간 종료 후, 심각/룩어헤드 방지)."""
    violations: list[str] = []
    for fact in facts:
        af = fact.available_from
        if af is None or af <= fact.period_end:
            violations.append(
                f"{fact.fs_scope} {fact.canonical_id} {fact.fiscal_year}"
                f"{_q_label(fact.fiscal_quarter)}: available_from {af} <= "
                f"period_end {fact.period_end}"
            )
    return ValidationCheck(
        name="available_from_gt_period_end",
        checked=len(facts),
        passed=not violations,
        violations=violations,
    )


def _check_coverage(facts: Sequence[Fact], registry: dict[str, CanonicalAccount]) -> CoverageReport:
    """CFS 연간 필수계정 5개년 + 최근 8개 분기 단독손익 non-null (명세 §8, 기록 전용)."""
    annual_present: set[tuple[str, int]] = {
        (f.canonical_id, f.fiscal_year)
        for f in facts
        if f.fs_scope == FsDiv.CFS.value and f.fiscal_quarter is None
    }
    missing_annual = [
        f"{account} {year}"
        for year in REQUIRED_ANNUAL_YEARS
        for account in REQUIRED_ANNUAL_ACCOUNTS
        if (account, year) not in annual_present
    ]

    quarters = sorted(
        {
            (f.fiscal_year, f.fiscal_quarter)
            for f in facts
            if f.fs_scope == FsDiv.CFS.value and f.fiscal_quarter is not None
        },
        reverse=True,
    )[:RECENT_QUARTERS_REQUIRED]
    income_present: set[tuple[str, int, int]] = {
        (f.canonical_id, f.fiscal_year, f.fiscal_quarter)
        for f in facts
        if f.fs_scope == FsDiv.CFS.value and f.fiscal_quarter is not None
    }
    missing_recent = [
        f"{account} {year}Q{quarter}"
        for (year, quarter) in quarters
        for account in INCOME_ACCOUNTS
        if (account, year, quarter) not in income_present
    ]
    return CoverageReport(
        annual_required_complete=not missing_annual,
        recent_quarters_income_complete=not missing_recent,
        missing_annual_required=missing_annual,
        missing_recent_quarter_income=missing_recent,
        recent_quarters_checked=[f"{y}Q{q}" for (y, q) in quarters],
    )


# --- 헬퍼 --------------------------------------------------------------------


def _within_tolerance(actual: int, expected: int) -> bool:
    """abs 1e6 KRW or rel 0.1% (README §16.3)."""
    diff = abs(actual - expected)
    if diff <= ABS_TOLERANCE:
        return True
    denom = max(abs(actual), abs(expected))
    return denom > 0 and diff / denom <= REL_TOLERANCE


def _require(value: date | None) -> date:
    if value is None:
        raise ValueError("available_from이 부여되지 않았습니다.")
    return value


def _period_sort(
    item: tuple[tuple[str, int, int | None], dict[str, int]],
) -> tuple[str, int, int]:
    (fs_scope, year, quarter), _ = item
    return (fs_scope, year, quarter or 0)


def _q_label(quarter: int | None) -> str:
    return "" if quarter is None else f"Q{quarter}"


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    from dataclasses import asdict, is_dataclass

    if is_dataclass(obj) and not isinstance(obj, type):
        return asdict(obj)
    raise TypeError(f"dataclass가 아닙니다: {type(obj)}")
