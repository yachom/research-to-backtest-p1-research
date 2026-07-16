"""Streamlit 7화면 AppTest 스모크 + 화면③ CLI 동등성 테스트 (docs/specs/W3c-report-ui.md §3.2).

LLM 호출 경로(화면②·⑤ 생성 버튼)는 이 테스트들에서 아예 누르지 않는다 —
스모크 케이스는 픽스처 run의 상태별 위젯 존재·잠금만 확인하고, 화면③
라운드트립 케이스는 LLM을 쓰지 않는 create-analyst-view 경로만 검증한다
(live LLM 호출 예산 0회, 명세 §4). ``FakeLlmClient``\\ 는 이 파일에서 직접
쓰이진 않지만, 만약 생성 버튼을 누르는 테스트를 추가한다면
``monkeypatch.setattr(actions, "create_llm_client", lambda cfg, s: FakeLlmClient([...]))``\\
패턴을 따른다(``tests/unit/test_cli_strategy_draft.py``\\ 와 동일 관례).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from research_backtest.app.ui import actions
from research_backtest.app.ui import state as ui_state
from research_backtest.core.config import DartConfig, MarketConfig, Settings
from research_backtest.core.dart.financial_api import CollectionSummary
from research_backtest.core.financials.pipeline import (
    METRICS_FILENAME,
    FinancialBuildReport,
    financials_out_dir,
)
from research_backtest.core.hitl.states import PipelineState
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.market.collector import (
    DAILY_FILENAME,
    MarketCollectionSummary,
    market_calendar_path,
    market_normalized_stock_dir,
)

from .conftest import (
    CORP_NAME,
    EVIDENCE_IDS,
    SK_HYNIX,
    make_analyst_view,
    make_backtest_interpretation,
    make_backtest_result,
    make_build_report,
    make_candidate_analysis,
    make_collection_summary,
    make_hypothesis,
    make_market_summary,
    make_run_store,
    make_strategy,
    make_strategy_review,
    mark_financials_ready,
    mark_market_ready,
    write_backtest_artifacts,
    write_evidence_manifest,
)

APP_PATH = (
    Path(__file__).resolve().parents[3] / "src" / "research_backtest" / "app" / "streamlit_app.py"
)


def _has_key(widgets: object, key: str) -> bool:
    return any(getattr(w, "key", None) == key for w in widgets)  # type: ignore[attr-defined]


#: 네비게이션 radio의 위젯 key — research_backtest.app.ui.screens.NAV_WIDGET_KEY와
#: 동일한 문자열이다(다른 위젯 key와 동일하게 리터럴로 참조한다, docs/specs/W3e-ui-ux.md F2).
NAV_KEY = "nav_screen"


# ---------------------------------------------------------------------------
# 스모크 1/3 — run 없음
# ---------------------------------------------------------------------------


def test_app_loads_with_no_runs(ui_settings: Settings) -> None:
    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()

    assert not at.exception
    assert _has_key(at.text_input, "scr1_company")
    assert any("등록된 run이 없습니다" in info.value for info in at.info)

    # 화면 이동(F2) — 네비게이션 radio로 화면②를 선택하면(아직 run 미선택)
    # "run 선택" 안내만 보인다. rerun 후에도 radio 선택이 유지된다는 것
    # 자체가 st.tabs 리셋 문제(U1a)의 해결을 보여준다.
    at.radio(key=NAV_KEY).set_value(1).run()
    assert not at.exception
    assert at.radio(key=NAV_KEY).value == 1
    assert any("먼저 사이드바에서 run을 선택" in info.value for info in at.info)


# ---------------------------------------------------------------------------
# 스모크 2/3 — DATA_READY (아직 후보 없음 → 화면②만 활성, 나머지 잠금)
# ---------------------------------------------------------------------------


def test_screen_state_data_ready(ui_settings: Settings) -> None:
    run_id = "20260101_000000_TESTCO"
    make_run_store(ui_settings, run_id, target_state=PipelineState.DATA_READY)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(run_id).run()

    # 화면② — 생성 버튼이 보인다(각 화면은 nav 선택 시에만 렌더된다, F2).
    at.radio(key=NAV_KEY).set_value(1).run()
    assert not at.exception
    assert _has_key(at.button, f"scr2_generate_btn__{run_id}")

    # 화면③~⑦은 아직 진입 불가 — 잠금 사유가 표시되고 폼 위젯은 렌더되지 않는다.
    at.radio(key=NAV_KEY).set_value(2).run()
    assert any("AI 분석 후보를 먼저 생성하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr3_view_id__{run_id}")

    at.radio(key=NAV_KEY).set_value(3).run()
    assert any("분석 관점을 먼저 저장하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr4_id__{run_id}")

    # 화면⑦(index 6) — AWAITING_INTERPRETATION 이전은 잠금(screen7_availability).
    at.radio(key=NAV_KEY).set_value(6).run()
    assert any("백테스트를 먼저 실행하세요" in w.value for w in at.warning)


# ---------------------------------------------------------------------------
# 스모크 3/3 — AWAITING_ANALYST_VIEW (후보 있음 → 화면②③ 활성, 화면④ 잠금)
# ---------------------------------------------------------------------------


def test_screen_state_awaiting_analyst_view(ui_settings: Settings) -> None:
    run_id = "20260101_000001_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.AWAITING_ANALYST_VIEW)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(run_id).run()

    # 화면② — AI 후보가 이미 있으므로 재생성 버튼 + 후보 statement가 보인다.
    at.radio(key=NAV_KEY).set_value(1).run()
    assert not at.exception
    assert _has_key(at.button, f"scr2_generate_btn__{run_id}")
    assert any("영업이익이 흑자로 전환되었다." in md.value for md in at.markdown)

    # 화면③ — 편집 가능한 폼이 렌더되고 잠겨있지 않다.
    at.radio(key=NAV_KEY).set_value(2).run()
    view_id_widget = at.text_input(key=f"scr3_view_id__{run_id}")
    assert view_id_widget.disabled is False
    assert _has_key(at.multiselect, f"scr3_selected__{run_id}")

    # 화면④ — 아직 분석 관점이 저장되지 않았으므로 잠금.
    at.radio(key=NAV_KEY).set_value(3).run()
    assert any("분석 관점을 먼저 저장하세요" in w.value for w in at.warning)
    assert not _has_key(at.text_input, f"scr4_id__{run_id}")


# ---------------------------------------------------------------------------
# 스모크(추가) — COMPLETE (전체 파이프라인 완료 → 화면⑥⑦ 결과·생성-보고서 안내)
# ---------------------------------------------------------------------------


def test_screen_state_complete(ui_settings: Settings) -> None:
    run_id = "20260101_000002_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.COMPLETE)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())
    store.save_analyst_view(make_analyst_view())
    store.save_human_hypothesis(make_hypothesis())
    draft = make_strategy()
    store.save_strategy_draft(draft)
    store.save_strategy_review(make_strategy_review(draft=draft))
    result = make_backtest_result()
    write_backtest_artifacts(store.run_dir, result)
    store.save_backtest_interpretation(make_backtest_interpretation())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(run_id).run()

    # 화면② — 이미 분석 관점 이후로 진행되어 재생성 버튼은 없고 읽기 전용 안내만 있다.
    at.radio(key=NAV_KEY).set_value(1).run()
    assert not at.exception
    assert not _has_key(at.button, f"scr2_generate_btn__{run_id}")
    assert any("재생성할 수 없습니다" in w.value for w in at.info)

    # 화면③④ — 읽기 전용(폼은 보이되 비활성화, 저장·승인 버튼 없음).
    at.radio(key=NAV_KEY).set_value(2).run()
    assert at.text_input(key=f"scr3_view_id__{run_id}").disabled is True
    assert not _has_key(at.button, f"scr3_save_btn__{run_id}")

    at.radio(key=NAV_KEY).set_value(3).run()
    assert at.text_input(key=f"scr4_id__{run_id}").disabled is True
    assert not _has_key(at.button, f"scr4_approve_btn__{run_id}")

    # 화면⑥ — 성과지표 표 + 거래내역이 렌더된다.
    at.radio(key=NAV_KEY).set_value(5).run()
    assert any("demo_strategy" in md.value for md in at.markdown)
    assert len(at.table) > 0
    assert len(at.dataframe) > 0

    # 화면⑦ — COMPLETE 안내 + generate-report 힌트.
    at.radio(key=NAV_KEY).set_value(6).run()
    assert any("COMPLETE" in s.value for s in at.success)
    assert any("generate-report" in c.value for c in at.caption)


# ---------------------------------------------------------------------------
# 라운드트립 — 화면③ 저장이 create-analyst-view와 동일 산출물·전이를 만드는지
# ---------------------------------------------------------------------------


def test_screen3_save_matches_cli_transition(ui_settings: Settings) -> None:
    run_id = "20260101_000003_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.AWAITING_ANALYST_VIEW)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(run_id).run()
    at.radio(key=NAV_KEY).set_value(2).run()

    at.text_input(key=f"scr3_view_id__{run_id}").set_value("view-e2e")
    at.text_input(key=f"scr3_author__{run_id}").set_value("테스트 사용자")
    at.text_area(key=f"scr3_question__{run_id}").set_value("실적 회복은 선반영되었는가?")
    at.text_area(key=f"scr3_thesis__{run_id}").set_value("서프라이즈 여부가 핵심이다.")
    at.multiselect(key=f"scr3_selected__{run_id}").set_value([EVIDENCE_IDS[0], EVIDENCE_IDS[1]])
    at.multiselect(key=f"scr3_rejected__{run_id}").set_value([EVIDENCE_IDS[2]])
    at.run()

    # 제외 근거를 선택했으므로 근거별 이유 입력 위젯이 동적으로 나타난다.
    assert _has_key(at.text_input, f"scr3_rej_reason_{EVIDENCE_IDS[2]}__{run_id}")
    at.text_input(key=f"scr3_rej_reason_{EVIDENCE_IDS[2]}__{run_id}").set_value("이번 범위 밖")
    at.text_area(key=f"scr3_sel_reason__{run_id}").set_value("1차 공시 자료를 우선한다.")
    at.text_area(key=f"scr3_interpretation__{run_id}").set_value("모멘텀이 이어진다.")
    at.text_area(key=f"scr3_mechanism__{run_id}").set_value("확인 → 수급 유입 → 추세 지속")
    at.text_area(key=f"scr3_counter__{run_id}").set_value("이미 선반영되었을 수 있다.")
    at.text_area(key=f"scr3_uncertain__{run_id}").set_value("업황 사이클 판단")
    at.run()

    at.button(key=f"scr3_save_btn__{run_id}").click().run()
    assert not at.exception
    assert any("분석 관점을 저장했습니다" in s.value for s in at.success)

    # --- CLI(create-analyst-view)가 만드는 것과 동일한 산출물·전이인지 검증 ---
    reloaded = RunStore(ui_settings.outputs_dir, run_id)
    run_state = reloaded.load_run_state()
    assert run_state.current_state == PipelineState.ANALYST_VIEW_APPROVED
    last_transition = run_state.transitions[-1]
    assert last_transition.from_state == PipelineState.AWAITING_ANALYST_VIEW
    assert last_transition.to_state == PipelineState.ANALYST_VIEW_APPROVED
    assert last_transition.actor == "user"
    assert last_transition.auto_approved is False

    saved_view = reloaded.load_analyst_view()
    assert saved_view.view_id == "view-e2e"
    assert saved_view.selected_evidence_ids == [EVIDENCE_IDS[0], EVIDENCE_IDS[1]]
    assert saved_view.rejected_evidence_ids == [EVIDENCE_IDS[2]]
    assert saved_view.rejected_evidence_reasons == {EVIDENCE_IDS[2]: "이번 범위 밖"}
    assert saved_view.counterarguments == ["이미 선반영되었을 수 있다."]


# ---------------------------------------------------------------------------
# U1·U3 회귀 고정 (docs/specs/W3e-ui-ux.md §3.1) — 화면③ 저장(상태 전이 발생)
# 후 예외 없음 + sidebar 선택 run_id 동일 + 현재 네비게이션이 화면④.
#
# 과거 구현은 (a) st.tabs가 rerun마다 첫 탭으로 리셋되고(U1a), (b) 사이드바
# selectbox 옵션 라벨에 가변 상태 문자열이 섞여 있어(U1b) 상태 전이가 일어나는
# 저장마다 화면①로 떨어지고 run 선택도 풀렸다. 라디오 네비게이션(session_state
# 보존, F2)과 run_id 옵션 값(F1)으로 고정한 뒤에는 예외 없이 run 선택 유지 +
# 화면④ 자동 전진(F3)이 함께 관측돼야 한다.
# ---------------------------------------------------------------------------


def test_screen3_save_preserves_selection_and_advances_nav(ui_settings: Settings) -> None:
    run_id = "20260101_000006_TESTCO"
    store = make_run_store(ui_settings, run_id, target_state=PipelineState.AWAITING_ANALYST_VIEW)
    write_evidence_manifest(store.run_dir)
    store.save_candidate_analysis(make_candidate_analysis())

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.selectbox(key="sidebar_run_select").select(run_id).run()
    at.radio(key=NAV_KEY).set_value(2).run()

    at.text_input(key=f"scr3_view_id__{run_id}").set_value("view-e2e")
    at.text_area(key=f"scr3_question__{run_id}").set_value("실적 회복은 선반영되었는가?")
    at.text_area(key=f"scr3_thesis__{run_id}").set_value("서프라이즈 여부가 핵심이다.")
    at.multiselect(key=f"scr3_selected__{run_id}").set_value([EVIDENCE_IDS[0], EVIDENCE_IDS[1]])
    at.text_area(key=f"scr3_counter__{run_id}").set_value("이미 선반영되었을 수 있다.")

    at.button(key=f"scr3_save_btn__{run_id}").click().run()

    assert not at.exception
    assert at.selectbox(key="sidebar_run_select").value == run_id
    assert at.radio(key=NAV_KEY).value == 3
    assert _has_key(at.text_input, f"scr4_id__{run_id}")


# ---------------------------------------------------------------------------
# 화면① 데이터 준비 패널 (docs/specs/W3d-ui-data-prep.md §2) — resolve_corp만
# monkeypatch해 DART 호출 없이 corp를 확보하고, 데이터 미비는 tmp data_dir이
# 비어 있는 것으로 자연히 재현한다(실행 클릭 케이스는 collector도 monkeypatch).
# ---------------------------------------------------------------------------


def _patch_prep_collectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """준비 실행 클릭 케이스용 — resolve_corp·collector 전부 monkeypatch(명세 §4).

    financials·market·build 각 fake는 요약 객체만 흉내내지 않고 실제로 marker
    파일을 써서(financial_metrics.parquet·daily.parquet·calendar.parquet),
    이어지는 create_run의 ensure_data_ready 재검사가 실제로 통과하게 한다 —
    "완료 후 run 생성 자동 재시도"까지 실제로 관통하는지 검증하기 위함이다.
    """
    monkeypatch.setattr(actions, "resolve_corp", lambda company, settings: SK_HYNIX)
    monkeypatch.setattr(actions, "load_dart_config", lambda: DartConfig())
    monkeypatch.setattr(actions, "load_market_config", lambda: MarketConfig())

    def fake_collect_financials(
        client: object, corp_code: str, *, out_dir: Path, **_: object
    ) -> CollectionSummary:
        return make_collection_summary(corp_code=corp_code)

    def fake_build(corp_code: str, *, data_dir: Path, **_: object) -> FinancialBuildReport:
        path = financials_out_dir(data_dir, corp_code) / METRICS_FILENAME
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
        return make_build_report(corp_code=corp_code)

    def fake_collect_market(
        source: object, *, stock_code: str, data_dir: Path, **_: object
    ) -> MarketCollectionSummary:
        daily = market_normalized_stock_dir(data_dir, stock_code) / DAILY_FILENAME
        daily.parent.mkdir(parents=True, exist_ok=True)
        daily.write_bytes(b"")
        calendar = market_calendar_path(data_dir)
        calendar.parent.mkdir(parents=True, exist_ok=True)
        calendar.write_bytes(b"")
        return make_market_summary(stock_code=stock_code)

    monkeypatch.setattr(actions, "collect_financials", fake_collect_financials)
    monkeypatch.setattr(actions, "build_financial_datasets", fake_build)
    monkeypatch.setattr(actions, "collect_market_data", fake_collect_market)


def test_screen1_missing_data_shows_prep_panel(
    monkeypatch: pytest.MonkeyPatch, ui_settings: Settings
) -> None:
    """데이터 미비 → 준비 패널(옵션·버튼)이 뜬다(명세 §2) — 실행은 누르지 않는다."""
    monkeypatch.setattr(actions, "resolve_corp", lambda company, settings: SK_HYNIX)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.text_input(key="scr1_company").set_value(CORP_NAME)
    at.run()
    at.button(key="scr1_create_run_btn").click().run()

    assert not at.exception
    assert any("데이터 준비가 완료되지 않았습니다" in e.value for e in at.error)
    assert _has_key(at.number_input, "scr1_prep_from_year")
    assert _has_key(at.checkbox, "scr1_prep_include_xbrl")
    assert _has_key(at.button, "scr1_prep_run_btn")
    # 아직 실행 전이므로 단계별 예상 총 소요 캡션이 보인다(명세 §2 "전체 계획 요약").
    assert any("단계" in c.value and "예상 총 소요" in c.value for c in at.caption)


def test_screen1_prep_execute_creates_run_and_retries(
    monkeypatch: pytest.MonkeyPatch, ui_settings: Settings
) -> None:
    """[데이터 준비 실행] 클릭 → 단계별 완료 후 run 생성을 자동 재시도한다(명세 §2).

    ``st.rerun()``\\ 은 같은 ``AppTest.run()`` 호출 안에서 스크립트를 즉시
    다시 실행한다(streamlit.testing.v1.local_script_runner의 rerun 처리 —
    실측으로 확인). 그 결과 재시도 *이전*(prep 실행 중) 렌더된 성공 배너·
    st.status 로그는 재실행된 최종 트리에는 남지 않는다 — 그래서 이 테스트는
    일시적 성공 문구 대신 **실제로 남는 산출물**(디스크의 RunManifest·
    RunState, 재실행 후 사이드바·상태 배너에 반영된 새 run)로 검증한다.
    """
    _patch_prep_collectors(monkeypatch)
    monkeypatch.setenv("DART_API_KEY", "test-dart-key")

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.text_input(key="scr1_company").set_value(CORP_NAME)
    at.run()
    at.button(key="scr1_create_run_btn").click().run()
    assert _has_key(at.button, "scr1_prep_run_btn")

    at.button(key="scr1_prep_run_btn").click().run()

    assert not at.exception

    run_dirs = [p for p in ui_settings.outputs_dir.iterdir() if p.is_dir()]
    assert len(run_dirs) == 1
    run_state = RunStore(ui_settings.outputs_dir, run_dirs[0].name).load_run_state()
    assert run_state.current_state == PipelineState.DATA_READY

    # 재실행된 최종 화면이 새 run을 선택된 상태로 반영한다 — "화면②로 이어지게
    # 한다"(명세 §2)의 관측 가능한 증거: 사이드바 선택·상태 배너가 갱신되고,
    # 화면②(다음 단계)의 생성 버튼이 이 run에 대해 렌더된다.
    data_ready_label = ui_state.PIPELINE_STATE_LABELS[PipelineState.DATA_READY]
    assert any(f"현재 상태: {data_ready_label}" in md.value for md in at.markdown)
    assert _has_key(at.button, f"scr2_generate_btn__{run_dirs[0].name}")


def test_sidebar_create_run_with_existing_runs_autoselects_new_run(
    monkeypatch: pytest.MonkeyPatch, ui_settings: Settings
) -> None:
    """기존 run이 있어 사이드바 selectbox가 이미 그려진 상태에서 새 run 생성 (회귀).

    과거 구현은 생성 직후 위젯 key("sidebar_run_select")에 직접 대입해
    StreamlitAPIException("cannot be modified after the widget ... is
    instantiated")으로 죽었다 — 첫 run(빈 outputs, selectbox 미생성)만 테스트돼
    잡히지 않았던 실사용 크래시. 수정 후에는 펜딩 키에 예약하고 다음 rerun에서
    위젯 생성 **전**에 적용되므로, 예외 없이 새 run이 자동 선택되어야 한다.

    새 run 생성 폼은 화면①에만 있다(docs/specs/W3e-ui-ux.md F5 — 사이드바
    생성 폼은 진입점 이중화라 제거됐다). 생성 성공은 run 선택뿐 아니라
    네비게이션도 화면②로 자동 전진해야 한다(F3).
    """
    make_run_store(ui_settings, "RUN-EXISTING", target_state=PipelineState.DATA_READY)
    mark_financials_ready(ui_settings)
    mark_market_ready(ui_settings)
    monkeypatch.setattr(actions, "resolve_corp", lambda company, settings: SK_HYNIX)
    monkeypatch.setenv("DART_API_KEY", "test-dart-key")

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    assert _has_key(at.selectbox, "sidebar_run_select")  # 전제: 위젯이 이미 존재
    assert not _has_key(at.text_input, "sidebar_new_company")  # F5 — 사이드바 생성 폼 제거됨

    at.text_input(key="scr1_company").set_value(CORP_NAME)
    at.run()
    at.button(key="scr1_create_run_btn").click().run()

    assert not at.exception
    run_ids = sorted(p.name for p in ui_settings.outputs_dir.iterdir() if p.is_dir())
    assert len(run_ids) == 2
    new_run_id = next(r for r in run_ids if r != "RUN-EXISTING")
    assert at.selectbox(key="sidebar_run_select").value == new_run_id
    assert at.radio(key=NAV_KEY).value == 1


def test_sidebar_goto_screen1_button_navigates(ui_settings: Settings) -> None:
    """F5 — 사이드바의 "① 화면으로 이동" 버튼은 화면①로 네비게이션을 예약한다."""
    make_run_store(ui_settings, "RUN-EXISTING", target_state=PipelineState.DATA_READY)

    at = AppTest.from_file(str(APP_PATH), default_timeout=30).run()
    at.radio(key=NAV_KEY).set_value(1).run()
    assert at.radio(key=NAV_KEY).value == 1

    at.button(key="sidebar_goto_screen1_btn").click().run()

    assert not at.exception
    assert at.radio(key=NAV_KEY).value == 0
    assert _has_key(at.text_input, "scr1_company")
