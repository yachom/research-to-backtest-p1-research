"""7화면 렌더링 (1804 §15, docs/specs/W3c-report-ui.md §3, S1 소유).

각 ``render_screenN``\\ 은 :mod:`research_backtest.app.ui.state`\\ 로 잠금 여부를
판정하고, :mod:`research_backtest.app.ui.actions`\\ 로 core API를 조립·호출한다.
이 모듈 자신은 승인·검증 규칙을 갖지 않는다 — 실패는 actions가 던지는 예외
그대로 ``st.error``\\ 로 보여준다(:func:`_run_action`).

AI가 생성한 영역(화면②의 후보, 화면⑤의 초안)에는 "AI 후보·초안" 캡션을
붙인다(HITL §8, content_origin은 모델이 저장하므로 UI 태그는 안내용일 뿐이다).
"""

from __future__ import annotations

import copy
import json
import time
from collections.abc import Callable
from datetime import date
from typing import cast

import streamlit as st  # streamlit 1.59는 py.typed로 인라인 타입을 배포한다(stub 부재 아님).
from pydantic import ValidationError as PydanticValidationError

from research_backtest.app.ui import actions, state
from research_backtest.core.config import Settings
from research_backtest.core.dart.financial_api import MIN_SUPPORTED_YEAR
from research_backtest.core.exceptions import (
    ApprovalGateError,
    ConfigError,
    DartApiError,
    DartTransportError,
    DataValidationError,
    MarketAuthError,
    StrategyValidationError,
    XbrlParseError,
)
from research_backtest.core.hitl.models import (
    AnalystView,
    BacktestInterpretation,
    HumanInvestmentHypothesis,
    HypothesisStatus,
    now_kst_iso,
)
from research_backtest.core.hitl.states import PipelineState, RunState
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.models import DartCorporation

_GUARDED_EXCEPTIONS = (
    ApprovalGateError,
    ConfigError,
    DartApiError,
    DartTransportError,
    DataValidationError,
    FileNotFoundError,
    MarketAuthError,
    StrategyValidationError,
    XbrlParseError,
    PydanticValidationError,
)

_AI_CAPTION = "[AI 후보·초안 — 사용자 검토·승인 필요]"

#: 화면① 데이터 준비 패널이 떠 있는 동안의 세션 상태 키 — 값은 (company, as_of ISO)
#: (docs/specs/W3d-ui-data-prep.md §1~§2).
_PREP_PENDING_KEY = "scr1_prep_pending"


def _format_duration(seconds: float) -> str:
    """초 단위 예상·경과 시간을 사람이 읽기 좋은 문자열로 바꾼다 (명세 §2 라벨 형식)."""
    if seconds < 60:
        return f"{seconds:.0f}초"
    return f"{seconds / 60:.1f}분"


def _run_action[T](fn: Callable[[], T], *, success_message: str | None = None) -> T | None:
    """액션 호출 공통 오류 처리 — CLI의 종료 코드 매핑을 st.error로 바꾼다.

    예외 메시지는 그대로 노출한다(토큰 등 민감정보는 core.llm 계층이 이미
    절단하므로 여기서 추가 가공하지 않는다).
    """
    try:
        result = fn()
    except _GUARDED_EXCEPTIONS as err:
        st.error(str(err))
        return None
    if success_message:
        st.success(success_message)
    return result


