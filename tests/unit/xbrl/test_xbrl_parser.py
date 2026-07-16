"""XBRL 파싱 단위 테스트 (README §9, 명세 §2.2) — 손계산 대조, 오프라인."""

from decimal import Decimal
from pathlib import Path

import pytest

from research_backtest.core.exceptions import XbrlParseError
from research_backtest.core.xbrl.models import ParsedXbrl, XbrlFact
from research_backtest.core.xbrl.parser import parse_extracted, parse_instance

IFRS_FULL_NS = "http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full"
EXT_NS = "http://example.com/entity/ext"


@pytest.fixture
def parsed_standard(standard_instance_bytes: bytes) -> ParsedXbrl:
    return parse_instance(standard_instance_bytes, source_file="fixture_standard.xbrl")


def _fact(parsed: ParsedXbrl, local_name: str, context_id: str) -> XbrlFact:
    return next(
        f for f in parsed.facts if f.concept_local_name == local_name and f.context_id == context_id
    )


# --- fact 수·필드 (손계산 대조) ----------------------------------------------


def test_fact_context_unit_counts(parsed_standard: ParsedXbrl) -> None:
    assert len(parsed_standard.facts) == 11
    assert len(parsed_standard.contexts) == 6
    assert len(parsed_standard.units) == 4


def test_source_file_recorded_on_every_fact(parsed_standard: ParsedXbrl) -> None:
    assert {f.source_file for f in parsed_standard.facts} == {"fixture_standard.xbrl"}


def test_qname_namespace_local_split(parsed_standard: ParsedXbrl) -> None:
    assets = _fact(parsed_standard, "Assets", "c_dimless_instant")
    assert assets.concept_qname == "ifrs-full:Assets"
    assert assets.concept_namespace == IFRS_FULL_NS
    assert assets.concept_local_name == "Assets"
    # 확장 네임스페이스 fact도 uri·local 분리 보존
    ext = _fact(parsed_standard, "CustomAdjustment", "c_scen")
    assert ext.concept_qname == "ext:CustomAdjustment"
    assert ext.concept_namespace == EXT_NS


# --- Numeric 변환 (README §9.6) ----------------------------------------------


def test_comma_stripping_and_decimal(parsed_standard: ParsedXbrl) -> None:
    assets = _fact(parsed_standard, "Assets", "c_dimless_instant")
    assert assets.raw_value == "123,456,000,000"  # 원문 보존(쉼표 유지)
    assert assets.numeric_value == Decimal("123456000000")
    assert isinstance(assets.numeric_value, Decimal)  # float 즉시 변환 금지
    assert assets.decimals == "-6"
    assert assets.unit_id == "KRW"


def test_parenthesis_negative(parsed_standard: ParsedXbrl) -> None:
    profit = _fact(parsed_standard, "ProfitLoss", "c_dur")
    assert profit.raw_value == "(1,234,567)"
    assert profit.numeric_value == Decimal("-1234567")


def test_signed_negative(parsed_standard: ParsedXbrl) -> None:
    adj = _fact(parsed_standard, "CustomAdjustment", "c_scen")
    assert adj.numeric_value == Decimal("-500000")


def test_decimals_inf_preserved_as_string(parsed_standard: ParsedXbrl) -> None:
    shares = _fact(parsed_standard, "NumberOfSharesOutstanding", "c_dimless_instant")
    assert shares.decimals == "INF"  # 'INF'는 문자열 그대로 (배율로 오해 금지)
    assert shares.numeric_value == Decimal("728002365")


def test_nil_fact(parsed_standard: ParsedXbrl) -> None:
    nil = _fact(parsed_standard, "CashAndCashEquivalents", "c_dimless_instant")
    assert nil.is_nil is True
    assert nil.raw_value is None
    assert nil.numeric_value is None  # 빈 값과 0을 구분(README §9.6)


