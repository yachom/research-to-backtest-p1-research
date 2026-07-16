"""xbrl_select 단위 테스트 — concept·period·scope 매칭 (명세 §3·§6)."""

from __future__ import annotations

from collections.abc import Callable

from research_backtest.core.constants import FsDiv
from research_backtest.core.reconciliation.xbrl_select import (
    SelectionStage,
    XbrlIndex,
    concept_matches,
    select_fact,
)
from research_backtest.core.xbrl.models import ParsedXbrl

from .conftest import (
    DART_GCD_NS,
    DART_NS,
    IFRS_NS,
    SCOPE_AXIS_QNAME,
    make_context,
    make_fact,
    make_units,
)

CONS = "ifrs-full:ConsolidatedMember"
SEP = "ifrs-full:SeparateMember"


# --- concept 매칭 (namespace 계열, prefix 비의존) -----------------------------


def test_concept_matches_ifrs_full_standard_host() -> None:
    assert concept_matches(["ifrs-full:Assets"], IFRS_NS, "Assets")


def test_concept_matches_ifrs_full_ignores_declared_prefix() -> None:
    # 같은 ifrs uri를 다른 prefix로 선언해도(=fixture_altprefix 케이스) uri로 판정.
    alt_ns = "http://xbrl.ifrs.org/taxonomy/2021-03-24/ifrs-full"
    assert concept_matches(["ifrs-full:Revenue"], alt_ns, "Revenue")


def test_concept_matches_dart_family() -> None:
    assert concept_matches(["dart:OperatingIncomeLoss"], DART_NS, "OperatingIncomeLoss")


def test_dart_gcd_namespace_does_not_match_dart_prefix() -> None:
    # dart-gcd uri는 tail이 'dart-gcd'라 'dart' 계열로 오분류되지 않는다(핵심 경계).
    assert not concept_matches(["dart:ConsolidatedMember"], DART_GCD_NS, "ConsolidatedMember")


def test_dart_uri_contains_ifrs_but_not_matched_as_ifrs_family() -> None:
    # dart uri(.../ifrs/dart)는 'ifrs'를 부분포함하지만 ifrs-full 계열이 아니다.
    assert not concept_matches(["ifrs-full:OperatingIncomeLoss"], DART_NS, "OperatingIncomeLoss")


def test_concept_local_name_must_match_exactly() -> None:
    assert not concept_matches(["ifrs-full:Assets"], IFRS_NS, "AssetsCurrent")


# --- period·scope·후보 수 (select_fact) --------------------------------------


def _consolidated_assets_index(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> XbrlIndex:
    """BS(instant) + CIS(duration)에 연결/별도 축 단독 context를 갖춘 합성 인덱스."""
    contexts = [
        make_context(
            "bs_cons",
            period_type="instant",
            instant="2023-12-31",
            dimensions=[(SCOPE_AXIS_QNAME, CONS)],
        ),
        make_context(
            "bs_sep",
            period_type="instant",
            instant="2023-12-31",
            dimensions=[(SCOPE_AXIS_QNAME, SEP)],
        ),
        # 추가 차원(ComponentsOfEquity)이 붙은 연결 context — scope 단독 조건 불충족.
        make_context(
            "bs_cons_equity",
            period_type="instant",
            instant="2023-12-31",
            dimensions=[
                (SCOPE_AXIS_QNAME, CONS),
                ("ifrs-full:ComponentsOfEquityAxis", "ifrs-full:RetainedEarningsMember"),
            ],
        ),
        # 차원 0 context (Assets 아님이 실데이터지만, 배제 규칙 검증용).
        make_context("bs_bare", period_type="instant", instant="2023-12-31"),
        make_context(
            "is_cons",
            period_type="duration",
            start="2023-01-01",
            end="2023-12-31",
            dimensions=[(SCOPE_AXIS_QNAME, CONS)],
        ),
    ]
    facts = [
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="bs_cons", raw_value="100"),
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="bs_sep", raw_value="90"),
        make_fact(
            namespace=IFRS_NS, local_name="Assets", context_id="bs_cons_equity", raw_value="7"
        ),
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="bs_bare", raw_value="8"),
        make_fact(namespace=IFRS_NS, local_name="Revenue", context_id="is_cons", raw_value="50"),
    ]
    return index_from_parsed(ParsedXbrl(facts=facts, contexts=contexts, units=make_units()))


