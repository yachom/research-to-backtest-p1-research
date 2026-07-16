"""backtest 서브커맨드 — 승인된 실행(run)을 Point-in-Time 백테스트한다 (명세 §4.4).

docs/specs/CLI-integration.md §4.4(T1) + 1804_FEEDBACK.md §14의 ``--run-id`` 형태
구현. 기존 ``--hypothesis/--start-date/--end-date`` 필수형 스텁을 대체한다.

승인 게이트를 절대 우회하지 않는다(CLAUDE.md §3): run_state가 STRATEGY_APPROVED
이상이어야 하고, 승인 가설·전략 리뷰가 모두 존재·정합해야 하며, 실제 실행은
``execute_approved_strategy``가 다시 한번 게이트를 강제한다(방어선 중첩). run_manifest는
T2 모델에 의존하지 않도록 json으로 직접 로드한다(명세 §6.1 병렬 개발 격리).

종료 코드(명세 §3): 0 성공 / 1 실행·검증·데이터 오류 / 3 설정 오류 /
4 승인 게이트 차단(ApprovalGateError).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from research_backtest.core.config import get_settings
from research_backtest.core.exceptions import (
    ApprovalGateError,
    ConfigError,
    DataValidationError,
    LookaheadError,
    StrategyValidationError,
)
from research_backtest.core.hitl import gates
from research_backtest.core.hitl.states import PipelineState, advance
from research_backtest.core.hitl.store import RunStore
from research_backtest.quant.backtest.costs import load_backtest_config
from research_backtest.quant.backtest.metrics import BacktestResult
from research_backtest.quant.backtest.runner import (
    BACKTEST_RESULT_FILENAME,
    DAILY_PORTFOLIO_FILENAME,
    TRADE_LOG_FILENAME,
    execute_approved_strategy,
)

console = Console()

# 종료 코드 (명세 §3 — 각 모듈 로컬 상수, 값 고정).
DATA_ERROR_EXIT_CODE = 1
CONFIG_ERROR_EXIT_CODE = 3
GATE_BLOCKED_EXIT_CODE = 4

# 백테스트 시작일 기본값 — 데이터 경계 (README §26.6·정오표 #5, 명세 §4.4).
DEFAULT_START_DATE = date(2016, 1, 1)

RUN_MANIFEST_FILENAME = "run_manifest.json"
_CREATE_RUN_HINT = "r2b create-run 으로 실행(run)을 먼저 등록하세요."

# 상태별 다음 단계 안내 (명세 §6.3 "다음 단계" 열 — 상태 표시 2줄에 사용).
_NEXT_STEP: dict[PipelineState, str] = {
    PipelineState.BACKTEST_COMPLETE: "submit-interpretation",
    PipelineState.AWAITING_INTERPRETATION: "submit-interpretation",
}


def backtest(
    run_id: Annotated[str, typer.Option("--run-id", help="백테스트할 실행(run) 식별자")],
    start_date: Annotated[
        str | None,
        typer.Option("--start-date", help="백테스트 시작일 YYYY-MM-DD (기본: 2016-01-01)"),
    ] = None,
    end_date: Annotated[
        str | None,
        typer.Option("--end-date", help="백테스트 종료일 YYYY-MM-DD (기본: run as_of_date)"),
    ] = None,
    fs_scope: Annotated[
        str, typer.Option("--fs-scope", help="재무 as-of join scope (CFS/OFS, 기본 CFS)")
    ] = "CFS",
    benchmark: Annotated[
        str | None,
        typer.Option("--benchmark", help="벤치마크 지수 (기본: configs/backtest.yaml)"),
    ] = None,
) -> None:
    """승인된 실행의 최종 전략을 실데이터로 백테스트하고 상태를 전이한다 (명세 §4.4).

    절차(순서 고정): run_state 로드 → run_manifest json 로드 → 상태 게이트
    (STRATEGY_APPROVED 이상, COMPLETE는 재백테스트 거부) → 가설·전략 승인 게이트
    → ``execute_approved_strategy``(산출물 3종 저장) → 상태 전이(STRATEGY_APPROVED
    에서만 BACKTEST_COMPLETE→AWAITING_INTERPRETATION, 재실행은 전이 없음).
    LookaheadError는 치명 결함 신호이므로 삼키지 않고 exit 1로 노출한다.
    """
    settings = get_settings()
    start = _parse_iso_date_option(start_date, "--start-date") or DEFAULT_START_DATE

    store = RunStore(settings.outputs_dir, run_id)

    # 1. run_state (부재 → exit 1 + create-run 안내)
    try:
        run_state = store.load_run_state()
    except DataValidationError as err:
        console.print(f"[red]{err}[/red]")
        console.print(_CREATE_RUN_HINT)
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err

    # 2. run_manifest.json — json 직접 로드 (§6.1: corp_code·stock_code·as_of_date만 소비)
    corp_code, stock_code, as_of = _load_manifest(store.run_dir / RUN_MANIFEST_FILENAME)
    end = _parse_iso_date_option(end_date, "--end-date") or as_of
    if start > end:
        raise typer.BadParameter(f"--start-date({start})는 --end-date({end})보다 클 수 없습니다.")

    # 3. 상태 게이트 (COMPLETE는 재백테스트 거부 → exit 4)
    if run_state.current_state == PipelineState.COMPLETE:
        console.print(
            "[red]해석까지 완료된 실행은 재백테스트하지 않습니다 — 새 run을 권장합니다"
            " (r2b create-run).[/red]"
        )
        raise typer.Exit(code=GATE_BLOCKED_EXIT_CODE)
    try:
        gates.ensure_state_at_least(run_state, PipelineState.STRATEGY_APPROVED)
    except ApprovalGateError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=GATE_BLOCKED_EXIT_CODE) from err

    # 4. 가설 게이트(방어선 중첩) + 전략 리뷰 정합
    try:
        hypothesis = store.load_human_hypothesis()
    except DataValidationError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    try:
        gates.ensure_hypothesis_approved(hypothesis)
    except ApprovalGateError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=GATE_BLOCKED_EXIT_CODE) from err
    try:
        review = store.load_strategy_review()
    except DataValidationError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    if review.hypothesis_id != hypothesis.hypothesis_id:
        console.print(
            "[red]전략 리뷰의 hypothesis_id가 승인 가설과 일치하지 않습니다: "
            f"review={review.hypothesis_id!r} vs hypothesis={hypothesis.hypothesis_id!r}.[/red]"
        )
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)

    # 5. 설정 로드 + 실행 (runner가 1차 게이트·산출물 3종 저장 담당)
    try:
        config = load_backtest_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err
    if benchmark is not None:
        config = config.model_copy(update={"benchmark": benchmark})

    try:
        result = execute_approved_strategy(
            review,
            data_dir=settings.data_dir,
            stock_code=stock_code,
            corp_code=corp_code,
            start_date=start,
            end_date=end,
            out_dir=store.run_dir,
            backtest_config=config,
            fs_scope=fs_scope,
        )
    except ApprovalGateError as err:  # runner 재검증 방어선
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=GATE_BLOCKED_EXIT_CODE) from err
    except LookaheadError as err:  # 치명 결함 신호 — 절대 삼키지 않는다 (명세 §4.4)
        console.print(f"[red]룩어헤드 검증 실패: {err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    except (StrategyValidationError, DataValidationError, FileNotFoundError) as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err

    # 6. 상태 전이 (STRATEGY_APPROVED에서만 2회 전진, 재실행은 전이 없음)
    if run_state.current_state == PipelineState.STRATEGY_APPROVED:
        run_state = advance(
            run_state,
            PipelineState.BACKTEST_COMPLETE,
            actor="system",
            note=f"{start}~{end} {result.strategy_name}",
        )
        run_state = advance(
            run_state,
            PipelineState.AWAITING_INTERPRETATION,
            actor="system",
            note="사용자 해석 대기",
        )
        store.save_run_state(run_state)
    else:
        console.print("[yellow]재실행 — 상태 전이 없음 (산출물만 갱신)[/yellow]")

    _print_result(result, store.run_dir)
    _print_status(run_state.current_state, run_id)


# --- 헬퍼 --------------------------------------------------------------------


def _parse_iso_date_option(value: str | None, option: str) -> date | None:
    """YYYY-MM-DD 옵션 파싱 — 형식 오류는 BadParameter (cli.py _parse_iso_date_option 복제)."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise typer.BadParameter(f"{option}는 YYYY-MM-DD 형식이어야 합니다: {value!r}") from err


