"""전략 초안 vs 최종 승인본의 dot-path diff (H1 §7, 원문 §9).

``dict``·``list``를 재귀적으로 비교해 값이 바뀐(또는 추가·삭제된) 위치마다
dot-path(예: ``entry.all[0].right``, ``execution.trade_time``)를 만들고
:class:`~research_backtest.core.hitl.models.StrategyModification`으로
기록한다. ``reason``은 빈 문자열로 남겨두고 CLI·UI 단계에서 사용자가 채운다.

리스트는 **위치 기준**으로 비교한다(LCS 등 정렬 보정 없음) — 중간 원소가
추가·삭제되면 그 뒤 인덱스가 모두 "변경"으로 잡히는 것이 알려진 한계다.
전략 조건 리스트는 보통 기존 항목의 임계값만 바꾸는 용도이므로 이 단순
비교로 충분하다.
"""

from collections.abc import Mapping

from research_backtest.core.hitl.models import StrategyModification

# 키가 한쪽에만 존재할 때(추가/삭제)를 실제 값 None과 구분하기 위한 내부 센티널.
# 최종 출력에는 절대 노출되지 않는다 — leaf 비교 시점에 None으로 변환한다.
_MISSING = object()


def diff_strategies(
    draft: Mapping[str, object], final: Mapping[str, object], *, modified_by: str
) -> list[StrategyModification]:
    """``draft``와 ``final``의 차이를 dot-path 단위 :class:`StrategyModification` 목록으로 반환한다.

    필드 추가·삭제·값 변경을 모두 포함하며, 없는 쪽의 값은 ``None``으로
    기록한다. 결과는 ``field_path`` 문자열 오름차순으로 정렬되어 결정적이다.
    동일한 ``draft``·``final``이면 빈 목록을 반환한다.
    """
    changes: dict[str, tuple[object | None, object | None]] = {}
    _collect_diff(draft, final, "", changes)
    return [
        StrategyModification(
            field_path=path,
            draft_value=draft_value,
            final_value=final_value,
            reason="",
            modified_by=modified_by,
        )
        for path, (draft_value, final_value) in sorted(changes.items())
    ]


def _collect_diff(
    draft: object,
    final: object,
    path: str,
    changes: dict[str, tuple[object | None, object | None]],
) -> None:
    if isinstance(draft, dict) and isinstance(final, dict):
        for key in sorted(set(draft) | set(final)):
            child_path = f"{path}.{key}" if path else str(key)
            _collect_diff(draft.get(key, _MISSING), final.get(key, _MISSING), child_path, changes)
        return

    if isinstance(draft, list) and isinstance(final, list):
        for i in range(max(len(draft), len(final))):
            child_path = f"{path}[{i}]"
            draft_item = draft[i] if i < len(draft) else _MISSING
            final_item = final[i] if i < len(final) else _MISSING
            _collect_diff(draft_item, final_item, child_path, changes)
        return

    _collect_leaf(draft, final, path, changes)


def _collect_leaf(
    draft: object,
    final: object,
    path: str,
    changes: dict[str, tuple[object | None, object | None]],
) -> None:
    if draft is _MISSING and final is _MISSING:
        return
    draft_value = None if draft is _MISSING else draft
    final_value = None if final is _MISSING else final
    if draft_value != final_value:
        changes[path] = (draft_value, final_value)


__all__ = ["diff_strategies"]
