"""XBRL 파싱 도메인 모델 (README §9.2~9.5).

원본 QName을 항상 3중으로 보존한다 — ``concept_qname``(문서 선언 prefix 기반,
참고용), ``concept_namespace``(uri), ``concept_local_name``(local). prefix는
문서마다 다를 수 있으므로(같은 uri에 다른 prefix) namespace·local이 정본이다
(README §9.6 "원본 QName 보존", 명세 §2.2).

수치는 float로 즉시 변환하지 않는다 — ``raw_value``(원문 문자열)와
``numeric_value``(:class:`~decimal.Decimal`)를 함께 보존해 정밀 비교를 가능하게
한다(README §9.6 금지 사항). parquet 저장 단계에서만 numeric_value를 float64로
낮춘다(KRW 정수 범위는 float64로 정확, 명세 §2.3).
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


class XbrlDimension(BaseModel):
    """Context의 segment·scenario 내 차원 1개 (README §9.4).

    - explicitMember: ``axis_qname``=dimension 속성, ``member_qname``=요소 텍스트,
      ``typed_member_value``=None
    - typedMember: ``axis_qname``=dimension 속성, ``member_qname``=None,
      ``typed_member_value``=내부 내용 문자열화

    axis_qname·member_qname은 문서에 기재된 prefix 표기(예:
    ``ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis``)를 그대로 보존한다.
    """

    axis_qname: str
    member_qname: str | None
    typed_member_value: str | None


class XbrlContext(BaseModel):
    """보고 맥락 — entity·period·차원 (README §9.3).

    ``period_type``은 ``instant``/``duration``/``forever`` 중 하나다.
    ``entity_scheme``은 identifier의 scheme 속성(예: DART 발급기관 URI)으로,
    README §9.3 모델에는 없으나 저장 스키마(명세 §2.3)가 요구해 보존한다.
    """

    context_id: str
    entity_identifier: str
    entity_scheme: str | None

    period_type: str
    instant_date: str | None
    start_date: str | None
    end_date: str | None

    segment_dimensions: list[XbrlDimension]
    scenario_dimensions: list[XbrlDimension]

    @property
    def dimensions(self) -> list[XbrlDimension]:
        """segment + scenario 차원 전체 (segment 먼저)."""
        return [*self.segment_dimensions, *self.scenario_dimensions]

    @property
    def dimension_count(self) -> int:
        """차원 개수 — 차원 없는 기본 Context 판별용 (README §10.1)."""
        return len(self.segment_dimensions) + len(self.scenario_dimensions)


class XbrlUnit(BaseModel):
    """측정 단위 (README §9.5).

    단일 measure(예: ``iso4217:KRW``, ``xbrli:shares``)면 ``measure``만,
    divide(예: KRW/shares)면 ``numerator``·``denominator``만 채워진다.
    """

    unit_id: str
    measure: str | None
    numerator: str | None
    denominator: str | None


class XbrlFact(BaseModel):
    """재무 수치 1건 (README §9.2).

    ``scale``은 표준 XBRL instance(xbrli)에는 존재하지 않는 속성이다 — 배율은
    iXBRL(inline XBRL)의 ``scale`` 속성 전용이며, DART 정기보고서의 XBRL 원본은
    표준 instance이므로 항상 None이다(README §9.1의 Scale 항목은 iXBRL 확장 대비).
    실제 배율 정보는 ``decimals``와 별개이며 decimals를 배율로 오해하지 않는다
    (README §9.6 금지 사항).
    """

    concept_qname: str
    concept_namespace: str
    concept_local_name: str

    context_id: str
    unit_id: str | None

    raw_value: str | None
    numeric_value: Decimal | None

    decimals: str | None
    scale: int | None
    is_nil: bool

    source_file: str


class ParsedXbrl(BaseModel):
    """instance 문서 1개 이상을 파싱·병합한 결과 (명세 §2).

    복수 instance(연결/별도 분리 제출 등)를 병합할 때 context·unit은 id 기준으로
    선착순 dedupe하고(문서 정렬 순서 고정), fact는 전부 보존하며 각자
    ``source_file``로 출처를 남긴다(명세 §2.1). fact 순서는 (파일명 → 문서 순서)로
    결정적이다.
    """

    facts: list[XbrlFact]
    contexts: list[XbrlContext]
    units: list[XbrlUnit]