def _load_manifest(path: Path) -> tuple[str, str, date]:
    """run_manifest.json을 json으로 직접 읽어 (corp_code, stock_code, as_of_date)를 반환한다.

    명세 §6.1 계약: T2의 RunManifest 모델에 의존하지 않고 세 필드만 str로 소비한다
    (나머지 키는 무시 — 전방 호환). 부재·필드 누락·형식 오류는 exit 1 + create-run 안내.
    """
    if not path.exists():
        console.print(f"[red]{RUN_MANIFEST_FILENAME}이(가) 없습니다: {path}[/red]")
        console.print(_CREATE_RUN_HINT)
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as err:
        console.print(f"[red]{RUN_MANIFEST_FILENAME}을(를) 읽을 수 없습니다: {err}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    if not isinstance(payload, dict):
        console.print(f"[red]{RUN_MANIFEST_FILENAME} 최상위 타입은 object여야 합니다: {path}[/red]")
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)

    corp_code = _require_str_field(payload, "corp_code", path)
    stock_code = _require_str_field(payload, "stock_code", path)
    as_of_raw = _require_str_field(payload, "as_of_date", path)
    try:
        as_of = date.fromisoformat(as_of_raw)
    except ValueError as err:
        console.print(
            f"[red]{RUN_MANIFEST_FILENAME}의 as_of_date 형식 오류(YYYY-MM-DD 아님): "
            f"{as_of_raw!r}[/red]"
        )
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE) from err
    return corp_code, stock_code, as_of


