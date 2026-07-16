"""XBRL parquet 저장·로드 단위 테스트 (README §19.5, 명세 §2.3) — 결정성 포함."""

from pathlib import Path

import pandas as pd
import pytest

from research_backtest.core.xbrl.models import ParsedXbrl
from research_backtest.core.xbrl.parser import parse_instance
from research_backtest.core.xbrl.store import (
    CONTEXT_COLUMNS,
    CONTEXTS_FILENAME,
    DIMENSION_COLUMNS,
    DIMENSIONS_FILENAME,
    FACT_COLUMNS,
    FACTS_FILENAME,
    UNIT_COLUMNS,
    UNITS_FILENAME,
    load_xbrl_table,
    store_parsed_xbrl,
)


@pytest.fixture
def parsed_standard(standard_instance_bytes: bytes) -> ParsedXbrl:
    return parse_instance(standard_instance_bytes, source_file="fixture_standard.xbrl")


def test_round_trip_columns_and_rowcount(parsed_standard: ParsedXbrl, tmp_path: Path) -> None:
    store_parsed_xbrl(parsed_standard, tmp_path)

    facts = load_xbrl_table(tmp_path, FACTS_FILENAME)
    contexts = load_xbrl_table(tmp_path, CONTEXTS_FILENAME)
    units = load_xbrl_table(tmp_path, UNITS_FILENAME)
    dims = load_xbrl_table(tmp_path, DIMENSIONS_FILENAME)

    assert list(facts.columns) == FACT_COLUMNS
    assert list(contexts.columns) == CONTEXT_COLUMNS
    assert list(units.columns) == UNIT_COLUMNS
    assert list(dims.columns) == DIMENSION_COLUMNS

    assert len(facts) == 11
    assert len(contexts) == 6
    assert len(units) == 4
    assert len(dims) == 3  # c_seg_consol·c_scen·c_typed 각 1개


def test_facts_numeric_float64_and_raw_preserved(
    parsed_standard: ParsedXbrl, tmp_path: Path
) -> None:
    store_parsed_xbrl(parsed_standard, tmp_path)
    facts = load_xbrl_table(tmp_path, FACTS_FILENAME)

    assert facts["numeric_value"].dtype == "float64"
    assert facts["is_nil"].dtype == "bool"

    assets = facts[
        (facts["concept_local_name"] == "Assets") & (facts["context_id"] == "c_dimless_instant")
    ].iloc[0]
    assert assets["numeric_value"] == 123456000000.0  # float64로 정확
    assert assets["raw_value"] == "123,456,000,000"  # 원문 문자열 보존(정밀 비교용)

    # nil fact는 numeric_value NaN, raw_value None
    nil = facts[facts["concept_local_name"] == "CashAndCashEquivalents"].iloc[0]
    assert pd.isna(nil["numeric_value"])
    assert nil["raw_value"] is None
    assert bool(nil["is_nil"]) is True


def test_contexts_dimension_count_int(parsed_standard: ParsedXbrl, tmp_path: Path) -> None:
    store_parsed_xbrl(parsed_standard, tmp_path)
    contexts = load_xbrl_table(tmp_path, CONTEXTS_FILENAME)
    assert contexts["dimension_count"].dtype == "int64"
    by_id = contexts.set_index("context_id")["dimension_count"].to_dict()
    assert by_id["c_dimless_instant"] == 0
    assert by_id["c_seg_consol"] == 1


def test_dimensions_container_labels(parsed_standard: ParsedXbrl, tmp_path: Path) -> None:
    store_parsed_xbrl(parsed_standard, tmp_path)
    dims = load_xbrl_table(tmp_path, DIMENSIONS_FILENAME)
    containers = dims.set_index("context_id")["container"].to_dict()
    assert containers["c_seg_consol"] == "segment"
    assert containers["c_scen"] == "scenario"
    # typedMember 행은 member_qname 없이 typed_member_value 보유
    typed = dims[dims["context_id"] == "c_typed"].iloc[0]
    assert typed["member_qname"] is None
    assert typed["typed_member_value"] == "<BondName>GlobalBond2ndUnsecured</BondName>"


def test_determinism_dataframe_equal(standard_instance_bytes: bytes, tmp_path: Path) -> None:
    # 동일 입력을 두 번 파싱·저장 → parquet DataFrame이 동일(README M4 결정성)
    first = parse_instance(standard_instance_bytes, source_file="fixture_standard.xbrl")
    second = parse_instance(standard_instance_bytes, source_file="fixture_standard.xbrl")
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"
    store_parsed_xbrl(first, out1)
    store_parsed_xbrl(second, out2)

    for filename in (FACTS_FILENAME, CONTEXTS_FILENAME, UNITS_FILENAME, DIMENSIONS_FILENAME):
        df1 = load_xbrl_table(out1, filename)
        df2 = load_xbrl_table(out2, filename)
        pd.testing.assert_frame_equal(df1, df2)


def test_empty_dimensions_preserves_schema(altprefix_instance_bytes: bytes, tmp_path: Path) -> None:
    # 차원이 하나도 없는 instance → 빈 dimensions parquet도 스키마 유지
    parsed = parse_instance(altprefix_instance_bytes, source_file="alt.xbrl")
    store_parsed_xbrl(parsed, tmp_path)
    dims = load_xbrl_table(tmp_path, DIMENSIONS_FILENAME)
    assert list(dims.columns) == DIMENSION_COLUMNS
    assert len(dims) == 0
