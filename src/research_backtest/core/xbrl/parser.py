"""XBRL instance 파싱 — namespace 동적 처리 (README §9, §19.5, 명세 §2.2).

prefix를 하드코딩하지 않는다. 각 요소의 QName ``{uri}local``에서 uri·local을
분리하고, concept_qname은 **문서 선언 prefix**로 재구성한다(prefix→uri 매핑은
``start-ns`` 이벤트로 수집). prefix는 문서마다 다를 수 있어 참고용이며,
concept_namespace(uri)·concept_local_name이 정본이다(README §9.6, 명세 §2.2).

Fact = xbrli(instance)·link(linkbase) 등 구조 네임스페이스 밖의, ``contextRef``를
가진 요소다. ``unitRef``가 있으면 수치 fact로 보아 §9.6 순서(쉼표·공백 제거 →
괄호 음수 → Decimal)로 변환하고, unitRef가 없으면(날짜·문자열 등 비수치 fact)
numeric_value는 None으로 둔다 — raw_value는 항상 보존한다.

결정성(README M4): context·unit·fact 모두 문서 순서를 그대로 보존한다.
"""

import logging
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

from research_backtest.core.exceptions import XbrlParseError
from research_backtest.core.xbrl.discovery import find_instance_documents
from research_backtest.core.xbrl.models import (
    ParsedXbrl,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlUnit,
)

logger = logging.getLogger("r2b.xbrl.parser")

XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

XBRL_ROOT_TAG = f"{{{XBRLI_NS}}}xbrl"
CONTEXT_TAG = f"{{{XBRLI_NS}}}context"
UNIT_TAG = f"{{{XBRLI_NS}}}unit"
XSI_NIL_ATTR = f"{{{XSI_NS}}}nil"

_NIL_TRUE = frozenset({"true", "1"})


def parse_extracted(extracted_dir: Path) -> ParsedXbrl:
    """extracted/의 instance 문서 전체를 파싱·병합한다 (명세 §2.1).

    복수 instance는 :func:`find_instance_documents` 정렬 순서(파일명)대로 파싱하고,
    fact는 전부 이어붙이며(각자 source_file 보존), context·unit은 id 기준 선착순
    dedupe한다 — 같은 id가 서로 다른 내용으로 두 번 나오는 경우는 첫 정의를 채택한다
    (DART 단일기업 정기보고서에서는 instance가 통상 1개라 실무상 충돌 없음, 명세 §2.1).

    instance가 하나도 없으면 :class:`XbrlParseError`.
    """
    instances = find_instance_documents(extracted_dir)
    if not instances:
        raise XbrlParseError(f"XBRL instance 문서를 찾지 못했습니다: {extracted_dir}")

    facts: list[XbrlFact] = []
    contexts_by_id: dict[str, XbrlContext] = {}
    units_by_id: dict[str, XbrlUnit] = {}
    for path in instances:
        source_file = path.relative_to(extracted_dir).as_posix()
        parsed = parse_instance(path.read_bytes(), source_file=source_file)
        facts.extend(parsed.facts)
        for ctx in parsed.contexts:
            contexts_by_id.setdefault(ctx.context_id, ctx)
        for unit in parsed.units:
            units_by_id.setdefault(unit.unit_id, unit)
    logger.info(
        "XBRL 파싱 완료 instances=%d facts=%d contexts=%d units=%d",
        len(instances),
        len(facts),
        len(contexts_by_id),
        len(units_by_id),
    )
    return ParsedXbrl(
        facts=facts,
        contexts=list(contexts_by_id.values()),
        units=list(units_by_id.values()),
    )


