"""Streamlit 엔트리 (docs/specs/W3c-report-ui.md §3.1, docs/specs/W3e-ui-ux.md F2, S1 소유).

실행: ``streamlit run src/research_backtest/app/streamlit_app.py``.

사이드바(run 선택·상태 배지·화면 이동) + 본문 1화면 렌더링으로 구성한다.
``st.tabs``\\ 는 rerun마다 첫 탭으로 리셋되어(W3e-ui-ux.md U1a) 저장 버튼이
끝날 때마다 호출하는 ``st.rerun()``\\ 이 항상 화면①로 되돌렸다 — 사이드바
radio 네비게이션(session_state 보존, :func:`screens.render_nav`)으로 대체해
저장·전이 후에도 현재 화면이 유지된다(F2). 매 rerun마다 선택된 화면 1개만
렌더한다(F2 "화면 렌더 1개만" 단순화) — 잠긴 화면은 해당 ``render_screenN``\\
이 잠금 배너를 그린다. 비즈니스 로직은 전혀 갖지 않는다 —
:mod:`research_backtest.app.ui.state`\\ 로 잠금 여부를 읽고,
:mod:`research_backtest.app.ui.screens`\\ 의 렌더 함수를 호출할 뿐이다.
``app/cli.py``·``app/commands/``는 import하지 않는다(§1).
"""

from __future__ import annotations

import streamlit as st  # streamlit 1.59는 py.typed로 인라인 타입을 배포한다(stub 부재 아님).
from pydantic import ValidationError as PydanticValidationError

from research_backtest.app.ui import screens, state
from research_backtest.core.config import get_settings
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.store import RunStore

st.set_page_config(page_title="Research-to-Backtest", layout="wide")

st.title("Research-to-Backtest — Human-in-the-Loop")
st.caption(
    "AI는 사실과 후보 관계를 정리하는 보조 도구다. 분석 관점·핵심 논지·근거 선택·"
    "투자 가설·전략 승인·결과 해석은 사용자가 담당한다(docs/HUMAN_IN_THE_LOOP.md)."
)

_settings = get_settings()
_selected_run_id = screens.render_sidebar(_settings)
_nav_index = screens.render_nav()

if _selected_run_id is None:
    st.info("사이드바에서 run을 선택하거나, 화면①에서 새 run을 생성하세요.")
    if _nav_index == 0:
        screens.render_screen1(_settings, None)
    else:
        st.subheader(state.SCREEN_TITLES[_nav_index])
        st.info("먼저 사이드바에서 run을 선택하거나 생성하세요.")
else:
    _store = RunStore(_settings.outputs_dir, _selected_run_id)
    try:
        _run_state = _store.load_run_state()
    except (DataValidationError, PydanticValidationError) as err:
        st.error(f"run_state를 읽을 수 없습니다: {err}")
    else:
        st.markdown(
            f"### 현재 상태: {state.PIPELINE_STATE_LABELS[_run_state.current_state]} "
            f"(run: `{_run_state.run_id}`)"
        )
        st.caption(f"다음 단계: {state.NEXT_STEP_HINTS[_run_state.current_state]}")

        if _nav_index == 0:
            screens.render_screen1(_settings, _selected_run_id)
        elif _nav_index == 1:
            screens.render_screen2(_settings, _store, _run_state)
        elif _nav_index == 2:
            screens.render_screen3(_store, _run_state)
        elif _nav_index == 3:
            screens.render_screen4(_store, _run_state)
        elif _nav_index == 4:
            screens.render_screen5(_settings, _store, _run_state)
        elif _nav_index == 5:
            screens.render_screen6(_settings, _store, _run_state)
        else:
            screens.render_screen7(_store, _run_state)
