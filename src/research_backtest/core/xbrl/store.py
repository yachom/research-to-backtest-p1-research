"""파싱 결과를 parquet 4종으로 저장·로드한다 (README §19.5, 명세 §2.3).

``data/normalized/xbrl/{corp_code}/{rcept_no}/`` 아래에 저장한다:

- ``xbrl_facts.parquet``      — fact 1건/행 (numeric_value는 float64로 낮춤)
- ``xbrl_contexts.parquet``   — context 1건/행 (dimension_count 포함)
- ``xbrl_units.parquet``      — unit 1건/행
- ``xbrl_dimensions.parquet`` — context 차원 1건/행 (segment/scenario)

결정성(README M4): 행 순서는 파싱 순서(파일명→문서 순서)를 그대로 보존하고
컬럼·dtype을 고정한다. 동일 입력을 두 번 파싱·저장하면 DataFrame이 동일하다.

numeric_value를 float64로 낮추는 이유: KRW 정수 금액은 |값| < 2^53 범위에서
float64로 정확히 표현된다(SK하이닉스 자산총계 약 1.8e14 « 9.0e15). 정밀 비교가
필요하면 원문 문자열 ``raw_value``를 쓴다(명세 §2.3).
"""

import logging
from pathlib import Path

import pandas as pd

from research_backtest.core.xbrl.models import ParsedXbrl, XbrlContext, XbrlFact, XbrlUnit
from research_backtest.core.xbrl.parser import dimension_rows

logger = logging.getLogger("r2b.xbrl.store")

FACTS_FILENAME = "xbrl_facts.parquet"
CONTEXTS_FILENAME = "xbrl_contexts.parquet"
UNITS_FILENAME = "xbrl_units.parquet"
DIMENSIONS_FILENAME = "xbrl_dimensions.parquet"

FACT_COLUMNS = [
    "concept_qname",
    "concept_namespace",
    "concept_local_name",
    "context_id",
    "unit_id",
    "raw_value",
    "numeric_value",
    "decimals",
    "is_nil",
    "source_file",
]
CONTEXT_COLUMNS = [
    "context_id",
    "entity_identifier",
    "entity_scheme",
    "period_type",
    "instant_date",
    "start_date",
    "end_date",
    "dimension_count",
]
UNIT_COLUMNS = ["unit_id", "measure", "numerator", "denominator"]
DIMENSION_COLUMNS = [
    "context_id",
    "axis_qname",
    "member_qname",
    "typed_member_value",
    "container",
]


def xbrl_normalized_dir(data_dir: Path, corp_code: str, rcept_no: str) -> Path:
    """정규화 저장 경로 — ``{data_dir}/normalized/xbrl/{corp_code}/{rcept_no}``."""
    return data_dir / "normalized" / "xbrl" / corp_code / rcept_no


def facts_dataframe(facts: list[XbrlFact]) -> pd.DataFrame:
    """fact 목록 → xbrl_facts DataFrame (numeric_value float64, is_nil bool)."""
    records = [
        {
            "concept_qname": f.concept_qname,
            "concept_namespace": f.concept_namespace,
            "concept_local_name": f.concept_local_name,
            "context_id": f.context_id,
            "unit_id": f.unit_id,
            "raw_value": f.raw_value,
            "numeric_value": float(f.numeric_value) if f.numeric_value is not None else None,
            "decimals": f.decimals,
            "is_nil": f.is_nil,
            "source_file": f.source_file,
        }
        for f in facts
    ]
    df = pd.DataFrame(records, columns=FACT_COLUMNS)
    df["numeric_value"] = df["numeric_value"].astype("float64")
    df["is_nil"] = df["is_nil"].astype("bool")
    return df


def contexts_dataframe(contexts: list[XbrlContext]) -> pd.DataFrame:
    """context 목록 → xbrl_contexts DataFrame (dimension_count int64)."""
    records = [
        {
            "context_id": c.context_id,
            "entity_identifier": c.entity_identifier,
            "entity_scheme": c.entity_scheme,
            "period_type": c.period_type,
            "instant_date": c.instant_date,
            "start_date": c.start_date,
            "end_date": c.end_date,
            "dimension_count": c.dimension_count,
        }
        for c in contexts
    ]
    df = pd.DataFrame(records, columns=CONTEXT_COLUMNS)
    df["dimension_count"] = df["dimension_count"].astype("int64")
    return df


def units_dataframe(units: list[XbrlUnit]) -> pd.DataFrame:
    """unit 목록 → xbrl_units DataFrame."""
    records = [
        {
            "unit_id": u.unit_id,
            "measure": u.measure,
            "numerator": u.numerator,
            "denominator": u.denominator,
        }
        for u in units
    ]
    return pd.DataFrame(records, columns=UNIT_COLUMNS)


def dimensions_dataframe(contexts: list[XbrlContext]) -> pd.DataFrame:
    """context 차원 → xbrl_dimensions DataFrame (context 순서·segment 먼저)."""
    records = [
        {
            "context_id": context_id,
            "axis_qname": axis_qname,
            "member_qname": member_qname,
            "typed_member_value": typed_member_value,
            "container": container,
        }
        for context_id, axis_qname, member_qname, typed_member_value, container in dimension_rows(
            contexts
        )
    ]
    return pd.DataFrame(records, columns=DIMENSION_COLUMNS)


def store_parsed_xbrl(parsed: ParsedXbrl, out_dir: Path) -> dict[str, Path]:
    """파싱 결과를 parquet 4종으로 저장하고 파일 경로 맵을 반환한다.

    행 순서·컬럼·dtype이 고정되어 동일 입력에 대해 결정적이다(README M4).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = {
        FACTS_FILENAME: facts_dataframe(parsed.facts),
        CONTEXTS_FILENAME: contexts_dataframe(parsed.contexts),
        UNITS_FILENAME: units_dataframe(parsed.units),
        DIMENSIONS_FILENAME: dimensions_dataframe(parsed.contexts),
    }
    paths: dict[str, Path] = {}
    for filename, frame in frames.items():
        path = out_dir / filename
        frame.to_parquet(path, engine="pyarrow", index=False)
        paths[filename] = path
    logger.info(
        "XBRL parquet 저장 완료 dir=%s facts=%d contexts=%d units=%d dims=%d",
        out_dir,
        len(frames[FACTS_FILENAME]),
        len(frames[CONTEXTS_FILENAME]),
        len(frames[UNITS_FILENAME]),
        len(frames[DIMENSIONS_FILENAME]),
    )
    return paths


def load_xbrl_table(out_dir: Path, filename: str) -> pd.DataFrame:
    """저장된 parquet 1종을 DataFrame으로 읽는다 (engine은 설치된 pyarrow로 auto 결정)."""
    return pd.read_parquet(out_dir / filename)