def parse_instance(data: bytes, *, source_file: str) -> ParsedXbrl:
    """instance XML 1개(bytes)를 파싱한다 (README §9).

    루트가 ``{xbrli}xbrl``가 아니거나 XML 파싱이 실패하면 :class:`XbrlParseError`.
    """
    uri_to_prefix = _collect_uri_prefixes(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as err:
        raise XbrlParseError(f"XBRL XML 파싱 실패 file={source_file!r}: {err}") from err
    if root.tag != XBRL_ROOT_TAG:
        raise XbrlParseError(
            f"루트 요소가 XBRL instance가 아닙니다 file={source_file!r} root={root.tag!r}"
        )

    contexts = [_parse_context(el, source_file) for el in root.findall(CONTEXT_TAG)]
    units = [_parse_unit(el) for el in root.findall(UNIT_TAG)]
    facts = [
        _parse_fact(el, uri_to_prefix, source_file)
        for el in root.iter()
        if "contextRef" in el.attrib
    ]
    return ParsedXbrl(facts=facts, contexts=contexts, units=units)


# --- namespace ---------------------------------------------------------------


def _collect_uri_prefixes(data: bytes) -> dict[str, str]:
    """``start-ns`` 이벤트로 uri→prefix 매핑을 만든다 (명세 §2.2).

    같은 uri에 복수 prefix가 선언될 수 있으므로 첫 non-empty prefix를 유지한다.
    XML이 손상되어도 여기서는 삼키고, 트리 파싱에서 :class:`XbrlParseError`로 잡힌다.
    """
    uri_to_prefix: dict[str, str] = {}
    try:
        for _event, (prefix, uri) in ET.iterparse(BytesIO(data), events=("start-ns",)):
            if uri not in uri_to_prefix or (not uri_to_prefix[uri] and prefix):
                uri_to_prefix[uri] = prefix
    except ET.ParseError:
        return uri_to_prefix
    return uri_to_prefix


def _split_qname(tag: str) -> tuple[str, str]:
    """``{uri}local`` → (uri, local). 네임스페이스가 없으면 uri는 빈 문자열."""
    if tag.startswith("{"):
        uri, _, local = tag[1:].partition("}")
        return uri, local
    return "", tag


# --- fact --------------------------------------------------------------------


def _parse_fact(elem: ET.Element, uri_to_prefix: dict[str, str], source_file: str) -> XbrlFact:
    namespace, local = _split_qname(elem.tag)
    prefix = uri_to_prefix.get(namespace, "")
    concept_qname = f"{prefix}:{local}" if prefix else local

    context_id = elem.get("contextRef", "")
    unit_id = elem.get("unitRef")
    decimals = elem.get("decimals")
    is_nil = (elem.get(XSI_NIL_ATTR) or "").strip().lower() in _NIL_TRUE
    raw_value = None if is_nil else elem.text
    numeric_value = _to_decimal(
        raw_value,
        is_numeric=unit_id is not None,
        source_file=source_file,
        concept_qname=concept_qname,
        context_id=context_id,
    )
    return XbrlFact(
        concept_qname=concept_qname,
        concept_namespace=namespace,
        concept_local_name=local,
        context_id=context_id,
        unit_id=unit_id,
        raw_value=raw_value,
        numeric_value=numeric_value,
        decimals=decimals,
        scale=None,  # 표준 XBRL instance에는 scale 속성이 없다 (models.XbrlFact docstring)
        is_nil=is_nil,
        source_file=source_file,
    )


def _to_decimal(
    raw_value: str | None,
    *,
    is_numeric: bool,
    source_file: str,
    concept_qname: str,
    context_id: str,
) -> Decimal | None:
    """§9.6 순서로 수치를 변환한다 — 비수치(unitRef 없음)·nil·빈 값은 None.

    수치 fact(unitRef 보유)인데 Decimal 변환이 실패하면 :class:`XbrlParseError`
    (원인 파일·concept·context 포함). 값을 float로 즉시 변환하지 않는다(README §9.6).
    """
    if not is_numeric or raw_value is None:
        return None
    cleaned = raw_value.replace(",", "").replace(" ", "").strip()
    if not cleaned:
        return None
    negative = False
    if cleaned.startswith("(") and cleaned.endswith(")"):
        negative = True
        cleaned = cleaned[1:-1].strip()
    try:
        value = Decimal(cleaned)
    except InvalidOperation as err:
        raise XbrlParseError(
            f"XBRL 수치 변환 실패 file={source_file!r} concept={concept_qname!r} "
            f"context={context_id!r} raw={raw_value!r}"
        ) from err
    return -value if negative else value


# --- context -----------------------------------------------------------------


def _parse_context(elem: ET.Element, source_file: str) -> XbrlContext:
    context_id = elem.get("id", "")
    entity = elem.find(f"{{{XBRLI_NS}}}entity")
    identifier = entity.find(f"{{{XBRLI_NS}}}identifier") if entity is not None else None
    entity_identifier = (identifier.text or "").strip() if identifier is not None else ""
    entity_scheme = identifier.get("scheme") if identifier is not None else None

    period_el = elem.find(f"{{{XBRLI_NS}}}period")
    period_type, instant_date, start_date, end_date = _parse_period(
        period_el, context_id=context_id, source_file=source_file
    )

    segment_el = entity.find(f"{{{XBRLI_NS}}}segment") if entity is not None else None
    scenario_el = elem.find(f"{{{XBRLI_NS}}}scenario")
    return XbrlContext(
        context_id=context_id,
        entity_identifier=entity_identifier,
        entity_scheme=entity_scheme,
        period_type=period_type,
        instant_date=instant_date,
        start_date=start_date,
        end_date=end_date,
        segment_dimensions=_parse_dimensions(segment_el),
        scenario_dimensions=_parse_dimensions(scenario_el),
    )


def _parse_period(
    period: ET.Element | None, *, context_id: str, source_file: str
) -> tuple[str, str | None, str | None, str | None]:
    """period 요소 → (period_type, instant_date, start_date, end_date)."""
    if period is None:
        raise XbrlParseError(
            f"context에 period가 없습니다 file={source_file!r} context={context_id!r}"
        )
    instant = period.find(f"{{{XBRLI_NS}}}instant")
    if instant is not None:
        return "instant", _text(instant), None, None
    start = period.find(f"{{{XBRLI_NS}}}startDate")
    end = period.find(f"{{{XBRLI_NS}}}endDate")
    if start is not None or end is not None:
        return "duration", None, _text(start), _text(end)
    if period.find(f"{{{XBRLI_NS}}}forever") is not None:
        return "forever", None, None, None
    raise XbrlParseError(
        f"context period 형식을 해석할 수 없습니다 file={source_file!r} context={context_id!r}"
    )


def _parse_dimensions(container: ET.Element | None) -> list[XbrlDimension]:
    """segment·scenario 내 explicitMember·typedMember를 XbrlDimension 목록으로."""
    if container is None:
        return []
    dimensions: list[XbrlDimension] = []
    for child in container:
        if child.tag == f"{{{XBRLDI_NS}}}explicitMember":
            dimensions.append(
                XbrlDimension(
                    axis_qname=(child.get("dimension") or "").strip(),
                    member_qname=_text(child),
                    typed_member_value=None,
                )
            )
        elif child.tag == f"{{{XBRLDI_NS}}}typedMember":
            dimensions.append(
                XbrlDimension(
                    axis_qname=(child.get("dimension") or "").strip(),
                    member_qname=None,
                    typed_member_value=_stringify_typed_member(child),
                )
            )
    return dimensions


def _stringify_typed_member(elem: ET.Element) -> str:
    """typedMember 내부 내용을 네임스페이스 노이즈 없이 결정적으로 문자열화한다.

    자식 요소가 있으면 ``<local>내용</local>`` 형태로 직렬화하고(prefix 제거),
    없으면 텍스트를 그대로 쓴다. 실측 DART 데이터에는 typedMember가 없어(전부
    explicitMember) 주로 fixture 검증용이지만, 표준 대비 지원한다.
    """
    children = list(elem)
    if children:
        return "".join(_stringify_element(child) for child in children)
    return (elem.text or "").strip()


def _stringify_element(elem: ET.Element) -> str:
    local = _split_qname(elem.tag)[1]
    inner = (elem.text or "").strip() + "".join(_stringify_element(c) for c in elem)
    return f"<{local}>{inner}</{local}>"


# --- unit --------------------------------------------------------------------


def _parse_unit(elem: ET.Element) -> XbrlUnit:
    unit_id = elem.get("id", "")
    divide = elem.find(f"{{{XBRLI_NS}}}divide")
    if divide is not None:
        numerator = divide.find(f"{{{XBRLI_NS}}}unitNumerator/{{{XBRLI_NS}}}measure")
        denominator = divide.find(f"{{{XBRLI_NS}}}unitDenominator/{{{XBRLI_NS}}}measure")
        return XbrlUnit(
            unit_id=unit_id,
            measure=None,
            numerator=_text(numerator),
            denominator=_text(denominator),
        )
    measure = elem.find(f"{{{XBRLI_NS}}}measure")
    return XbrlUnit(unit_id=unit_id, measure=_text(measure), numerator=None, denominator=None)


def _text(elem: ET.Element | None) -> str | None:
    """요소 텍스트를 strip해 반환 — None이거나 빈 문자열이면 None."""
    if elem is None:
        return None
    stripped = (elem.text or "").strip()
    return stripped or None


def dimension_rows(
    contexts: Sequence[XbrlContext],
) -> list[tuple[str, str, str | None, str | None, str]]:
    """context 목록을 dimension parquet 행(명세 §2.3)으로 평탄화한다.

    반환 튜플: (context_id, axis_qname, member_qname, typed_member_value, container).
    container는 ``segment``/``scenario``. 순서는 context 순서 → segment 먼저.
    """
    rows: list[tuple[str, str, str | None, str | None, str]] = []
    for ctx in contexts:
        for container, dims in (
            ("segment", ctx.segment_dimensions),
            ("scenario", ctx.scenario_dimensions),
        ):
            for dim in dims:
                rows.append(
                    (
                        ctx.context_id,
                        dim.axis_qname,
                        dim.member_qname,
                        dim.typed_member_value,
                        container,
                    )
                )
    return rows