def test_non_numeric_fact_without_unit(parsed_standard: ParsedXbrl) -> None:
    note = _fact(parsed_standard, "FilingNote", "c_typed")
    assert note.unit_id is None
    assert note.numeric_value is None  # 비수치(날짜·문자열) — 변환 시도 안 함
    assert note.raw_value == "2024.03.15 정정 제출"  # raw는 항상 보존
    assert note.is_nil is False


def test_scale_is_none_for_standard_instance(parsed_standard: ParsedXbrl) -> None:
    # 표준 XBRL instance에는 scale 속성이 없다 (iXBRL 전용) → 항상 None
    assert all(f.scale is None for f in parsed_standard.facts)


# --- 동일 concept·복수 context 보존 (README M4) ------------------------------


def test_same_concept_multiple_contexts_preserved(parsed_standard: ParsedXbrl) -> None:
    assets = [f for f in parsed_standard.facts if f.concept_local_name == "Assets"]
    assert len(assets) == 2
    by_ctx = {f.context_id: f.numeric_value for f in assets}
    assert by_ctx == {
        "c_dimless_instant": Decimal("123456000000"),
        "c_seg_consol": Decimal("999000000000"),
    }


# --- Context·Period·Entity (README §9.3) -------------------------------------


def test_every_fact_context_resolves(parsed_standard: ParsedXbrl) -> None:
    context_ids = {c.context_id for c in parsed_standard.contexts}
    assert all(f.context_id in context_ids for f in parsed_standard.facts)


def test_entity_identifier_and_scheme(parsed_standard: ParsedXbrl) -> None:
    ctx = next(c for c in parsed_standard.contexts if c.context_id == "c_dimless_instant")
    assert ctx.entity_identifier == "00164779"
    assert ctx.entity_scheme == "http://dart.fss.or.kr/ifrs/CIK"


def test_period_types(parsed_standard: ParsedXbrl) -> None:
    by_id = {c.context_id: c for c in parsed_standard.contexts}
    assert by_id["c_dimless_instant"].period_type == "instant"
    assert by_id["c_dimless_instant"].instant_date == "2024-12-31"
    assert by_id["c_dur"].period_type == "duration"
    assert by_id["c_dur"].start_date == "2024-01-01"
    assert by_id["c_dur"].end_date == "2024-12-31"
    assert by_id["c_forever"].period_type == "forever"


# --- Dimension (README §9.4) -------------------------------------------------


def test_explicit_member_in_segment(parsed_standard: ParsedXbrl) -> None:
    ctx = next(c for c in parsed_standard.contexts if c.context_id == "c_seg_consol")
    assert ctx.dimension_count == 1
    dim = ctx.segment_dimensions[0]
    assert dim.axis_qname == "ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis"
    assert dim.member_qname == "ifrs-full:ConsolidatedMember"
    assert dim.typed_member_value is None


def test_explicit_member_in_scenario(parsed_standard: ParsedXbrl) -> None:
    ctx = next(c for c in parsed_standard.contexts if c.context_id == "c_scen")
    assert len(ctx.scenario_dimensions) == 1
    dim = ctx.scenario_dimensions[0]
    assert dim.axis_qname == "ifrs-full:OperatingSegmentsAxis"
    assert dim.member_qname == "ext:SemiconductorSegmentMember"


def test_typed_member(parsed_standard: ParsedXbrl) -> None:
    ctx = next(c for c in parsed_standard.contexts if c.context_id == "c_typed")
    dim = ctx.segment_dimensions[0]
    assert dim.axis_qname == "ext:BondByNameAxis"
    assert dim.member_qname is None
    assert dim.typed_member_value == "<BondName>GlobalBond2ndUnsecured</BondName>"


# --- Unit (README §9.5) ------------------------------------------------------


