"""전략 초안 vs 최종 dot-path diff 테스트 (H1 §7, 원문 §9).

DoD 4: 원문 §9의 diff 예시(entry 조건 0.1→0.2, execution.trade_time 변경)가
diff.py로 재현됨을 증명한다.
"""

from typing import Any

from research_backtest.core.hitl.diff import diff_strategies
from research_backtest.core.hitl.models import StrategyModification, StrategyReview


def _mods_by_path(mods: list[StrategyModification]) -> dict[str, StrategyModification]:
    return {m.field_path: m for m in mods}


# ---------------------------------------------------------------------------
# 원문 §9 예시 재현
# ---------------------------------------------------------------------------


def test_reproduces_feedback_section_9_examples() -> None:
    """1804_FEEDBACK.md §9의 두 예시를 정확히 재현한다."""
    draft: dict[str, Any] = {
        "entry": {
            "operating_income_yoy": {"left": "operating_income_yoy", "operator": ">", "right": 0.1}
        },
        "execution": {"signal_time": "close", "trade_time": "same_close"},
    }
    final: dict[str, Any] = {
        "entry": {
            "operating_income_yoy": {"left": "operating_income_yoy", "operator": ">", "right": 0.2}
        },
        "execution": {"signal_time": "close", "trade_time": "next_open"},
    }

    mods = diff_strategies(draft, final, modified_by="user")
    by_path = _mods_by_path(mods)

    assert by_path["entry.operating_income_yoy.right"].draft_value == 0.1
    assert by_path["entry.operating_income_yoy.right"].final_value == 0.2

    assert by_path["execution.trade_time"].draft_value == "same_close"
    assert by_path["execution.trade_time"].final_value == "next_open"

    # signal_time은 바뀌지 않았으므로 diff에 없어야 한다.
    assert "execution.signal_time" not in by_path

    for mod in mods:
        assert mod.modified_by == "user"
        assert mod.reason == ""  # reason은 빈 문자열로 남겨두고 사용자가 채운다.


def test_reproduces_list_style_entry_condition_path() -> None:
    """A5 스타일(entry.all[i])로 중첩된 조건 리스트도 동일하게 재현된다."""
    draft = {
        "entry": {"all": [{"left": "operating_income_yoy", "operator": ">", "right": 0.1}]},
    }
    final = {
        "entry": {"all": [{"left": "operating_income_yoy", "operator": ">", "right": 0.2}]},
    }
    mods = diff_strategies(draft, final, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "entry.all[0].right"
    assert mods[0].draft_value == 0.1
    assert mods[0].final_value == 0.2


# ---------------------------------------------------------------------------
# 값 변경 · 추가 · 삭제 · 리스트 원소 변경
# ---------------------------------------------------------------------------


def test_value_change_is_detected() -> None:
    mods = diff_strategies({"a": 1}, {"a": 2}, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "a"
    assert mods[0].draft_value == 1
    assert mods[0].final_value == 2


def test_field_addition_has_none_draft_value() -> None:
    mods = diff_strategies({"a": 1}, {"a": 1, "b": 2}, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "b"
    assert mods[0].draft_value is None
    assert mods[0].final_value == 2


def test_field_deletion_has_none_final_value() -> None:
    mods = diff_strategies({"a": 1, "b": 2}, {"a": 1}, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "b"
    assert mods[0].draft_value == 2
    assert mods[0].final_value is None


def test_list_element_change_is_detected() -> None:
    draft = {"exit": {"any": [{"type": "stop_loss", "value": -0.1}]}}
    final = {"exit": {"any": [{"type": "stop_loss", "value": -0.15}]}}
    mods = diff_strategies(draft, final, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "exit.any[0].value"
    assert mods[0].draft_value == -0.1
    assert mods[0].final_value == -0.15


def test_list_element_addition_at_tail() -> None:
    draft = {"tickers": ["000660"]}
    final = {"tickers": ["000660", "005930"]}
    mods = diff_strategies(draft, final, modified_by="user")
    assert len(mods) == 1
    assert mods[0].field_path == "tickers[1]"
    assert mods[0].draft_value is None
    assert mods[0].final_value == "005930"


def test_identical_dicts_produce_empty_list() -> None:
    payload = {
        "strategy_name": "demo",
        "entry": {"all": [{"left": "x", "operator": ">", "right": 0.1}]},
        "execution": {"trade_time": "next_open"},
    }
    mods = diff_strategies(payload, dict(payload), modified_by="user")
    assert mods == []


def test_no_changes_when_both_empty() -> None:
    assert diff_strategies({}, {}, modified_by="user") == []


# ---------------------------------------------------------------------------
# 결정적 순서
# ---------------------------------------------------------------------------


def test_result_is_sorted_by_field_path() -> None:
    draft = {"z": 1, "a": 1, "m": 1}
    final = {"z": 2, "a": 2, "m": 2}
    mods = diff_strategies(draft, final, modified_by="user")
    assert [m.field_path for m in mods] == ["a", "m", "z"]


def test_result_order_is_stable_across_calls() -> None:
    draft = {"execution": {"trade_time": "same_close"}, "entry": {"threshold": 0.1}}
    final = {"execution": {"trade_time": "next_open"}, "entry": {"threshold": 0.2}}
    first = [m.field_path for m in diff_strategies(draft, final, modified_by="user")]
    second = [m.field_path for m in diff_strategies(draft, final, modified_by="user")]
    assert first == second == sorted(first)


# ---------------------------------------------------------------------------
# 역할 분리(원문 §18): AI draft와 final strategy 수정 이력이 저장되는지 확인
# ---------------------------------------------------------------------------


def test_diff_output_feeds_directly_into_strategy_review() -> None:
    """diff_strategies의 출력이 그대로 StrategyReview.modifications로 저장된다.

    reason은 diff.py가 빈 문자열로 남기고, CLI·UI 단계에서 사용자가 채운
    뒤 StrategyReview를 구성한다고 가정한다(H1 §7).
    """
    draft = {"entry": {"operating_income_yoy": {"right": 0.1}}}
    final = {"entry": {"operating_income_yoy": {"right": 0.2}}}
    modifications = diff_strategies(draft, final, modified_by="user")
    assert modifications[0].reason == ""

    filled_in = [
        m.model_copy(update={"reason": "10% 증가는 통상적 변동과 구분하기 어려워 기준을 상향했다."})
        for m in modifications
    ]

    review = StrategyReview(
        review_id="REVIEW-1",
        hypothesis_id="HYP-1",
        llm_draft_strategy=draft,
        final_strategy=final,
        modifications=filled_in,
        approval_reason="임계값 상향에 동의해 승인한다.",
        approved_by="user",
        approved_at="2026-07-14T12:00:00+09:00",
    )
    saved_reason = review.modifications[0].reason
    assert review.modifications[0].field_path == "entry.operating_income_yoy.right"
    assert saved_reason == "10% 증가는 통상적 변동과 구분하기 어려워 기준을 상향했다."
    assert review.llm_draft_strategy != review.final_strategy
