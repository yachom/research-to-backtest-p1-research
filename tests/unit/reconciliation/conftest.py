"""B3 정합성 검증 unit 픽스처 — 오프라인, 소형 합성 parquet (명세 §6).

XBRL은 B2 모델(:class:`ParsedXbrl`)로 합성해 :func:`store_parsed_xbrl`로 parquet에
쓰고 :meth:`XbrlIndex.from_frames`로 되읽어(=파이프라인 경로) 인덱스를 만든다.
A4 ``normalized_facts.parquet``도 실제 스키마의 소형 합성본을 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.reconciliation.xbrl_select import XbrlIndex
from research_backtest.core.xbrl.models import (
    ParsedXbrl,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlUnit,
)
from research_backtest.core.xbrl.store import (
    CONTEXTS_FILENAME,
    DIMENSIONS_FILENAME,
    FACTS_FILENAME,
    load_xbrl_table,
    store_parsed_xbrl,
)

IFRS_NS = "http://xbrl.ifrs.org/taxonomy/2019-03-27/ifrs-full"
DART_NS = "http://dart.fss.or.kr/taxonomy/2019-10-01/ifrs/dart"
DART_GCD_NS = "http://dart.fss.or.kr/taxonomy/2019-10-01/ifrs/dart-gcd"
SCOPE_AXIS_QNAME = "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"


def make_context(
    context_id: str,
    *,
    period_type: str,
    instant: str | None = None,
    start: str | None = None,
    end: str | None = None,
    dimensions: Sequence[tuple[str, str]] = (),
) -> XbrlContext:
    """합성 XbrlContext — ``dimensions``는 (axis_qname, member_qname) 목록(segment)."""
    return XbrlContext(
        context_id=context_id,
        entity_identifier="00164779",
        entity_scheme="http://dart.fss.or.kr/ifrs/CIK",
        period_type=period_type,
        instant_date=instant,
        start_date=start,
        end_date=end,
        segment_dimensions=[
            XbrlDimension(axis_qname=axis, member_qname=member, typed_member_value=None)
            for axis, member in dimensions
        ],
        scenario_dimensions=[],
    )


def make_fact(
    *,
    namespace: str,
    local_name: str,
    context_id: str,
    raw_value: str | None,
    prefix: str = "ns",
) -> XbrlFact:
    """합성 XbrlFact — 정밀 비교용 raw_value 보존, numeric_value는 부수적."""
    numeric = Decimal(raw_value) if raw_value is not None else None
    return XbrlFact(
        concept_qname=f"{prefix}:{local_name}",
        concept_namespace=namespace,
        concept_local_name=local_name,
        context_id=context_id,
        unit_id="KRW",
        raw_value=raw_value,
        numeric_value=numeric,
        decimals="-6",
        scale=None,
        is_nil=raw_value is None,
        source_file="synthetic.xbrl",
    )


@pytest.fixture
def index_from_parsed(tmp_path: Path) -> Callable[[ParsedXbrl], XbrlIndex]:
    """ParsedXbrl → parquet 저장 → from_frames 인덱스 (파이프라인 parquet 경로 검증)."""
    counter = {"n": 0}

    def _build(parsed: ParsedXbrl) -> XbrlIndex:
        counter["n"] += 1
        out = tmp_path / f"xbrl_{counter['n']}"
        store_parsed_xbrl(parsed, out)
        return XbrlIndex.from_frames(
            load_xbrl_table(out, FACTS_FILENAME),
            load_xbrl_table(out, CONTEXTS_FILENAME),
            load_xbrl_table(out, DIMENSIONS_FILENAME),
        )

    return _build


# --- A4 normalized_facts 합성 (pipeline 테스트) -------------------------------

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


def write_normalized_facts(rows: Sequence[dict[str, object]], path: Path) -> None:
    """A4 normalized_facts.parquet 소형 합성본을 실제 스키마로 쓴다."""
    records = []
    for r in rows:
        record = {col: r.get(col) for col in NORMALIZED_FACTS_COLUMNS}
        records.append(record)
    df = pd.DataFrame(records, columns=NORMALIZED_FACTS_COLUMNS)
    df = df.astype(
        {
            "canonical_id": "string",
            "fs_scope": "string",
            "sj_div": "string",
            "fiscal_year": "int64",
            "fiscal_quarter": "Int64",
            "value": "Int64",
            "value_type": "string",
            "rcept_no": "string",
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, engine="pyarrow", index=False)


def reported_row(
    *,
    canonical_id: str,
    sj_div: str,
    fiscal_year: int,
    period_end: date,
    value: int,
    rcept_no: str,
    fs_scope: str = "CFS",
    fiscal_quarter: int | None = None,
    period_start: date | None = None,
) -> dict[str, object]:
    """REPORTED 행 1건(A4 스키마) — 합성 헬퍼."""
    return {
        "canonical_id": canonical_id,
        "fs_scope": fs_scope,
        "sj_div": sj_div,
        "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
        "period_start": period_start,
        "period_end": period_end,
        "value": value,
        "value_type": "REPORTED",
        "rcept_no": rcept_no,
        "rcept_dt": date(fiscal_year + 1, 3, 15),
        "available_from": date(fiscal_year + 1, 3, 16),
        "source_account_id": "",
        "source_account_nm": "",
    }


def make_units() -> list[XbrlUnit]:
    return [XbrlUnit(unit_id="KRW", measure="iso4217:KRW", numerator=None, denominator=None)]