def test_units(parsed_standard: ParsedXbrl) -> None:
    by_id = {u.unit_id: u for u in parsed_standard.units}
    assert by_id["KRW"].measure == "iso4217:KRW"
    assert by_id["SHARES"].measure == "xbrli:shares"
    # divide unit — numerator/denominator만 채워지고 measure는 None
    krwps = by_id["KRWPS"]
    assert krwps.measure is None
    assert krwps.numerator == "iso4217:KRW"
    assert krwps.denominator == "xbrli:shares"


# --- Namespace 동적 처리 (명세 §2.2) ----------------------------------------


def test_alt_prefix_same_namespace_different_qname(
    standard_instance_bytes: bytes, altprefix_instance_bytes: bytes
) -> None:
    std = parse_instance(standard_instance_bytes, source_file="std.xbrl")
    alt = parse_instance(altprefix_instance_bytes, source_file="alt.xbrl")
    std_assets = next(f for f in std.facts if f.concept_local_name == "Assets")
    alt_assets = next(f for f in alt.facts if f.concept_local_name == "Assets")
    # 같은 uri → concept_namespace 동일, prefix만 달라 concept_qname은 다르다
    assert std_assets.concept_namespace == alt_assets.concept_namespace == IFRS_FULL_NS
    assert std_assets.concept_qname == "ifrs-full:Assets"
    assert alt_assets.concept_qname == "ifrs:Assets"


# --- 복수 instance 병합 (명세 §2.1) ------------------------------------------


def test_parse_extracted_merges_multiple_instances(
    standard_instance_bytes: bytes, altprefix_instance_bytes: bytes, tmp_path: Path
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "a.xbrl").write_bytes(standard_instance_bytes)
    (extracted / "b.xbrl").write_bytes(altprefix_instance_bytes)

    parsed = parse_extracted(extracted)
    # fact 전부 보존(11 + 1), 각자 source_file
    assert len(parsed.facts) == 12
    assert {f.source_file for f in parsed.facts} == {"a.xbrl", "b.xbrl"}
    # context·unit은 id 기준 선착순 dedupe (양쪽 다 KRW unit, d1/c_* context)
    assert len(parsed.units) == len({u.unit_id for u in parsed.units})


def test_parse_extracted_without_instance_raises(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "only.txt").write_bytes(b"nothing here")
    with pytest.raises(XbrlParseError):
        parse_extracted(extracted)


# --- 오류 처리 (명세 §2.2) ---------------------------------------------------


def test_numeric_conversion_failure_raises_with_context() -> None:
    # unitRef가 있는(수치) fact인데 값이 숫자가 아니면 XbrlParseError
    bad = (
        b'<?xml version="1.0"?>'
        b'<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"'
        b' xmlns:iso4217="http://www.xbrl.org/2003/iso4217"'
        b' xmlns:ifrs-full="http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full">'
        b'<xbrli:context id="c1"><xbrli:entity>'
        b'<xbrli:identifier scheme="s">00164779</xbrli:identifier></xbrli:entity>'
        b"<xbrli:period><xbrli:instant>2024-12-31</xbrli:instant></xbrli:period></xbrli:context>"
        b'<xbrli:unit id="KRW"><xbrli:measure>iso4217:KRW</xbrli:measure></xbrli:unit>'
        b'<ifrs-full:Assets contextRef="c1" unitRef="KRW">not-a-number</ifrs-full:Assets>'
        b"</xbrli:xbrl>"
    )
    with pytest.raises(XbrlParseError, match="수치 변환 실패"):
        parse_instance(bad, source_file="bad.xbrl")


def test_root_not_instance_raises() -> None:
    not_instance = (
        b'<?xml version="1.0"?><link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>'
    )
    with pytest.raises(XbrlParseError, match="instance"):
        parse_instance(not_instance, source_file="linkbase.xml")


def test_malformed_xml_raises() -> None:
    with pytest.raises(XbrlParseError):
        parse_instance(b"<xbrli:xbrl not closed", source_file="broken.xbrl")