def test_select_instant_scope_single_candidate(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    index = _consolidated_assets_index(index_from_parsed)
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Assets"],
        scope=FsDiv.CFS,
        period_type="instant",
        period_start=None,
        period_end="2023-12-31",
    )
    assert sel.stage is SelectionStage.SELECTED
    assert len(sel.candidates) == 1
    assert sel.candidates[0].raw_value == "100"  # 연결(ConsolidatedMember) 단독 context


def test_select_separate_scope_picks_separate_member(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    index = _consolidated_assets_index(index_from_parsed)
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Assets"],
        scope=FsDiv.OFS,
        period_type="instant",
        period_start=None,
        period_end="2023-12-31",
    )
    assert sel.stage is SelectionStage.SELECTED
    assert sel.candidates[0].raw_value == "90"


def test_select_duration_exact_match(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    index = _consolidated_assets_index(index_from_parsed)
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Revenue"],
        scope=FsDiv.CFS,
        period_type="duration",
        period_start="2023-01-01",
        period_end="2023-12-31",
    )
    assert sel.stage is SelectionStage.SELECTED
    assert sel.candidates[0].raw_value == "50"


def test_select_no_concept(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    index = _consolidated_assets_index(index_from_parsed)
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Liabilities"],
        scope=FsDiv.CFS,
        period_type="instant",
        period_start=None,
        period_end="2023-12-31",
    )
    assert sel.stage is SelectionStage.NO_CONCEPT
    assert sel.candidates == ()


def test_select_no_period_when_dates_differ(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    index = _consolidated_assets_index(index_from_parsed)
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Revenue"],
        scope=FsDiv.CFS,
        period_type="duration",
        period_start="2023-07-01",  # 존재하지 않는 3개월 구간
        period_end="2023-09-30",
    )
    assert sel.stage is SelectionStage.NO_PERIOD
    assert sel.concept_count == 1


def test_scope_rejects_extra_dimension_and_bare_context(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    # ComponentsOfEquity 차원이 붙은 것(dim=2)과 차원 0짜리는 scope 단독 조건 불충족.
    contexts = [
        make_context(
            "cons_equity",
            period_type="instant",
            instant="2023-12-31",
            dimensions=[
                (SCOPE_AXIS_QNAME, CONS),
                ("ifrs-full:ComponentsOfEquityAxis", "ifrs-full:RetainedEarningsMember"),
            ],
        ),
        make_context("bare", period_type="instant", instant="2023-12-31"),
    ]
    facts = [
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="cons_equity", raw_value="7"),
        make_fact(namespace=IFRS_NS, local_name="Assets", context_id="bare", raw_value="8"),
    ]
    index = index_from_parsed(ParsedXbrl(facts=facts, contexts=contexts, units=make_units()))
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Assets"],
        scope=FsDiv.CFS,
        period_type="instant",
        period_start=None,
        period_end="2023-12-31",
    )
    assert sel.stage is SelectionStage.NO_SCOPE
    assert sel.period_count == 2  # 두 context 모두 기간은 맞지만 scope 단독 아님


def test_select_two_candidates_when_contexts_coincide(
    index_from_parsed: Callable[[ParsedXbrl], XbrlIndex],
) -> None:
    # Q1의 3개월(FQQ)·누적(FQA) context가 같은 (start,end)로 공존 → 후보 2개.
    contexts = [
        make_context(
            "q1_fqq",
            period_type="duration",
            start="2023-01-01",
            end="2023-03-31",
            dimensions=[(SCOPE_AXIS_QNAME, CONS)],
        ),
        make_context(
            "q1_fqa",
            period_type="duration",
            start="2023-01-01",
            end="2023-03-31",
            dimensions=[(SCOPE_AXIS_QNAME, CONS)],
        ),
    ]
    facts = [
        make_fact(namespace=IFRS_NS, local_name="Revenue", context_id="q1_fqq", raw_value="8494"),
        make_fact(namespace=IFRS_NS, local_name="Revenue", context_id="q1_fqa", raw_value="8494"),
    ]
    index = index_from_parsed(ParsedXbrl(facts=facts, contexts=contexts, units=make_units()))
    sel = select_fact(
        index,
        accepted_concepts=["ifrs-full:Revenue"],
        scope=FsDiv.CFS,
        period_type="duration",
        period_start="2023-01-01",
        period_end="2023-03-31",
    )
    assert sel.stage is SelectionStage.SELECTED
    assert len(sel.candidates) == 2