def _require_str_field(payload: dict[str, Any], key: str, path: Path) -> str:
    """manifest에서 비어있지 않은 str 필드를 꺼낸다 — 없으면 exit 1 + create-run 안내."""
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        console.print(f"[red]{RUN_MANIFEST_FILENAME}에 {key}이(가) 없습니다: {path}[/red]")
        console.print(_CREATE_RUN_HINT)
        raise typer.Exit(code=DATA_ERROR_EXIT_CODE)
    return value


def _fmt(value: float | None, *, percent: bool = False) -> str:
    """지표 값 포맷 — None은 '-', percent면 백분율."""
    if value is None:
        return "-"
    return f"{value * 100:.2f}%" if percent else f"{value:.4f}"


def _print_result(result: BacktestResult, run_dir: Path) -> None:
    """성과 요약 테이블 + 산출물 경로 3종 (명세 §4.4)."""
    table = Table(
        title=f"백테스트 성과 — {result.strategy_name} [{result.start_date}~{result.end_date}]"
    )
    for column in ("지표", "값"):
        table.add_column(column)
    table.add_row("누적수익률", _fmt(result.cumulative_return, percent=True))
    table.add_row("CAGR", _fmt(result.cagr, percent=True))
    table.add_row("샤프", _fmt(result.sharpe))
    table.add_row("최대낙폭(MDD)", _fmt(result.max_drawdown, percent=True))
    table.add_row("거래 횟수", str(result.num_trades))
    table.add_row("승률", _fmt(result.win_rate, percent=True))
    table.add_row("Profit Factor", _fmt(result.profit_factor))
    table.add_row(
        f"벤치마크 초과수익 ({result.benchmark.name})",
        _fmt(result.benchmark.excess_return, percent=True),
    )
    console.print(table)
    console.print(f"산출물 — 결과: {run_dir / BACKTEST_RESULT_FILENAME}")
    console.print(f"산출물 — 체결 로그: {run_dir / TRADE_LOG_FILENAME}")
    console.print(f"산출물 — 일별 포트폴리오: {run_dir / DAILY_PORTFOLIO_FILENAME}")


def _print_status(state: PipelineState, run_id: str) -> None:
    """공통 상태 표시 2줄 (명세 §3 — T1·T2 동일 문자열 포맷)."""
    console.print(f"파이프라인 상태: {state.value}  (run: {run_id})")
    console.print(f"다음 단계: {_NEXT_STEP.get(state, 'submit-interpretation')}")


# --- 등록 (명세 §3·§4.6) -----------------------------------------------------


def register(app: typer.Typer) -> None:
    """backtest 명령을 루트 앱에 등록한다 (명세 §3 register 패턴)."""
    app.command("backtest")(backtest)
