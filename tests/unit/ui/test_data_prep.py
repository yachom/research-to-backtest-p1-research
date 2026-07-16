"""데이터 준비 오케스트레이션 단위테스트 (docs/specs/W3d-ui-data-prep.md §4).

collector들(collect_financials·collect_market_data·build_financial_datasets·
download_xbrl_filings·reconcile_all·find_periodic_filings)은 전부 monkeypatch한다 —
실호출 금지(명세 §4, tests/unit/test_cli_collect_financials.py·
test_cli_collect_market.py의 monkeypatch 관례와 동일하게 ``actions`` 모듈
네임스페이스에 patch한다). ``load_dart_config``·``load_market_config``도
patch해 configs/*.yaml 실 파일에 의존하지 않는다.

여기서 검증하는 것은 오케스트레이션(``plan_data_preparation``·
``run_preparation_step``·``execute_data_preparation``)과 §3 산식이다 —
Streamlit 렌더링은 test_streamlit_app.py의 AppTest가 담당한다. 픽스처 팩토리
(corp·collector 반환값)는 conftest.py를 공유한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from research_backtest.app.ui import actions
from research_backtest.core.config import DartConfig, MarketConfig, Settings
from research_backtest.core.dart.financial_api import CollectionSummary
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.market.collector import MarketCollectionSummary

from .conftest import (
    CORP_CODE,
    SK_HYNIX,
    STOCK_CODE,
    UNLISTED_CORP,
    make_build_report,
    make_collection_summary,
    make_filing,
    make_market_summary,
    make_reconciliation_report,
    make_xbrl_outcomes,
    mark_financials_ready,
    mark_market_ready,
)


def _settings(tmp_path: Path) -> Settings:
    """DART 키만 채운 격리 Settings — KRX 키는 기본 빈 값(부분 수집 케이스 재사용)."""
    return Settings(
        _env_file=None,
        dart_api_key="test-dart-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
    )


# ---------------------------------------------------------------------------
# plan_data_preparation — 계획 구성·순서·옵션 on/off·경계값 (명세 §4)
# ---------------------------------------------------------------------------


def test_plan_all_ready_returns_empty_plan(tmp_path: Path) -> None:
    """준비 완료 상태 → 계획 0단계 — 화면은 이 경우 패널을 띄우지 않는다."""
    settings = _settings(tmp_path)
    mark_financials_ready(settings, CORP_CODE)
    mark_market_ready(settings, STOCK_CODE)

    plan = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=False, settings=settings
    )

    assert plan.steps == []
    assert plan.total_estimate_seconds == 0.0


def test_plan_orders_all_steps_when_nothing_ready(tmp_path: Path) -> None:
    """미비 상태 + XBRL 옵션 on → 5단계가 고정 순서로 구성된다."""
    settings = _settings(tmp_path)

    plan = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=True, settings=settings
    )

    assert [step.key for step in plan.steps] == [
        "financials",
        "market",
        "build",
        "xbrl",
        "reconcile",
    ]
    assert all(step.estimate_seconds > 0 for step in plan.steps)


def test_plan_excludes_market_when_already_ready(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mark_market_ready(settings, STOCK_CODE)

    plan = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=False, settings=settings
    )

    assert [step.key for step in plan.steps] == ["financials", "build"]


def test_plan_excludes_financials_and_build_when_already_ready(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mark_financials_ready(settings, CORP_CODE)

    plan = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=False, settings=settings
    )

    assert [step.key for step in plan.steps] == ["market"]


def test_plan_include_xbrl_toggle_adds_two_steps(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    mark_financials_ready(settings, CORP_CODE)
    mark_market_ready(settings, STOCK_CODE)

    plan_off = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=False, settings=settings
    )
    plan_on = actions.plan_data_preparation(
        SK_HYNIX, from_year=2015, to_year=2025, include_xbrl=True, settings=settings
    )

    assert plan_off.steps == []
    assert [step.key for step in plan_on.steps] == ["xbrl", "reconcile"]


def test_plan_rejects_from_year_after_to_year(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(DataValidationError, match="시작 연도"):
        actions.plan_data_preparation(
            SK_HYNIX, from_year=2020, to_year=2015, include_xbrl=False, settings=settings
        )


def test_plan_rejects_from_year_before_min_supported(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(DataValidationError, match="2015"):
        actions.plan_data_preparation(
            SK_HYNIX, from_year=2010, to_year=2020, include_xbrl=False, settings=settings
        )


def test_plan_rejects_unlisted_corp(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    with pytest.raises(DataValidationError, match="비상장"):
        actions.plan_data_preparation(
            UNLISTED_CORP, from_year=2015, to_year=2020, include_xbrl=False, settings=settings
        )


# ---------------------------------------------------------------------------
# §3 산식 — 연수에 단조 증가, 경계값 (명세 §4)
# ---------------------------------------------------------------------------


def test_estimate_financials_seconds_monotonic_and_boundary() -> None:
    single_year = actions._estimate_financials_seconds(2020, 2020)
    ten_years = actions._estimate_financials_seconds(2015, 2025)

    assert single_year > 0
    assert ten_years > single_year
    # R = 1(연) x 4(보고서) x 2(scope) = 8건.
    expected = 8 * (
        actions._DART_MIN_INTERVAL_ESTIMATE_SECONDS + actions._DART_AVG_RESPONSE_ESTIMATE_SECONDS
    )
    assert single_year == pytest.approx(expected)


def test_estimate_market_seconds_monotonic() -> None:
    assert actions._estimate_market_seconds(2015, 2025) > actions._estimate_market_seconds(
        2015, 2015
    )
    # 연수=1이어도 로그인 오버헤드는 항상 더해진다.
    assert (
        actions._estimate_market_seconds(2020, 2020)
        >= actions._MARKET_LOGIN_OVERHEAD_ESTIMATE_SECONDS
    )


def test_estimate_xbrl_filing_count_boundary_and_monotonic() -> None:
    assert actions._estimate_xbrl_filing_count(2020, 2020) == (1 + 1) * 4
    assert actions._estimate_xbrl_filing_count(2015, 2025) > actions._estimate_xbrl_filing_count(
        2015, 2015
    )


def test_estimate_reconcile_seconds_monotonic_and_floor() -> None:
    floor = actions._estimate_reconcile_seconds(2020, 2020)
    assert floor >= actions._RECONCILE_BASE_ESTIMATE_SECONDS
    assert actions._estimate_reconcile_seconds(2015, 2025) > floor


# ---------------------------------------------------------------------------
# run_preparation_step — 단계별 core 조립 (collector 전부 monkeypatch, 명세 §4)
# ---------------------------------------------------------------------------


def test_run_preparation_step_financials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    summary = make_collection_summary(count=8)
    calls: list[tuple[str, int, int]] = []

    def fake_collect(
        client: object, corp_code: str, *, from_year: int, to_year: int, **_: object
    ) -> CollectionSummary:
        calls.append((corp_code, from_year, to_year))
        return summary

    monkeypatch.setattr(actions, "collect_financials", fake_collect)
    step = actions.PrepStep(key="financials", label="① 재무 데이터 수집", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "재무제표 수집 완료" in result
    assert "8건" in result
    assert calls == [(CORP_CODE, 2015, 2025)]


def test_run_preparation_step_market(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_market_config", lambda: MarketConfig())
    monkeypatch.setattr(actions, "collect_market_data", lambda *a, **k: make_market_summary())
    step = actions.PrepStep(key="market", label="② 시장 데이터 수집", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "시장 데이터 수집 완료" in result
    assert "부분 수집" not in result


def test_run_preparation_step_market_partial_mode_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """KRX 자격증명 없음 → collect_market_data가 SKIPPED_NO_AUTH를 반환하는 케이스의 요약."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_market_config", lambda: MarketConfig())
    monkeypatch.setattr(
        actions,
        "collect_market_data",
        lambda *a, **k: make_market_summary(skipped_no_auth=True),
    )
    step = actions.PrepStep(key="market", label="② 시장 데이터 수집", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "부분 수집" in result
    assert "KRX 자격증명 없음" in result


def test_run_preparation_step_build(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(
        actions, "build_financial_datasets", lambda *a, **k: make_build_report(fact_count=321)
    )
    step = actions.PrepStep(key="build", label="③ 재무 데이터셋 빌드", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "321건" in result


def test_run_preparation_step_xbrl_success(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    monkeypatch.setattr(actions, "find_periodic_filings", lambda *a, **k: [make_filing()])
    monkeypatch.setattr(actions, "download_xbrl_filings", lambda *a, **k: make_xbrl_outcomes())
    step = actions.PrepStep(key="xbrl", label="④ XBRL 원본 수집", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "XBRL 원본 수집 완료" in result


def test_run_preparation_step_xbrl_failed_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """XBRL 다운로드 실패 1건이라도 있으면 DataValidationError(README §27 부분 실패 비은폐)."""
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    monkeypatch.setattr(actions, "find_periodic_filings", lambda *a, **k: [make_filing()])
    monkeypatch.setattr(
        actions, "download_xbrl_filings", lambda *a, **k: make_xbrl_outcomes(failed=True)
    )
    step = actions.PrepStep(key="xbrl", label="④ XBRL 원본 수집", estimate_seconds=1.0)

    with pytest.raises(DataValidationError, match="XBRL 원본 수집 실패"):
        actions.run_preparation_step(
            step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
        )


def test_run_preparation_step_reconcile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    report = make_reconciliation_report(total=290)
    monkeypatch.setattr(actions, "reconcile_all", lambda *a, **k: report)
    step = actions.PrepStep(key="reconcile", label="⑤ API-XBRL 대조", estimate_seconds=1.0)

    result = actions.run_preparation_step(
        step, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert "290건" in result


# ---------------------------------------------------------------------------
# execute_data_preparation — 순서·콜백·단계 실패 시 중단(명세 §1·§4)
# ---------------------------------------------------------------------------


def test_execute_data_preparation_runs_all_steps_in_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    monkeypatch.setattr(actions, "load_market_config", lambda: MarketConfig())
    monkeypatch.setattr(actions, "collect_financials", lambda *a, **k: make_collection_summary())
    monkeypatch.setattr(actions, "collect_market_data", lambda *a, **k: make_market_summary())
    monkeypatch.setattr(actions, "build_financial_datasets", lambda *a, **k: make_build_report())

    plan = actions.PrepPlan(
        steps=[
            actions.PrepStep(key="financials", label="① 재무 데이터 수집", estimate_seconds=10.0),
            actions.PrepStep(key="market", label="② 시장 데이터 수집", estimate_seconds=20.0),
            actions.PrepStep(key="build", label="③ 재무 데이터셋 빌드", estimate_seconds=10.0),
        ]
    )
    seen_keys: list[str] = []
    seen_remaining: list[float] = []

    def _record_step_start(step: actions.PrepStep, remaining: float) -> None:
        seen_keys.append(step.key)
        seen_remaining.append(remaining)

    result = actions.execute_data_preparation(
        plan,
        SK_HYNIX,
        settings=settings,
        from_year=2015,
        to_year=2025,
        on_step_start=_record_step_start,
    )

    assert result.succeeded
    assert [outcome.step.key for outcome in result.completed] == ["financials", "market", "build"]
    assert seen_keys == ["financials", "market", "build"]
    # 첫 콜백은 계획 총 예상, 이후 완료된 단계만큼 줄어든다(단조 감소).
    assert seen_remaining == sorted(seen_remaining, reverse=True)
    assert seen_remaining[0] == pytest.approx(40.0)


def test_execute_data_preparation_stops_on_first_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    settings = _settings(tmp_path)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    monkeypatch.setattr(actions, "load_market_config", lambda: MarketConfig())

    def fake_financials_fails(*a: object, **k: object) -> CollectionSummary:
        raise DataValidationError("DART 오류로 재무 수집 실패")

    market_called = False

    def fake_market(*a: object, **k: object) -> MarketCollectionSummary:
        nonlocal market_called
        market_called = True
        return make_market_summary()

    monkeypatch.setattr(actions, "collect_financials", fake_financials_fails)
    monkeypatch.setattr(actions, "collect_market_data", fake_market)

    plan = actions.PrepPlan(
        steps=[
            actions.PrepStep(key="financials", label="① 재무 데이터 수집", estimate_seconds=10.0),
            actions.PrepStep(key="market", label="② 시장 데이터 수집", estimate_seconds=20.0),
        ]
    )

    result = actions.execute_data_preparation(
        plan, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert not result.succeeded
    assert result.completed == []
    assert result.failed_step is not None
    assert result.failed_step.key == "financials"
    assert result.error_message is not None
    assert "DART 오류로 재무 수집 실패" in result.error_message
    assert market_called is False


def test_execute_data_preparation_empty_plan_succeeds_trivially(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    plan = actions.PrepPlan(steps=[])

    result = actions.execute_data_preparation(
        plan, SK_HYNIX, settings=settings, from_year=2015, to_year=2025
    )

    assert result.succeeded
    assert result.completed == []
