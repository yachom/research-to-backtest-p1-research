"""significance_score 결정적 수식 경계 테스트 (명세 W3a-E1 §3.3 DoD).

요건: ① [0,1] 클램프 ② |change_rate| 단조 증가 ③ 부호 전환 가점 ④ 최근성 가중.
"""

from datetime import date

import pytest

from research_backtest.research.evidence.builder import _significance

_AS_OF = date(2025, 12, 31)


def test_clamped_to_unit_interval_extremes() -> None:
    high = _significance(
        1e9, sign_change=True, period_end=date(2025, 12, 30), as_of=_AS_OF, lookback_years=5
    )
    assert 0.0 <= high <= 1.0
    # change_rate None + 부호 전환 없음 + 아주 오래된 기간 → 0으로 클램프
    low = _significance(
        None, sign_change=False, period_end=date(1990, 1, 1), as_of=_AS_OF, lookback_years=5
    )
    assert low == 0.0


def test_monotonic_in_change_rate_magnitude() -> None:
    def s(rate: float) -> float:
        return _significance(
            rate, sign_change=False, period_end=date(2025, 6, 30), as_of=_AS_OF, lookback_years=5
        )

    assert s(0.05) < s(0.5) < s(5.0) < s(50.0)
    # 부호와 무관하게 크기만 반영
    assert s(0.5) == pytest.approx(
        _significance(
            -0.5, sign_change=False, period_end=date(2025, 6, 30), as_of=_AS_OF, lookback_years=5
        )
    )


def test_sign_change_adds_fixed_bonus() -> None:
    period_end = date(2025, 6, 30)
    with_turn = _significance(
        0.3, sign_change=True, period_end=period_end, as_of=_AS_OF, lookback_years=5
    )
    without = _significance(
        0.3, sign_change=False, period_end=period_end, as_of=_AS_OF, lookback_years=5
    )
    assert with_turn > without
    assert with_turn == pytest.approx(without + 0.2)


def test_recency_weight_prefers_closer_periods() -> None:
    recent = _significance(
        0.3, sign_change=False, period_end=date(2025, 9, 30), as_of=_AS_OF, lookback_years=5
    )
    older = _significance(
        0.3, sign_change=False, period_end=date(2022, 9, 30), as_of=_AS_OF, lookback_years=5
    )
    assert recent > older


def test_none_change_rate_is_zero_magnitude() -> None:
    # change_rate None이면 magnitude 성분은 0 — 최근성만 반영
    only_recency = _significance(
        None, sign_change=False, period_end=date(2025, 12, 30), as_of=_AS_OF, lookback_years=5
    )
    with_magnitude = _significance(
        1.0, sign_change=False, period_end=date(2025, 12, 30), as_of=_AS_OF, lookback_years=5
    )
    assert with_magnitude > only_recency