def _lines(text: str) -> list[str]:
    """여러 줄 textarea 입력을 비어있지 않은 줄 목록으로 변환한다(1804 폼 관례)."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def _ai_caption() -> None:
    st.caption(_AI_CAPTION)


def _or_blank(value: str | None) -> str:
    return value or ""


def _lock_banner(availability: state.ScreenAvailability) -> bool:
    """잠금 상태면 안내를 출력하고 True(더 진행하지 말 것)를 반환한다."""
    if availability.locked:
        st.warning(availability.reason)
        return True
    if availability.read_only and availability.reason:
        st.info(availability.reason)
    return False


def _evidence_format_func(
    entries_by_id: dict[str, actions.EvidenceEntry],
) -> Callable[[str], str]:
    """evidence_id → 사람이 읽는 라벨 매핑 함수를 만든다 (docs/specs/W3e-ui-ux.md F4).

    ``st.multiselect``\\ 의 ``format_func``\\ 로 쓴다 — 표시만 라벨로 바뀌고
    위젯이 실제로 값으로 다루고 저장하는 것은 여전히 evidence_id다(모델 계약
    불변). manifest에 없는 id(방어적 폴백)는 그대로 보여준다.
    """

    def _fmt(evidence_id: str) -> str:
        entry = entries_by_id.get(evidence_id)
        return actions.evidence_label(entry) if entry is not None else evidence_id

    return _fmt


# ---------------------------------------------------------------------------
# 사이드바 — run 선택·상태 배지·화면 네비게이션 (§3.1, docs/specs/W3e-ui-ux.md F1·F2·F5)
# ---------------------------------------------------------------------------


#: 다음 rerun에서 사이드바 selectbox가 선택할 run_id (위젯 키가 아닌 일반 세션 키).
#: 위젯 key("sidebar_run_select")는 위젯 생성 후 같은 실행에서 대입이 금지되므로
#: (StreamlitAPIException), run 생성 경로는 이 키에 적어두기만 하고 호출부가
#: 다른 예약과 함께 한 번만 st.rerun()한다 — 사이드바가 다음 실행에서 위젯을
#: 만들기 **전에** 이 값을 위젯 key로 옮긴다.
_PENDING_RUN_SELECT_KEY = "_pending_run_select_run_id"

#: 다음 rerun에서 네비게이션 radio(``NAV_WIDGET_KEY``)가 선택할 화면 인덱스(0~6) —
#: 위와 동일한 펜딩 패턴(F3, U1a/U1b와 같은 "위젯 생성 후 직접 대입 금지" 회귀
#: 위험 회피). 저장·생성 성공 직후 다음 화면 자동 전진에 쓴다.
_PENDING_NAV_KEY = "_pending_nav_screen"

#: 네비게이션 radio의 위젯 key (F2) — session_state에 남아 rerun 후에도 선택이
#: 보존된다(st.tabs와 달리 첫 화면으로 리셋되지 않는다, U1a 해결).
NAV_WIDGET_KEY = "nav_screen"


def _select_run_on_next_rerun(run_id: str) -> None:
    """다음 rerun에서 사이드바가 ``run_id``\\ 를 선택하게 예약한다(F1, run 생성 직후 호출).

    다른 예약(:func:`_goto_screen_on_next_rerun`)과 묶어 호출부가 한 번만
    ``st.rerun()``\\ 하도록 여기서는 rerun을 호출하지 않는다.
    """
    st.session_state[_PENDING_RUN_SELECT_KEY] = run_id


def _goto_screen_on_next_rerun(screen_index: int) -> None:
    """다음 rerun에서 네비게이션이 ``screen_index``\\ (0~6)를 가리키게 예약한다(F3).

    위젯 key(``NAV_WIDGET_KEY``)는 위젯 생성 후 대입이 금지되므로(U1b와 동일한
    StreamlitAPIException 회귀 위험) run 선택과 동일한 펜딩 패턴을 쓴다 — 값은
    일반 세션 키에 적어두고, :func:`render_nav`\\ 가 위젯 생성 **전**에 옮긴다.
    """
    st.session_state[_PENDING_NAV_KEY] = screen_index


def _toast_advance(screen_index: int) -> None:
    """저장·생성 성공 직후 다음 화면 자동 전진을 예약하고 안내 토스트를 띄운다(F3, U3)."""
    _goto_screen_on_next_rerun(screen_index)
    st.toast(f"저장됨 — 화면 {screen_index + 1}로 이동")


def render_sidebar(settings: Settings) -> str | None:
    """사이드바(run 선택 + 상태 배지 + 화면① 이동 안내)를 그리고 선택된 run_id를 반환한다.

    새 run 생성 폼은 화면①에만 있다(U4/F5 — 생성 진입점 이중화 제거). 여기서는
    화면①로 이동하는 버튼만 제공한다.
    """
    st.sidebar.header("실행(run) 관리")
    summaries = state.scan_runs(settings)
    summary_by_id = {s.run_id: s for s in summaries}

    selected_run_id: str | None = None
    if summaries:
        options: list[str | None] = [None, *summary_by_id]

        def _format_run_option(rid: str | None) -> str:
            if rid is None:
                return "(선택 안 함)"
            summary = summary_by_id[rid]
            label = state.PIPELINE_STATE_LABELS[summary.current_state]
            return f"{rid} — {summary.company} [{label}]"

        # 예약된 선택(run 생성 직후)을 위젯 생성 전에 적용한다(F1) — 옵션 값
        # 자체가 불변 run_id이므로 상태 전이로 라벨이 바뀌어도 선택이 풀리지
        # 않는다(U1b: 과거엔 라벨 문자열이 옵션 값이라 상태가 바뀌면 옵션이
        # 사라져 선택이 index 0으로 되돌아갔다).
        pending = st.session_state.pop(_PENDING_RUN_SELECT_KEY, None)
        if pending is not None and pending in summary_by_id:
            st.session_state["sidebar_run_select"] = pending
        selected_run_id = st.sidebar.selectbox(
            "run 선택", options, format_func=_format_run_option, key="sidebar_run_select"
        )
    else:
        st.sidebar.info("등록된 run이 없습니다. 화면①에서 새로 생성하세요.")

    if selected_run_id is not None:
        store = RunStore(settings.outputs_dir, selected_run_id)
        try:
            run_state = store.load_run_state()
        except (DataValidationError, PydanticValidationError):
            st.sidebar.error("선택한 run의 run_state.json을 읽을 수 없습니다.")
        else:
            st.sidebar.markdown(
                f"**파이프라인 상태**: {state.PIPELINE_STATE_LABELS[run_state.current_state]}"
                f"  \n(run: `{run_state.run_id}`)"
            )
            st.sidebar.caption(f"다음 단계: {state.NEXT_STEP_HINTS[run_state.current_state]}")

    st.sidebar.divider()
    st.sidebar.caption("새 run은 화면①에서 생성하세요.")
    if st.sidebar.button("① 화면으로 이동", key="sidebar_goto_screen1_btn"):
        _goto_screen_on_next_rerun(0)
        st.rerun()

    return selected_run_id


def render_nav() -> int:
    """화면 네비게이션(사이드바 radio)을 그리고 선택된 화면 인덱스(0~6)를 반환한다(F2).

    ``st.tabs``\\ 는 rerun마다 첫 탭으로 리셋되어(U1a) 저장 버튼이 끝날 때마다
    호출하는 ``st.rerun()``\\ 이 항상 화면①로 떨어뜨렸다. radio는 위젯 값이
    session_state(``NAV_WIDGET_KEY``)에 남아 rerun 후에도 선택이 유지된다.
    잠긴 화면도 옵션에는 그대로 노출한다(게이트 가시성 유지) — 실제 잠금
    배너는 각 ``render_screenN``\\ 이 그린다(:func:`_lock_banner`).
    """
    pending = st.session_state.pop(_PENDING_NAV_KEY, None)
    if pending is not None:
        st.session_state[NAV_WIDGET_KEY] = pending
    st.sidebar.divider()
    return st.sidebar.radio(
        "화면 이동",
        options=range(len(state.SCREEN_TITLES)),
        format_func=lambda i: state.SCREEN_TITLES[i],
        key=NAV_WIDGET_KEY,
    )


def _render_resolve_failure(failure: actions.ResolveFailure) -> None:
    if failure.result.method == "AMBIGUOUS":
        st.warning(
            f"'{failure.query}'에 대한 후보가 여러 개입니다. 6자리 종목코드로 다시 시도하세요."
        )
        rows = [
            {"corp_code": c.corp_code, "stock_code": c.stock_code or "-", "corp_name": c.corp_name}
            for c in failure.result.candidates
        ]
        st.dataframe(rows, hide_index=True)
    else:
        st.error(f"'{failure.query}'에 해당하는 기업을 찾지 못했습니다 (NOT_FOUND).")
        st.caption("6자리 종목코드로 다시 시도하면 정확히 식별됩니다 (예: 000660).")


# ---------------------------------------------------------------------------
# 화면① — 기업·기준일 입력 (1804 §15 화면1, create-run)
# ---------------------------------------------------------------------------


def _create_run_and_rerun(settings: Settings, company: str, as_of: date) -> None:
    """create_run을 호출해 성공하면 사이드바 선택·네비게이션을 갱신하고 재실행한다.

    화면①의 직접 제출·준비 완료 후 자동 재시도(명세 §2 "run 생성을 자동
    재시도해 화면 ②로 이어지게 한다") 두 경로가 이 헬퍼를 공유한다. run 선택
    예약(F1)과 화면② 자동 전진 예약(F3, docs/specs/W3e-ui-ux.md)을 한 rerun에
    함께 적용한다.
    """
    outcome = _run_action(lambda: actions.create_run(settings, company=company, as_of=as_of))
    if isinstance(outcome, actions.ResolveFailure):
        _render_resolve_failure(outcome)
    elif outcome is not None:
        st.success(f"run 생성 완료: {outcome.run_id}")
        _select_run_on_next_rerun(outcome.run_id)
        _toast_advance(1)
        st.rerun()


def _attempt_create_run(settings: Settings, company: str, as_of: date) -> None:
    """run 생성을 시도한다 — 데이터가 미비하면 준비 패널을 띄우기 위한 상태만 남긴다.

    ``actions.create_run``\\ 을 바로 부르지 않는 이유: 그 함수는 실패 시
    문자열 메시지만 담은 예외를 던져 준비 패널에 필요한 corp 정보를 잃는다.
    대신 그 함수가 내부적으로 쓰는 것과 동일한 조립 부품
    (``resolve_corp``\\ ·``ensure_data_ready``, 둘 다 actions.py 공개 API)을
    직접 호출해 corp를 확보한다 — core 로직 재구현이 아니라 재사용이다.
    """
    resolved = _run_action(lambda: actions.resolve_corp(company, settings))
    if resolved is None:
        return
    if isinstance(resolved, actions.ResolveFailure):
        _render_resolve_failure(resolved)
        return
    corp = resolved
    stock_code = corp.stock_code
    if stock_code is None:
        st.error(f"'{corp.corp_name}'은(는) 비상장 법인입니다 — run을 생성할 수 없습니다.")
        return
    missing = _run_action(lambda: actions.ensure_data_ready(corp, stock_code, settings))
    if missing is None:
        return
    if missing:
        st.session_state[_PREP_PENDING_KEY] = (company.strip(), as_of.isoformat())
        return
    _create_run_and_rerun(settings, company, as_of)


def _render_prep_panel(settings: Settings, company: str, as_of: date) -> None:
    """데이터 미비 안내 아래에 준비 옵션·[데이터 준비 실행] 버튼을 그린다 (명세 §1~§2).

    corp·missing은 매 렌더마다 다시 확인한다(가볍다 — 고유번호 레지스트리는
    로컬 캐시, 존재 확인은 파일 존재 검사뿐) — 그래야 연도·체크박스를 바꿀
    때마다(=매 rerun) 계획이 최신 상태로 재계산된다.
    """
    resolved = _run_action(lambda: actions.resolve_corp(company, settings))
    if resolved is None:
        return
    if isinstance(resolved, actions.ResolveFailure):
        _render_resolve_failure(resolved)
        return
    corp = resolved
    stock_code = corp.stock_code
    if stock_code is None:
        st.error(f"'{corp.corp_name}'은(는) 비상장 법인입니다 — run을 생성할 수 없습니다.")
        st.session_state.pop(_PREP_PENDING_KEY, None)
        return

    missing = _run_action(lambda: actions.ensure_data_ready(corp, stock_code, settings))
    if missing is None:
        return
    if not missing:
        # 다른 화면·CLI에서 그 사이 준비가 끝났을 수 있다 — 바로 재시도.
        st.session_state.pop(_PREP_PENDING_KEY, None)
        _create_run_and_rerun(settings, company, as_of)
        return

    st.error("데이터 준비가 완료되지 않았습니다:\n" + "\n".join(f"- {m}" for m in missing))
    st.markdown("**데이터 준비**")
    from_year = st.number_input(
        "수집 시작 연도",
        min_value=MIN_SUPPORTED_YEAR,
        max_value=as_of.year,
        value=MIN_SUPPORTED_YEAR,
        step=1,
        key="scr1_prep_from_year",
    )
    include_xbrl = st.checkbox(
        "XBRL 원본 수집 + API-XBRL 대조도 함께 실행 (정합성 검증용 · 수 분 추가)",
        value=False,
        key="scr1_prep_include_xbrl",
    )

    plan = _run_action(
        lambda: actions.plan_data_preparation(
            corp,
            from_year=int(from_year),
            to_year=as_of.year,
            include_xbrl=include_xbrl,
            settings=settings,
        )
    )
    if plan is None:
        return
    if not plan.steps:
        st.info("필요한 데이터가 모두 준비되어 있습니다 — run 생성을 다시 시도합니다.")
        st.session_state.pop(_PREP_PENDING_KEY, None)
        _create_run_and_rerun(settings, company, as_of)
        return

    if any(prep_step.key == "market" for prep_step in plan.steps) and (
        not settings.krx_id or not settings.krx_pw
    ):
        st.warning(
            "투자자 수급·지수는 KRX 로그인 필요 — .env에 KRX_ID/KRX_PW 설정 후 재실행하면 "
            "가격 캐시는 유지된 채 나머지만 수집된다."
        )

    st.caption(
        f"총 {len(plan.steps)}단계 · 예상 총 소요 ~{_format_duration(plan.total_estimate_seconds)} "
        "(캐시가 있으면 훨씬 빨리 끝납니다)"
    )
    for prep_step in plan.steps:
        st.caption(f"- {prep_step.label} — 예상 ~{_format_duration(prep_step.estimate_seconds)}")

    if st.button("데이터 준비 실행", key="scr1_prep_run_btn"):
        _execute_prep_plan(
            settings, corp, plan, from_year=int(from_year), company=company, as_of=as_of
        )


def _execute_prep_plan(
    settings: Settings,
    corp: DartCorporation,
    plan: actions.PrepPlan,
    *,
    from_year: int,
    company: str,
    as_of: date,
) -> None:
    """계획을 st.status로 단계별 표시하며 실행하고, 성공하면 run 생성을 재시도한다 (명세 §2)."""
    with st.status("데이터 준비를 실행하는 중...", expanded=True) as status:

        def _on_step_start(prep_step: actions.PrepStep, remaining: float) -> None:
            status.update(
                label=(
                    f"{prep_step.label} — 예상 ~{_format_duration(prep_step.estimate_seconds)} "
                    f"(전체 남은 예상 ~{_format_duration(remaining)})"
                )
            )

        result = actions.execute_data_preparation(
            plan,
            corp,
            settings=settings,
            from_year=from_year,
            to_year=as_of.year,
            on_step_start=_on_step_start,
        )
        for outcome in result.completed:
            st.write(
                f"{outcome.step.label} — 예상 ~{_format_duration(outcome.step.estimate_seconds)} "
                f"… 완료 ({outcome.elapsed_seconds:.0f}초): {outcome.summary}"
            )
        if not result.succeeded:
            failed_step = result.failed_step
            assert failed_step is not None
            status.update(label=f"{failed_step.label} — 실패", state="error")
            st.error(result.error_message)
            return
        status.update(label="데이터 준비 완료", state="complete")

    st.session_state.pop(_PREP_PENDING_KEY, None)
    _create_run_and_rerun(settings, company, as_of)


def render_screen1(settings: Settings, run_id: str | None) -> None:
    st.subheader(state.SCREEN_TITLES[0])
    st.write("기업명과 분석 기준일을 입력해 새 실행(run)을 생성합니다.")

    company = st.text_input("기업명 또는 종목코드", key="scr1_company")
    as_of = st.date_input("분석 기준일", value=date.today(), key="scr1_as_of")
    col1, col2 = st.columns(2)
    with col1:
        st.date_input("분석 기간 시작(참고용)", value=date(2016, 1, 1), key="scr1_period_start")
    with col2:
        st.date_input("분석 기간 종료(참고용)", value=date.today(), key="scr1_period_end")
    st.text_area("분석 초점(참고용)", key="scr1_focus")
    st.caption(
        "분석 초점은 v2 산출물에 없음 — analyst_view(화면③)에 반영하라. "
        "기간·초점 입력값은 저장되지 않습니다."
    )

    if st.button("run 생성", key="scr1_create_run_btn"):
        if not company.strip():
            st.error("기업명을 입력하세요.")
        else:
            _attempt_create_run(settings, company, as_of)

    pending = st.session_state.get(_PREP_PENDING_KEY)
    if pending is not None and pending[0] == company.strip():
        _render_prep_panel(settings, pending[0], date.fromisoformat(pending[1]))

    if run_id is not None:
        st.divider()
        st.write(f"현재 선택된 run: `{run_id}` (이미 생성됨)")
        st.caption("위 폼으로 별도의 새 run도 만들 수 있습니다.")


# ---------------------------------------------------------------------------
# 화면② — AI 분석 후보 검토 (1804 §15 화면2, generate-candidates)
# ---------------------------------------------------------------------------


def render_screen2(settings: Settings, store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[1])
    availability = state.screen2_availability(run_state.current_state)

    analysis = actions.try_load_candidate_analysis(store)

    if not availability.read_only:
        label = (
            "AI 분석 후보 (재)생성 (예상 2~5분)"
            if analysis is not None
            else "AI 분석 후보 생성 (예상 2~5분)"
        )
        if st.button(label, key=f"scr2_generate_btn__{run_state.run_id}"):
            generate_start = time.monotonic()
            with st.spinner("AI 분석 후보를 생성하는 중... (예상 2~5분)"):
                outcome = _run_action(
                    lambda: actions.generate_candidates(settings, store, run_state)
                )
            if outcome is not None:
                elapsed = time.monotonic() - generate_start
                st.success(f"AI 분석 후보 생성 완료 ({elapsed:.0f}초).")
                _toast_advance(2)
                st.rerun()
    elif availability.reason:
        st.info(availability.reason)

    if analysis is None:
        st.write("아직 생성된 AI 분석 후보가 없습니다.")
        return

    _ai_caption()
    run_id = run_state.run_id
    entries_by_id = {e.evidence_id: e for e in actions.load_evidence_entries(store)}
    fmt_evidence = _evidence_format_func(entries_by_id)
    categories: list[tuple[str, str]] = [
        ("financial_findings", "재무 변화 후보"),
        ("business_findings", "사업 변화 후보"),
        ("industry_findings", "산업 변화 후보"),
        ("catalyst_candidates", "촉매 후보"),
        ("risk_candidates", "위험 후보"),
        ("conflicting_evidence", "상충 근거"),
    ]
    selected_ids: set[str] = set()
    rejected_ids: set[str] = set()
    for field_name, title in categories:
        findings = getattr(analysis, field_name)
        with st.expander(f"{title} ({len(findings)}건)", expanded=True):
            if not findings:
                st.caption("해당 없음")
            for finding in findings:
                st.markdown(f"- {finding.statement}")
                evidence_display = ", ".join(fmt_evidence(eid) for eid in finding.evidence_ids)
                st.caption(
                    f"category={finding.category} · source_type={finding.source_type} · "
                    f"confidence={finding.confidence:.2f} · 근거: {evidence_display}"
                )
                if finding.limitations:
                    st.caption(f"한계: {'; '.join(finding.limitations)}")
                c1, c2 = st.columns(2)
                sel_key = f"scr2_sel_{field_name}_{finding.finding_id}__{run_id}"
                rej_key = f"scr2_rej_{field_name}_{finding.finding_id}__{run_id}"
                with c1:
                    is_sel = st.checkbox("선택", key=sel_key)
                with c2:
                    is_rej = st.checkbox("제외", key=rej_key)
                if is_sel:
                    selected_ids.update(finding.evidence_ids)
                if is_rej:
                    rejected_ids.update(finding.evidence_ids)

    with st.expander(f"변수 간 관계 후보 ({len(analysis.relationship_candidates)}건)"):
        for rel in analysis.relationship_candidates:
            st.markdown(f"- {rel.cause_or_signal} → {rel.outcome}: {rel.proposed_mechanism}")
            evidence_display = ", ".join(fmt_evidence(eid) for eid in rel.evidence_ids)
            counter_display = ", ".join(fmt_evidence(eid) for eid in rel.counter_evidence_ids)
            st.caption(
                f"confidence={rel.confidence:.2f} · 근거: {evidence_display} · "
                f"반대 근거: {counter_display} · "
                f"measurable_variables={rel.measurable_variables}"
            )
            c1, c2 = st.columns(2)
            sel_key = f"scr2_sel_rel_{rel.relationship_id}__{run_id}"
            rej_key = f"scr2_rej_rel_{rel.relationship_id}__{run_id}"
            with c1:
                is_sel = st.checkbox("선택", key=sel_key)
            with c2:
                is_rej = st.checkbox("제외", key=rej_key)
            if is_sel:
                selected_ids.update(rel.evidence_ids)
            if is_rej:
                rejected_ids.update(rel.evidence_ids)

    if analysis.missing_information:
        with st.expander("데이터 없음(missing_information)"):
            for item in analysis.missing_information:
                st.markdown(f"- {item}")

    st.session_state[f"scr2_carry_selected__{run_id}"] = sorted(selected_ids)
    st.session_state[f"scr2_carry_rejected__{run_id}"] = sorted(rejected_ids)
    if selected_ids or rejected_ids:
        st.caption(
            f"선택 {len(selected_ids)}건 · 제외 {len(rejected_ids)}건 — "
            "화면③(분석 관점 작성)의 초기값으로 반영됩니다."
        )


# ---------------------------------------------------------------------------
# 화면③ — 분석 관점 작성 (1804 §15 화면3, create-analyst-view)
# ---------------------------------------------------------------------------


def render_screen3(store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[2])
    availability = state.screen3_availability(run_state.current_state)
    if _lock_banner(availability):
        return

    run_id = run_state.run_id
    existing = actions.try_load_analyst_view(store)
    evidence_entries = actions.load_evidence_entries(store)
    evidence_ids = [e.evidence_id for e in evidence_entries]
    fmt_evidence = _evidence_format_func({e.evidence_id: e for e in evidence_entries})
    carry_selected = st.session_state.get(f"scr2_carry_selected__{run_id}", [])
    carry_rejected = st.session_state.get(f"scr2_carry_rejected__{run_id}", [])

    default_view_id = existing.view_id if existing else f"view-{run_id}"
    default_author = existing.author if existing else ""
    default_question = existing.research_question if existing else ""
    default_thesis = existing.core_thesis if existing else ""
    default_selected = existing.selected_evidence_ids if existing else carry_selected
    default_rejected = existing.rejected_evidence_ids if existing else carry_rejected
    default_sel_reason = existing.evidence_selection_reason if existing else ""
    default_interpretation = existing.interpretation if existing else ""
    default_mechanism = existing.expected_mechanism if existing else ""
    default_counter = "\n".join(existing.counterarguments) if existing else ""
    default_uncertain = "\n".join(existing.uncertainties) if existing else ""

    disabled = availability.read_only
    view_id = st.text_input(
        "view_id", value=default_view_id, key=f"scr3_view_id__{run_id}", disabled=disabled
    )
    author = st.text_input(
        "작성자", value=default_author, key=f"scr3_author__{run_id}", disabled=disabled
    )
    research_question = st.text_area(
        "분석 질문", value=default_question, key=f"scr3_question__{run_id}", disabled=disabled
    )
    core_thesis = st.text_area(
        "핵심 논지", value=default_thesis, key=f"scr3_thesis__{run_id}", disabled=disabled
    )
    selected_evidence_ids = st.multiselect(
        "선택한 근거",
        options=evidence_ids,
        default=[e for e in default_selected if e in evidence_ids],
        format_func=fmt_evidence,
        key=f"scr3_selected__{run_id}",
        disabled=disabled,
    )
    rejected_evidence_ids = st.multiselect(
        "제외한 근거",
        options=evidence_ids,
        default=[e for e in default_rejected if e in evidence_ids],
        format_func=fmt_evidence,
        key=f"scr3_rejected__{run_id}",
        disabled=disabled,
    )
    evidence_selection_reason = st.text_area(
        "근거 선택 이유",
        value=default_sel_reason,
        key=f"scr3_sel_reason__{run_id}",
        disabled=disabled,
    )
    rejected_reasons: dict[str, str] = {}
    if rejected_evidence_ids:
        st.write("제외 이유(근거별):")
        for eid in rejected_evidence_ids:
            prior = existing.rejected_evidence_reasons.get(eid, "") if existing else ""
            rejected_reasons[eid] = st.text_input(
                f"- {fmt_evidence(eid)}",
                value=prior,
                key=f"scr3_rej_reason_{eid}__{run_id}",
                disabled=disabled,
            )
    interpretation = st.text_area(
        "해석",
        value=default_interpretation,
        key=f"scr3_interpretation__{run_id}",
        disabled=disabled,
    )
    expected_mechanism = st.text_area(
        "예상 메커니즘", value=default_mechanism, key=f"scr3_mechanism__{run_id}", disabled=disabled
    )
    counterarguments = st.text_area(
        "반대 논리(줄바꿈으로 구분, 최소 1개)",
        value=default_counter,
        key=f"scr3_counter__{run_id}",
        disabled=disabled,
    )
    uncertainties = st.text_area(
        "불확실성(줄바꿈으로 구분)",
        value=default_uncertain,
        key=f"scr3_uncertain__{run_id}",
        disabled=disabled,
    )

    if disabled:
        return

    if st.button("분석 관점 저장", key=f"scr3_save_btn__{run_id}"):
        now = now_kst_iso()
        try:
            view = AnalystView(
                view_id=view_id,
                author=author,
                research_question=research_question,
                core_thesis=core_thesis,
                selected_evidence_ids=selected_evidence_ids,
                rejected_evidence_ids=rejected_evidence_ids,
                evidence_selection_reason=evidence_selection_reason,
                rejected_evidence_reasons=rejected_reasons,
                interpretation=interpretation,
                expected_mechanism=expected_mechanism,
                counterarguments=_lines(counterarguments),
                uncertainties=_lines(uncertainties),
                created_at=existing.created_at if existing else now,
                updated_at=now,
            )
        except PydanticValidationError as err:
            st.error(str(err))
            return
        outcome = _run_action(
            lambda: actions.save_analyst_view(store, run_state, view),
            success_message="분석 관점을 저장했습니다.",
        )
        if outcome is not None:
            _toast_advance(3)
            st.rerun()


# ---------------------------------------------------------------------------
# 화면④ — 투자 가설 작성 (1804 §15 화면4, create-hypothesis)
# ---------------------------------------------------------------------------


def render_screen4(store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[3])
    availability = state.screen4_availability(run_state.current_state)
    if _lock_banner(availability):
        return

    run_id = run_state.run_id
    existing = actions.try_load_human_hypothesis(store)
    analyst_view = actions.try_load_analyst_view(store)
    evidence_entries = actions.load_evidence_entries(store)
    evidence_ids = [e.evidence_id for e in evidence_entries]
    fmt_evidence = _evidence_format_func({e.evidence_id: e for e in evidence_entries})

    default_id = existing.hypothesis_id if existing else f"hyp-{run_id}"
    default_view_id = (
        existing.view_id if existing else (analyst_view.view_id if analyst_view else "")
    )
    default_author = existing.author if existing else ""
    default_thesis = existing.thesis if existing else ""
    default_rationale = existing.economic_rationale if existing else ""
    default_mechanism = existing.expected_mechanism if existing else ""
    default_variables = ", ".join(existing.selected_variables) if existing else ""
    default_direction = existing.expected_direction if existing else "up"
    default_horizon = existing.investment_horizon_days if existing else 60
    default_evidence = (
        existing.evidence_ids
        if existing
        else (analyst_view.selected_evidence_ids if analyst_view else [])
    )
    default_falsification = "\n".join(existing.falsification_conditions) if existing else ""
    default_limitations = "\n".join(existing.limitations) if existing else ""
    default_unsupported = ", ".join(existing.unsupported_variables) if existing else ""

    disabled = availability.read_only
    hypothesis_id = st.text_input(
        "hypothesis_id", value=default_id, key=f"scr4_id__{run_id}", disabled=disabled
    )
    view_id = st.text_input(
        "view_id", value=default_view_id, key=f"scr4_view_id__{run_id}", disabled=disabled
    )
    author = st.text_input(
        "작성자", value=default_author, key=f"scr4_author__{run_id}", disabled=disabled
    )
    thesis = st.text_area(
        "가설", value=default_thesis, key=f"scr4_thesis__{run_id}", disabled=disabled
    )
    economic_rationale = st.text_area(
        "경제적 근거", value=default_rationale, key=f"scr4_rationale__{run_id}", disabled=disabled
    )
    expected_mechanism = st.text_area(
        "예상 메커니즘", value=default_mechanism, key=f"scr4_mechanism__{run_id}", disabled=disabled
    )
    variables_text = st.text_input(
        "변수(쉼표로 구분)",
        value=default_variables,
        key=f"scr4_variables__{run_id}",
        disabled=disabled,
    )
    expected_direction = st.selectbox(
        "예상 방향",
        options=["up", "down", "neutral"],
        index=["up", "down", "neutral"].index(default_direction)
        if default_direction in ("up", "down", "neutral")
        else 0,
        key=f"scr4_direction__{run_id}",
        disabled=disabled,
    )
    investment_horizon_days = st.number_input(
        "보유기간(거래일)",
        value=int(default_horizon),
        step=1,
        min_value=1,
        key=f"scr4_horizon__{run_id}",
        disabled=disabled,
    )
    evidence_ids_selected = st.multiselect(
        "근거",
        options=evidence_ids,
        default=[e for e in default_evidence if e in evidence_ids],
        format_func=fmt_evidence,
        key=f"scr4_evidence__{run_id}",
        disabled=disabled,
    )
    falsification_text = st.text_area(
        "반증 조건(줄바꿈으로 구분, 최소 1개)",
        value=default_falsification,
        key=f"scr4_falsification__{run_id}",
        disabled=disabled,
    )
    limitations_text = st.text_area(
        "한계(줄바꿈으로 구분)",
        value=default_limitations,
        key=f"scr4_limitations__{run_id}",
        disabled=disabled,
    )
    unsupported_text = st.text_input(
        "Indicator Registry 미지원 변수(쉼표로 구분, 있는 경우만)",
        value=default_unsupported,
        key=f"scr4_unsupported__{run_id}",
        disabled=disabled,
    )

    if disabled:
        return

    def _build_draft(status: HypothesisStatus) -> HumanInvestmentHypothesis:
        now = now_kst_iso()
        return HumanInvestmentHypothesis(
            hypothesis_id=hypothesis_id,
            view_id=view_id,
            author=author,
            thesis=thesis,
            economic_rationale=economic_rationale,
            expected_mechanism=expected_mechanism,
            selected_variables=[v.strip() for v in variables_text.split(",") if v.strip()],
            expected_direction=expected_direction,
            investment_horizon_days=int(investment_horizon_days),
            evidence_ids=evidence_ids_selected,
            falsification_conditions=_lines(falsification_text),
            limitations=_lines(limitations_text),
            unsupported_variables=[v.strip() for v in unsupported_text.split(",") if v.strip()],
            status=status,
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("초안 저장(DRAFT)", key=f"scr4_save_draft_btn__{run_id}"):
            try:
                hypothesis = _build_draft(HypothesisStatus.DRAFT)
            except PydanticValidationError as err:
                st.error(str(err))
            else:
                outcome = _run_action(
                    lambda: actions.save_hypothesis(store, run_state, hypothesis),
                    success_message="투자 가설 초안을 저장했습니다.",
                )
                if outcome is not None:
                    st.rerun()
    with col2:
        approved_by = st.text_input("승인자(approved_by)", key=f"scr4_approved_by__{run_id}")
        if st.button("승인(APPROVED)", key=f"scr4_approve_btn__{run_id}"):
            if not approved_by.strip():
                st.error("approved_by를 입력해야 승인할 수 있습니다.")
            else:
                try:
                    draft_hyp = _build_draft(HypothesisStatus.DRAFT)
                    approved_hyp = actions.approve_hypothesis_draft(
                        draft_hyp, approved_by=approved_by
                    )
                except (PydanticValidationError, DataValidationError) as err:
                    st.error(str(err))
                else:
                    outcome = _run_action(
                        lambda: actions.save_hypothesis(store, run_state, approved_hyp),
                        success_message="투자 가설을 승인했습니다.",
                    )
                    if outcome is not None:
                        _toast_advance(4)
                        st.rerun()


# ---------------------------------------------------------------------------
# 화면⑤ — 전략 초안 검토 (1804 §15 화면5, generate-strategy-draft·approve-strategy)
# ---------------------------------------------------------------------------


def _numeric_field_key(item: dict[str, object]) -> str | None:
    for key in ("right", "value"):
        value = item.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool):
            return key
    return None


def _condition_label(item: dict[str, object]) -> str:
    if "left" in item and "operator" in item:
        return f"{item.get('left')} {item.get('operator')}"
    if "type" in item:
        return str(item.get("type"))
    return "값"


def _numeric_input(label: str, original: object, *, key: str) -> int | float:
    if isinstance(original, int) and not isinstance(original, bool):
        return st.number_input(label, value=int(original), step=1, key=key)
    value = float(cast("float", original))
    return st.number_input(label, value=value, step=0.01, format="%.4f", key=key)


def _render_editable_items(items: object, *, key_prefix: str) -> list[object]:
    """entry.all(any) / exit.any 항목을 렌더링하고 숫자 필드만 편집 가능하게 한다."""
    if not isinstance(items, list):
        st.json(items)
        return []
    updated: list[object] = []
    for i, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            st.json(raw_item)
            updated.append(raw_item)
            continue
        item = dict(raw_item)
        numeric_key = _numeric_field_key(item)
        if numeric_key is None:
            st.caption(json.dumps(item, ensure_ascii=False))
            updated.append(item)
            continue
        label = _condition_label(item)
        edited = _numeric_input(label, item[numeric_key], key=f"{key_prefix}_{i}")
        item[numeric_key] = edited
        updated.append(item)
    return updated


def _entry_branch(entry: object) -> tuple[str, object] | None:
    if not isinstance(entry, dict):
        return None
    for branch in ("all", "any"):
        value = entry.get(branch)
        if isinstance(value, list):
            return branch, value
    return None


def render_screen5(settings: Settings, store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[4])
    availability = state.screen5_availability(run_state.current_state)
    if _lock_banner(availability):
        return

    run_id = run_state.run_id
    draft = actions.try_load_strategy_draft(store)
    can_generate = run_state.current_state in state.SCREEN5_DRAFT_STATES

    if can_generate:
        label = (
            "AI 전략 초안 (재)생성 (예상 ~1분)"
            if draft is not None
            else "AI 전략 초안 생성 (예상 ~1분)"
        )
        if st.button(label, key=f"scr5_generate_btn__{run_id}"):
            generate_start = time.monotonic()
            with st.spinner("전략 초안을 생성하는 중... (예상 ~1분)"):
                draft_outcome = _run_action(
                    lambda: actions.generate_strategy_draft_action(settings, store, run_state)
                )
            if draft_outcome is not None:
                elapsed = time.monotonic() - generate_start
                st.success(f"전략 초안 생성 완료 ({elapsed:.0f}초).")
                st.rerun()

    if draft is None:
        st.write("아직 생성된 전략 초안이 없습니다.")
        return

    _ai_caption()
    st.json(draft)

    if availability.read_only:
        return

    can_approve = run_state.current_state in state.SCREEN5_APPROVE_STATES
    if not can_approve:
        return

    st.write("임계값 수정")
    existing_review = actions.try_load_strategy_review(store)
    base = existing_review.final_strategy if existing_review else draft
    entry = base.get("entry") if isinstance(base, dict) else None
    branch_info = _entry_branch(entry)

    st.markdown("**진입 조건(entry)**")
    if branch_info is None:
        st.json(entry)
        entry_branch, entry_items = "all", []
    else:
        entry_branch, entry_raw_items = branch_info
        entry_items = _render_editable_items(entry_raw_items, key_prefix=f"scr5_entry__{run_id}")

    st.markdown("**청산 조건(exit)**")
    exit_spec = base.get("exit") if isinstance(base, dict) else None
    exit_raw_items = exit_spec.get("any") if isinstance(exit_spec, dict) else None
    exit_items = _render_editable_items(exit_raw_items, key_prefix=f"scr5_exit__{run_id}")

    modification_reason = st.text_area(
        "수정 이유(임계값을 바꾼 경우 필수)", key=f"scr5_mod_reason__{run_id}"
    )
    approval_reason = st.text_area(
        "승인 사유",
        value=existing_review.approval_reason if existing_review else "",
        key=f"scr5_approval_reason__{run_id}",
    )
    approved_by = st.text_input(
        "승인자(approved_by)",
        value=existing_review.approved_by if existing_review else "",
        key=f"scr5_approved_by__{run_id}",
    )

    if st.button("전략 승인", key=f"scr5_approve_btn__{run_id}"):
        if not approved_by.strip():
            st.error("approved_by를 입력해야 승인할 수 있습니다.")
            return
        final_strategy = copy.deepcopy(draft)
        entry_final = final_strategy.get("entry")
        if isinstance(entry_final, dict) and branch_info is not None:
            entry_final[entry_branch] = entry_items
        exit_final = final_strategy.get("exit")
        if isinstance(exit_final, dict):
            exit_final["any"] = exit_items

        has_changes = final_strategy != draft
        if has_changes and not modification_reason.strip():
            st.error("임계값을 수정했다면 수정 이유를 입력하세요.")
            return

        approve_outcome = _run_action(
            lambda: actions.approve_strategy_action(
                store,
                run_state,
                final_strategy=final_strategy,
                approved_by=approved_by,
                approval_reason=approval_reason,
                modification_reason=modification_reason,
            ),
            success_message="전략을 승인했습니다.",
        )
        if approve_outcome is not None:
            _toast_advance(5)
            st.rerun()


# ---------------------------------------------------------------------------
# 화면⑥ — 백테스트 결과 (1804 §15 화면6, backtest)
# ---------------------------------------------------------------------------


def render_screen6(settings: Settings, store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[5])
    availability = state.screen6_availability(run_state.current_state)
    if _lock_banner(availability):
        return

    run_id = run_state.run_id
    blocked_reason = state.screen6_run_blocked_reason(run_state.current_state)

    with st.expander("고급 설정(선택)"):
        start_date = st.date_input(
            "시작일", value=actions.DEFAULT_BACKTEST_START_DATE, key=f"scr6_start__{run_id}"
        )
        end_date = st.date_input("종료일(기본: 기준일)", value=None, key=f"scr6_end__{run_id}")
        benchmark = st.text_input(
            "벤치마크(기본: configs/backtest.yaml)", key=f"scr6_benchmark__{run_id}"
        )

    if blocked_reason:
        st.warning(blocked_reason)
    else:
        if st.button("백테스트 실행", key=f"scr6_run_btn__{run_id}"):
            with st.spinner("백테스트를 실행하는 중..."):
                outcome = _run_action(
                    lambda: actions.run_backtest_action(
                        settings,
                        store,
                        run_state,
                        start_date=start_date,
                        end_date=end_date if isinstance(end_date, date) else None,
                        benchmark=benchmark or None,
                    ),
                    success_message="백테스트 완료.",
                )
            if outcome is not None:
                _toast_advance(6)
                st.rerun()

    result = actions.load_backtest_result(store)
    if result is None:
        st.write("아직 백테스트 결과가 없습니다.")
        return

    st.markdown(f"**{result.strategy_name}** [{result.start_date} ~ {result.end_date}]")
    metrics_rows = [
        {"지표": "누적수익률", "값": result.cumulative_return},
        {"지표": "CAGR", "값": result.cagr},
        {"지표": "샤프", "값": result.sharpe},
        {"지표": "소르티노", "값": result.sortino},
        {"지표": "최대낙폭(MDD)", "값": result.max_drawdown},
        {"지표": "Calmar", "값": result.calmar},
        {"지표": "거래 횟수", "값": result.num_trades},
        {"지표": "승률", "값": result.win_rate},
        {"지표": "평균 보유일", "값": result.avg_holding_days},
        {"지표": "Profit Factor", "값": result.profit_factor},
        {"지표": "시장 노출도", "값": result.market_exposure},
    ]
    st.table(metrics_rows)

    daily = actions.load_daily_portfolio(store)
    if daily is not None and not daily.empty:
        st.markdown("**equity 곡선**")
        st.line_chart(daily.set_index("date")["equity"])

    trades = actions.load_trade_log(store)
    if trades is not None:
        st.markdown(f"**거래내역** ({len(trades)}건)")
        st.dataframe(trades)

    st.markdown("**벤치마크·Buy&Hold 비교**")
    st.table(
        [
            {
                "구분": f"벤치마크({result.benchmark.name})",
                "누적수익률": result.benchmark.cumulative_return,
                "초과수익률": result.benchmark.excess_return,
                "Information Ratio": result.benchmark.information_ratio,
            },
            {
                "구분": "Buy & Hold",
                "누적수익률": result.buy_hold.cumulative_return,
                "CAGR": result.buy_hold.cagr,
                "MDD": result.buy_hold.max_drawdown,
            },
        ]
    )

    robustness = actions.load_robustness_report(store)
    if robustness is not None:
        ablation = robustness.get("condition_ablation")
        if isinstance(ablation, list) and ablation:
            st.markdown("**조건 제거(강건성) 분석**")
            st.dataframe(ablation)


# ---------------------------------------------------------------------------
# 화면⑦ — 최종 해석 (1804 §15 화면7, submit-interpretation)
# ---------------------------------------------------------------------------


def render_screen7(store: RunStore, run_state: RunState) -> None:
    st.subheader(state.SCREEN_TITLES[6])
    availability = state.screen7_availability(run_state.current_state)
    if _lock_banner(availability):
        return

    run_id = run_state.run_id
    hypothesis = actions.try_load_human_hypothesis(store)
    existing = actions.try_load_backtest_interpretation(store)

    default_id = existing.interpretation_id if existing else f"interp-{run_id}"
    default_hyp_id = (
        existing.hypothesis_id if existing else (hypothesis.hypothesis_id if hypothesis else "")
    )
    default_strategy_id = (
        existing.strategy_id if existing else (actions.load_strategy_name(store) or "")
    )
    default_author = existing.author if existing else ""

    interpretation_id = st.text_input(
        "interpretation_id", value=default_id, key=f"scr7_id__{run_id}"
    )
    hypothesis_id = st.text_input(
        "hypothesis_id", value=default_hyp_id, key=f"scr7_hyp_id__{run_id}"
    )
    strategy_id = st.text_input(
        "strategy_id", value=default_strategy_id, key=f"scr7_strategy_id__{run_id}"
    )
    author = st.text_input("작성자", value=default_author, key=f"scr7_author__{run_id}")
    main_findings = st.text_area(
        "주요 발견", value=existing.main_findings if existing else "", key=f"scr7_main__{run_id}"
    )
    supporting_text = st.text_area(
        "가설에 유리한 결과(줄바꿈으로 구분)",
        value="\n".join(existing.supporting_results) if existing else "",
        key=f"scr7_supporting__{run_id}",
    )
    contradicting_text = st.text_area(
        "가설에 불리한 결과(줄바꿈으로 구분)",
        value="\n".join(existing.contradicting_results) if existing else "",
        key=f"scr7_contradicting__{run_id}",
    )
    regime_dependence = st.text_input(
        "국면 의존성(선택)",
        value=_or_blank(existing.regime_dependence) if existing else "",
        key=f"scr7_regime__{run_id}",
    )
    limitations_text = st.text_area(
        "한계(줄바꿈으로 구분)",
        value="\n".join(existing.limitations) if existing else "",
        key=f"scr7_limitations__{run_id}",
    )
    decision_options = list(actions.HYPOTHESIS_DECISION_OPTIONS)
    default_decision_idx = (
        decision_options.index(existing.hypothesis_decision)
        if existing and existing.hypothesis_decision in decision_options
        else 0
    )
    hypothesis_decision = st.selectbox(
        "가설 판정",
        options=decision_options,
        index=default_decision_idx,
        key=f"scr7_decision__{run_id}",
    )
    decision_reason = st.text_area(
        "판정 이유",
        value=existing.decision_reason if existing else "",
        key=f"scr7_reason__{run_id}",
    )
    revised_hypothesis = st.text_area(
        "수정 가설(REVISED인 경우 필수)",
        value=_or_blank(existing.revised_hypothesis) if existing else "",
        key=f"scr7_revised__{run_id}",
    )
    followup_text = st.text_area(
        "추가 검증(줄바꿈으로 구분)",
        value="\n".join(existing.followup_tests) if existing else "",
        key=f"scr7_followup__{run_id}",
    )

    if st.button("결과 해석 제출", key=f"scr7_submit_btn__{run_id}"):
        try:
            interpretation = BacktestInterpretation(
                interpretation_id=interpretation_id,
                hypothesis_id=hypothesis_id,
                strategy_id=strategy_id,
                author=author,
                main_findings=main_findings,
                supporting_results=_lines(supporting_text),
                contradicting_results=_lines(contradicting_text),
                regime_dependence=regime_dependence or None,
                limitations=_lines(limitations_text),
                hypothesis_decision=hypothesis_decision,
                decision_reason=decision_reason,
                revised_hypothesis=revised_hypothesis or None,
                followup_tests=_lines(followup_text),
                created_at=now_kst_iso(),
            )
        except PydanticValidationError as err:
            st.error(str(err))
            return
        outcome = _run_action(
            lambda: actions.submit_interpretation_action(store, run_state, interpretation),
            success_message="결과 해석을 제출했습니다.",
        )
        if outcome is not None:
            st.rerun()

    if run_state.current_state == PipelineState.COMPLETE:
        st.success("이 run은 COMPLETE 상태입니다.")
        st.caption(
            "보고서 생성: 터미널에서 `r2b generate-report --run-id " + run_id + "` 를 실행하세요."
        )
