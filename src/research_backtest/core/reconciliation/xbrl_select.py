"""XBRL fact 선택 — concept·period·scope 매칭 (명세 B3 §3, README §10·§16.4).

A4 정규화 재무의 REPORTED 행 하나(계정·기간·scope)에 대응하는 XBRL 원본
fact를 B2 파싱 산출(:mod:`research_backtest.core.xbrl`)에서 골라낸다. 세 단계로
좁힌다:

1. **concept** — registry ``accepted_concepts``("prefix:Local")를 (namespace
   계열, local)로 해석해 매칭한다. **문서가 선언한 prefix 문자열에 의존하지
   않는다**(같은 uri에 다른 prefix가 올 수 있음, B2 fixture_altprefix) — fact가
   보존한 ``concept_namespace``(uri)와 ``concept_local_name``으로 판정한다.
2. **period** — 대상이 instant(BS)면 context.instant == period_end, duration
   (IS·CIS·CF)이면 (start, end) 정확 일치. A4 값은 원본 semantics(3개월/누적)를
   보존하므로 통상 XBRL context 중 하나와 만난다(명세 §3).
3. **scope** — DATA_NOTES B1+B2 실측 ②의 개정 규칙: 연결/별도는 파일이 아니라
   ``ConsolidatedAndSeparateFinancialStatementsAxis`` **차원**으로만 구분된다.
   ⇒ context의 차원이 **정확히 1개**이고 그 axis local이 위 축, member local이
   CFS→``ConsolidatedMember`` / OFS→``SeparateMember``인 context만 채택한다.
   (README §10.1의 "추가 Dimension이 없는 기본 Context" 규칙은 실 XBRL에 존재하지
   않는다 — 차원 0 context는 Assets가 아니다. DATA_NOTES B1+B2-②.)

namespace 계열 판정(:func:`_namespace_family_matches`)은 uri의 **taxonomy tail**
(마지막 경로 세그먼트)로 한다. 실 DART 네임스페이스는
``.../ifrs-full``·``.../ifrs/dart``·``.../ifrs/dart-gcd``·``.../entity{corp}``라
tail이 각각 ``ifrs-full``·``dart``·``dart-gcd``·``entity…``로 깔끔히 갈린다.
명세 §3의 "namespace에 ifrs 포함" 문언을 그대로 쓰면 dart uri(``.../ifrs/dart``)도
"ifrs 포함"이라 오분류되므로, tail 일치 + ifrs 표준 host(xbrl.ifrs.org) 조합으로
정밀화한다(명세 이탈: 근거는 이 docstring·보고).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import pandas as pd

from research_backtest.core.constants import FsDiv
from research_backtest.core.xbrl.models import ParsedXbrl

# --- scope 축·멤버 상수 (실데이터 확인, DATA_NOTES B1+B2-② / B3 보고) ----------

#: 연결·별도 구분 축의 local name (ifrs-full:ConsolidatedAndSeparate…Axis).
SCOPE_AXIS_LOCAL = "ConsolidatedAndSeparateFinancialStatementsAxis"
#: 연결(CFS) member local — 실측 ``ifrs-full:ConsolidatedMember``.
CONSOLIDATED_MEMBER_LOCAL = "ConsolidatedMember"
#: 별도(OFS) member local — 실측 ``ifrs-full:SeparateMember``.
SEPARATE_MEMBER_LOCAL = "SeparateMember"
#: scope → 채택할 member local.
SCOPE_TO_MEMBER_LOCAL: dict[FsDiv, str] = {
    FsDiv.CFS: CONSOLIDATED_MEMBER_LOCAL,
    FsDiv.OFS: SEPARATE_MEMBER_LOCAL,
}

#: IFRS 표준 taxonomy host — dart uri(``.../ifrs/dart``)와 구분하는 정밀 신호.
_IFRS_HOST = "xbrl.ifrs.org"

# period_type 리터럴 (registry.period_type / XbrlContext.period_type와 정렬)
INSTANT = "instant"
DURATION = "duration"


class SelectionStage(StrEnum):
    """선택이 성공/중단된 단계 — compare.py의 상태 분류 입력(명세 §4)."""

    SELECTED = "SELECTED"  # scope까지 통과, 후보 1개 이상
    NO_CONCEPT = "NO_CONCEPT"  # concept 매칭 0 → MISSING_IN_XBRL
    NO_PERIOD = "NO_PERIOD"  # concept 있으나 period 일치 context 0 → CONTEXT_MISMATCH
    NO_SCOPE = "NO_SCOPE"  # period 맞으나 scope 축 단독 context 0 → SCOPE_MISMATCH


@dataclass(frozen=True)
class ContextInfo:
    """선택에 필요한 context 정보(기간 + 차원 축/멤버 local)."""

    context_id: str
    period_type: str
    instant_date: str | None
    start_date: str | None
    end_date: str | None
    dimensions: tuple[tuple[str, str | None], ...]  # (axis_local, member_local)

    @property
    def dimension_count(self) -> int:
        return len(self.dimensions)


@dataclass(frozen=True)
class XbrlFactView:
    """선택 대상 fact의 최소 뷰 — 정밀 비교는 원문 ``raw_value``를 쓴다(명세 §4)."""

    concept_namespace: str
    concept_local_name: str
    context_id: str
    raw_value: str | None


@dataclass(frozen=True)
class FactSelection:
    """선택 결과 — 채택 후보 + 진단 카운트(명세 §3·§4).

    ``candidates``는 scope까지 통과한 fact들이다: 정확히 1개면 값 비교로,
    2개 이상이면 중복(REQUIRES_REVIEW), 0개면 ``stage``가 왜 비었는지 알려준다.
    """

    stage: SelectionStage
    candidates: tuple[XbrlFactView, ...]
    concept_count: int
    period_count: int


class XbrlIndex:
    """한 보고서(rcept) XBRL의 조회용 인덱스 — B2 parquet 또는 ParsedXbrl에서 구축."""

    def __init__(self, facts: Sequence[XbrlFactView], contexts: dict[str, ContextInfo]) -> None:
        self.facts = tuple(facts)
        self.contexts = contexts

    @classmethod
    def from_frames(
        cls,
        facts_df: pd.DataFrame,
        contexts_df: pd.DataFrame,
        dimensions_df: pd.DataFrame,
    ) -> XbrlIndex:
        """B2 store의 parquet 3종(facts·contexts·dimensions)에서 인덱스를 만든다."""
        dims_by_ctx: dict[str, list[tuple[str, str | None]]] = {}
        for row in dimensions_df.itertuples(index=False):
            axis_local = _local_part(str(row.axis_qname))
            member_qname = getattr(row, "member_qname", None)
            member_local = _local_part(str(member_qname)) if _present(member_qname) else None
            dims_by_ctx.setdefault(str(row.context_id), []).append((axis_local, member_local))

        contexts: dict[str, ContextInfo] = {}
        for row in contexts_df.itertuples(index=False):
            cid = str(row.context_id)
            contexts[cid] = ContextInfo(
                context_id=cid,
                period_type=str(row.period_type),
                instant_date=_str_or_none(getattr(row, "instant_date", None)),
                start_date=_str_or_none(getattr(row, "start_date", None)),
                end_date=_str_or_none(getattr(row, "end_date", None)),
                dimensions=tuple(dims_by_ctx.get(cid, [])),
            )

        facts = [
            XbrlFactView(
                concept_namespace=str(row.concept_namespace),
                concept_local_name=str(row.concept_local_name),
                context_id=str(row.context_id),
                raw_value=_str_or_none(getattr(row, "raw_value", None)),
            )
            for row in facts_df.itertuples(index=False)
        ]
        return cls(facts, contexts)

    @classmethod
    def from_parsed(cls, parsed: ParsedXbrl) -> XbrlIndex:
        """B2 :class:`ParsedXbrl`(인메모리)에서 직접 인덱스를 만든다(테스트·대안 경로)."""
        contexts: dict[str, ContextInfo] = {}
        for ctx in parsed.contexts:
            dims = tuple(
                (
                    _local_part(dim.axis_qname),
                    _local_part(dim.member_qname) if dim.member_qname else None,
                )
                for dim in ctx.dimensions
            )
            contexts[ctx.context_id] = ContextInfo(
                context_id=ctx.context_id,
                period_type=ctx.period_type,
                instant_date=ctx.instant_date,
                start_date=ctx.start_date,
                end_date=ctx.end_date,
                dimensions=dims,
            )
        facts = [
            XbrlFactView(
                concept_namespace=f.concept_namespace,
                concept_local_name=f.concept_local_name,
                context_id=f.context_id,
                raw_value=f.raw_value,
            )
            for f in parsed.facts
        ]
        return cls(facts, contexts)


def select_fact(
    index: XbrlIndex,
    *,
    accepted_concepts: Sequence[str],
    scope: FsDiv,
    period_type: str,
    period_start: str | None,
    period_end: str,
) -> FactSelection:
    """(concept → period → scope) 순으로 좁혀 후보 fact를 고른다(명세 §3).

    ``accepted_concepts``는 registry의 ``"prefix:Local"`` 목록이다. 각 단계가
    비면 그 단계를 ``stage``로 표시해 반환한다(상태 분류는 compare.py).
    """
    concept_facts = [
        f
        for f in index.facts
        if concept_matches(accepted_concepts, f.concept_namespace, f.concept_local_name)
    ]
    if not concept_facts:
        return FactSelection(SelectionStage.NO_CONCEPT, (), 0, 0)

    period_facts = [
        f
        for f in concept_facts
        if (ctx := index.contexts.get(f.context_id)) is not None
        and _period_matches(ctx, period_type, period_start, period_end)
    ]
    if not period_facts:
        return FactSelection(SelectionStage.NO_PERIOD, (), len(concept_facts), 0)

    scope_facts = [
        f
        for f in period_facts
        if (ctx := index.contexts.get(f.context_id)) is not None and _scope_matches(ctx, scope)
    ]
    if not scope_facts:
        return FactSelection(SelectionStage.NO_SCOPE, (), len(concept_facts), len(period_facts))
    return FactSelection(
        SelectionStage.SELECTED, tuple(scope_facts), len(concept_facts), len(period_facts)
    )


# --- concept 매칭 -------------------------------------------------------------


def concept_matches(accepted_concepts: Sequence[str], namespace_uri: str, local_name: str) -> bool:
    """fact의 (namespace uri, local)이 accepted_concepts 중 하나와 계열 일치하는가.

    accepted_concept ``"prefix:Local"``에서 local이 fact local과 정확히 같고,
    prefix가 namespace 계열과 맞으면 True. 콜론이 없으면 local-only로 보아
    namespace 계열은 따지지 않는다.
    """
    for accepted in accepted_concepts:
        prefix, sep, local = accepted.partition(":")
        if not sep:  # 콜론 없음 → 전체가 local
            prefix, local = "", accepted
        if local_name != local:
            continue
        if _namespace_family_matches(prefix, namespace_uri):
            return True
    return False


def _namespace_family_matches(prefix: str, namespace_uri: str) -> bool:
    """registry prefix(``ifrs-full``·``dart`` 등)가 namespace uri 계열과 맞는가.

    taxonomy tail(마지막 경로 세그먼트) 일치를 1차 신호로 쓴다 — 문서 선언
    prefix가 아니라 uri 구조를 보므로 상이 prefix에도 안정적이다. ifrs 계열은
    표준 host(xbrl.ifrs.org)도 인정한다. dart uri(``.../ifrs/dart``)가 "ifrs"를
    부분 포함하는 문제를 tail 기준으로 회피한다(모듈 docstring 근거).
    """
    if not prefix:
        return True
    tail = _namespace_tail(namespace_uri)
    if tail == prefix:
        return True
    return prefix.startswith("ifrs") and _IFRS_HOST in namespace_uri


def _namespace_tail(namespace_uri: str) -> str:
    """uri의 마지막 비어있지 않은 경로 세그먼트(taxonomy 짧은 이름)."""
    return namespace_uri.rstrip("/").rsplit("/", 1)[-1]


# --- period·scope 매칭 --------------------------------------------------------


def _period_matches(
    ctx: ContextInfo, period_type: str, period_start: str | None, period_end: str
) -> bool:
    """대상 기간과 context 기간의 일치(BS=instant, 그 외=duration 정확 일치)."""
    if period_type == INSTANT:
        return ctx.period_type == INSTANT and ctx.instant_date == period_end
    if period_type == DURATION:
        return (
            ctx.period_type == DURATION
            and period_start is not None
            and ctx.start_date == period_start
            and ctx.end_date == period_end
        )
    return False


def _scope_matches(ctx: ContextInfo, scope: FsDiv) -> bool:
    """차원이 정확히 1개이고 연결/별도 축 단독이며 member가 scope와 맞는가(명세 §3)."""
    if ctx.dimension_count != 1:
        return False
    axis_local, member_local = ctx.dimensions[0]
    return axis_local == SCOPE_AXIS_LOCAL and member_local == SCOPE_TO_MEMBER_LOCAL[scope]


# --- 헬퍼 --------------------------------------------------------------------


def _local_part(qname: str) -> str:
    """``prefix:Local`` → ``Local``(콜론 없으면 원문). prefix 표기에 무관하게 local 추출."""
    return qname.rsplit(":", 1)[-1]


def _present(value: object) -> bool:
    """pandas NA/NaN/None이 아닌 실제 값인가."""
    return value is not None and not (isinstance(value, float) and pd.isna(value))


def _str_or_none(value: object) -> str | None:
    """parquet 셀 → 문자열 또는 None(NA/NaN/None은 None)."""
    if not _present(value):
        return None
    return str(value)
