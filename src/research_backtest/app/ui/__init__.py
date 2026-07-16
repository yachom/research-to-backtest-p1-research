"""Streamlit UI 계층 (docs/specs/W3c-report-ui.md §3, S1 소유).

- :mod:`.state` — 조회 전용: run 목록 스캔, 화면별 잠금(lock) 판정, 상태 배지 문구.
- :mod:`.actions` — 쓰기·상태 전이: core/hitl·research·quant API를 CLI
  (``app/commands/hitl_flow.py``·``app/commands/backtest_cmd.py``)와 동일한
  순서로 직접 조립한다(비즈니스 로직 재구현 금지 — 각 함수 docstring에 대응
  CLI 명령을 기록한다).
- :mod:`.screens` — 7화면(1804 §15) 렌더링. state·actions만 소비하고
  ``streamlit_app.py``가 엔트리에서 라우팅한다.

이 패키지는 ``app/cli.py``·``app/commands/``를 import하지 않는다(§1 파일
소유권 — typer 명령 함수 호출 금지).
"""
