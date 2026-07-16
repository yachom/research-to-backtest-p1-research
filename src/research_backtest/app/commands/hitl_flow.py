"""HITL 워크플로 CLI 명령 (docs/specs/CLI-integration.md §5, T2 소유).

``create-run``으로 실행(run)을 등록한 뒤, 1804 §14의 명령들
(``create-analyst-view``·``create-hypothesis``·``approve-strategy``·
``submit-interpretation``과 상태 인지형 스텁 3종)이 전부 ``--run-id`` 기반으로
승인 게이트 검사·산출물 저장·상태 전이를 수행한다. ``run``·``status``로 현재
상태를 확인한다. ``register(app)``만 노출하며 ``app/cli.py``는 건드리지
않는다 — 등록은 병합 시 메인 세션이 수행한다(app/commands/__init__.py 참고).
"""

import json
import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

import typer
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError
from rich.console import Console
from rich.table import Table

from research_backtest.core.config import Settings, get_settings, load_dart_config
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import corp_code_cache_dir, load_corp_code_registry
from research_backtest.core.dart.models import ResolveResult
from research_backtest.core.exceptions import (
    ApprovalGateError,
    ConfigError,
    DartApiError,
    DartTransportError,
    DataValidationError,
    StrategyValidationError,
)
from research_backtest.core.financials.pipeline import METRICS_FILENAME, financials_out_dir
from research_backtest.core.hitl.diff import diff_strategies
from research_backtest.core.hitl.gates import ensure_hypothesis_approved, ensure_state_at_least
from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    HumanInvestmentHypothesis,
    HypothesisCandidate,
    HypothesisStatus,
    RunManifest,
    StrategyReview,
    now_kst_iso,
)
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    RunState,
    advance,
    create_run_state,
    generate_run_id,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.hitl.validation import (
    FileEvidenceStore,
    validate_analyst_view,
    validate_hypothesis,
)
from research_backtest.core.llm import LlmCallMetadata, create_llm_client, load_llm_config
from research_backtest.core.market.collector import (
    DAILY_FILENAME,
    market_calendar_path,
    market_normalized_stock_dir,
)
from research_backtest.core.models import DartCorporation
from research_backtest.quant.backtest.costs import BacktestConfig
from research_backtest.quant.backtest.metrics import BacktestResult
from research_backtest.quant.backtest.robustness import run_robustness
from research_backtest.quant.strategy.compiler import compile_strategy
from research_backtest.quant.strategy.draft import DEFAULT_PROMPTS_DIR, draft_strategy
from research_backtest.quant.strategy.registry import resolve_indicator
from research_backtest.quant.strategy.schema import parse_strategy_spec
from research_backtest.research.candidates.generator import (
    CANDIDATE_ANALYSIS_PROMPT_NAME,
    HYPOTHESIS_CANDIDATE_PROMPT_NAME,
    PROMPTS_DIR,
    generate_candidate_analysis,
    generate_hypothesis_candidates,
)
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    build_financial_evidence,
)
from research_backtest.research.report import build_research_report, draft_result_explanation

console = Console()

KST = ZoneInfo("Asia/Seoul")

# --- 종료 코드 (§3 — 값 고정, 모듈 로컬 상수) ---------------------------------------
VALIDATION_ERROR_EXIT_CODE = 1
NOT_IMPLEMENTED_EXIT_CODE = 2
CONFIG_ERROR_EXIT_CODE = 3
GATE_BLOCKED_EXIT_CODE = 4  # ApprovalGateError 전용 (신설, §3)

# 명령 실행 후 현재 상태에서 다음에 실행할 명령 안내 (§6.3 표의 "다음 단계" 열).
_NEXT_STEP_HINTS: dict[PipelineState, str] = {
    PipelineState.DATA_READY: "generate-candidates",
    PipelineState.CANDIDATE_ANALYSIS_READY: "create-analyst-view",
    PipelineState.AWAITING_ANALYST_VIEW: "create-analyst-view",
    PipelineState.ANALYST_VIEW_APPROVED: "create-hypothesis",
    PipelineState.HYPOTHESIS_DRAFT: "create-hypothesis (APPROVED 입력으로 승인)",
    PipelineState.HYPOTHESIS_APPROVED: "generate-strategy-draft",
    PipelineState.STRATEGY_DRAFT_READY: "approve-strategy",
    PipelineState.AWAITING_STRATEGY_REVIEW: "approve-strategy",
    PipelineState.STRATEGY_APPROVED: "backtest",
    PipelineState.BACKTEST_COMPLETE: "submit-interpretation",
    PipelineState.AWAITING_INTERPRETATION: "submit-interpretation",
    PipelineState.COMPLETE: "generate-report",
}

# docs/OUTPUT_SCHEMA.md §0 산출물 체크리스트 순서. backtest 산출물은 OUTPUT_SCHEMA의
# "charts/" 대신 A6 runner가 실제로 저장하는 파일명(daily_portfolio.csv)을 쓴다
# (docs/specs/CLI-integration.md §6.2 — backtest 산출물 3종 계약).
_ARTIFACT_CHECKLIST: tuple[str, ...] = (
    "run_manifest.json",
    "run_state.json",
    "evidence_manifest.json",
    "candidate_analysis.json",
    "hypothesis_candidates.json",
    "analyst_view.json",
    "human_investment_hypothesis.json",
    "strategy_draft.json",
    "strategy_review.json",
    "strategy_spec.json",
    "backtest_result.json",
    "trade_log.csv",
    "daily_portfolio.csv",
    "backtest_interpretation.json",
    "ai_usage_log.jsonl",
    "research_report.md",
)

# 가설 판정(1804 §10) → HumanInvestmentHypothesis.status 매핑 (§5.6).
_DECISION_TO_STATUS: dict[str, HypothesisStatus] = {
    "SUPPORTED": HypothesisStatus.SUPPORTED,
    "PARTIALLY_SUPPORTED": HypothesisStatus.PARTIALLY_SUPPORTED,
    "REJECTED": HypothesisStatus.REJECTED,
    "REVISED": HypothesisStatus.REVISED,
    "INCONCLUSIVE": HypothesisStatus.TESTED,  # INCONCLUSIVE만 다른 이름의 status로 매핑
}


# ---------------------------------------------------------------------------
# 예외 → 종료 코드 통일 매핑 (§3)
# ---------------------------------------------------------------------------


@contextmanager
def _gate_guard() -> Iterator[None]:
    """블록 안에서 발생한 ``ApprovalGateError``를 exit 4로 통일 처리한다 (§3)."""
    try:
        yield
    except ApprovalGateError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=GATE_BLOCKED_EXIT_CODE) from err


@contextmanager
def _validation_guard() -> Iterator[None]:
    """블록 안에서 발생한 ``DataValidationError``·``StrategyValidationError``를
    exit 1로 통일 처리한다 (§3, 메시지 그대로 출력)."""
    try:
        yield
    except (DataValidationError, StrategyValidationError) as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err


# ---------------------------------------------------------------------------
# 공통 헬퍼 (§3)
# ---------------------------------------------------------------------------


def _parse_iso_date_option(value: str | None, option: str) -> date | None:
    """YYYY-MM-DD 옵션 파싱 — 형식 오류는 BadParameter (app/cli.py 관례 복제, §3)."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as err:
        raise typer.BadParameter(f"{option}는 YYYY-MM-DD 형식이어야 합니다: {value!r}") from err


def _resolve_corp(company: str, settings: Settings) -> DartCorporation:
    """기업명·종목코드로 :class:`DartCorporation`을 식별한다 (§3 공통 규약).

    app/cli.py의 ``_resolve_or_exit``·``_print_resolve_failure``와 동일한
    동작을 이 모듈 안에 복제한다 — 커맨드 모듈 간 import는 금지되어 있으므로
    (§3) 중복을 허용하고, 병합 후 통합 정리는 메인 세션 후속 작업이다.
    """
    try:
        api_key = settings.require_dart_api_key()
        dart_config = load_dart_config()
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    try:
        with DartClient(
            api_key,
            timeout=dart_config.timeout_seconds,
            max_attempts=dart_config.retry.max_attempts,
            backoff_seconds=dart_config.retry.backoff_seconds,
        ) as client:
            registry = load_corp_code_registry(
                client,
                corp_code_cache_dir(settings.data_dir),
                refresh_days=dart_config.corp_code_cache.refresh_days,
            )
            result = registry.resolve(company)
    except (DartApiError, DartTransportError) as err:
        console.print(f"[red]DART 호출 실패: {err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err

    if result.matched is None:
        _print_resolve_failure(company, result)
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)
    return result.matched


def _print_resolve_failure(query: str, result: ResolveResult) -> None:
    """AMBIGUOUS는 후보 테이블, NOT_FOUND는 안내 메시지 (app/cli.py와 동일 동작)."""
    if result.method == "AMBIGUOUS":
        console.print(f"[yellow]'{query}'에 대한 후보가 여러 개입니다 (AMBIGUOUS).[/yellow]")
        table = Table(title="후보 기업 (상장 우선, 최대 10)")
        for column in ("corp_code", "stock_code", "corp_name"):
            table.add_column(column)
        for corp in result.candidates:
            table.add_row(corp.corp_code, corp.stock_code or "-", corp.corp_name)
        console.print(table)
    else:
        console.print(f"[red]'{query}'에 해당하는 기업을 찾지 못했습니다 (NOT_FOUND).[/red]")
    console.print("6자리 종목코드로 다시 시도하면 정확히 식별됩니다 (예: --company 000660).")


def _git_short_hash() -> str | None:
    """현재 커밋의 짧은 해시 — best-effort(git 부재·실패 시 None, §5.1)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    hash_value = result.stdout.strip()
    return hash_value or None


def _ensure_data_ready(corp: DartCorporation, stock_code: str, settings: Settings) -> None:
    """create-run의 데이터 준비 검사(§5.1) — 수집을 트리거하지 않고 존재만 확인한다."""
    missing: list[str] = []

    metrics_path = financials_out_dir(settings.data_dir, corp.corp_code) / METRICS_FILENAME
    if not metrics_path.exists():
        missing.append(
            f"재무 지표({metrics_path}) — "
            f'r2b build-financials --company "{corp.corp_name}" 먼저 실행하세요.'
        )
    daily_path = market_normalized_stock_dir(settings.data_dir, stock_code) / DAILY_FILENAME
    if not daily_path.exists():
        missing.append(
            f"시장 데이터({daily_path}) — "
            f'r2b collect-market --company "{corp.corp_name}" 먼저 실행하세요.'
        )
    calendar_path = market_calendar_path(settings.data_dir)
    if not calendar_path.exists():
        missing.append(f"거래일 캘린더({calendar_path}) — r2b collect-market 먼저 실행하세요.")

    if missing:
        console.print("[red]데이터 준비가 완료되지 않았습니다:[/red]")
        for item in missing:
            console.print(f"  - {item}")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)


def _load_run_state_or_exit(store: RunStore) -> RunState:
    with _validation_guard():
        return store.load_run_state()


def _load_evidence_store(store: RunStore) -> FileEvidenceStore:
    with _validation_guard():
        return FileEvidenceStore.from_manifest(store.run_dir / "evidence_manifest.json")


def _load_json_file(path: Path) -> Any:
    if not path.exists():
        raise DataValidationError(f"입력 파일이 없습니다: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise DataValidationError(f"입력 파일이 올바른 JSON이 아닙니다: {path} ({err})") from err


def _load_model_or_exit[ModelT: BaseModel](path: Path, model_cls: type[ModelT]) -> ModelT:
    """JSON 파일을 읽어 pydantic 모델로 검증한다 — 실패 시 exit 1 + 필드 경로 요약(§5.3)."""
    try:
        raw = _load_json_file(path)
    except DataValidationError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err
    try:
        return model_cls.model_validate(raw)
    except PydanticValidationError as err:
        console.print(f"[red]{model_cls.__name__} 검증 실패 ({path}):[/red]")
        for error in err.errors():
            loc = ".".join(str(part) for part in error["loc"]) or "(root)"
            console.print(f"  - {loc}: {error['msg']}")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err


def _check_allowed_state(run_state: RunState, allowed: set[PipelineState], *, command: str) -> None:
    """§6.3 표의 허용 진입 상태 밖이면 ``ApprovalGateError``(호출부가 exit 4로 매핑)."""
    if run_state.current_state in allowed:
        return
    current_idx = FORWARD_ORDER.index(run_state.current_state)
    min_idx = min(FORWARD_ORDER.index(state) for state in allowed)
    hint = (
        "이전 단계를 먼저 완료하세요."
        if current_idx < min_idx
        else "이 단계로의 회귀는 허용되지 않습니다."
    )
    raise ApprovalGateError(
        f"'{command}'는 현재 상태({run_state.current_state.value})에서 실행할 수 없습니다. {hint}"
    )


def _supported_variables(selected: Sequence[str]) -> set[str]:
    """A5 Indicator Registry가 지원하는 변수만 골라낸다 (§5.4).

    ``validate_hypothesis``(core.hitl.validation)는 지원 변수 목록을
    파라미터로만 받으므로(H1은 A5와 통합하지 않음), 여기서 registry와 결합한다.
    """
    supported: set[str] = set()
    for name in selected:
        try:
            resolve_indicator(name)
        except StrategyValidationError:
            continue
        supported.add(name)
    return supported


def _load_strategy_name(store: RunStore) -> str | None:
    """strategy_spec.json의 ``strategy_name``을 읽는다 (§5.6). 부재·오형식은 DataValidationError."""
    path = store.run_dir / "strategy_spec.json"
    if not path.exists():
        raise DataValidationError(f"strategy_spec.json이 없습니다: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as err:
        raise DataValidationError(f"strategy_spec.json이 올바른 JSON이 아닙니다: {path}") from err
    name = data.get("strategy_name") if isinstance(data, dict) else None
    return str(name) if name is not None else None


def _print_status_footer(run_id: str, run_state: RunState) -> None:
    """공통 상태 표시 2줄 (docs/specs/CLI-integration.md §3 — T1·T2 동일 문자열 포맷)."""
    hint = _NEXT_STEP_HINTS[run_state.current_state]
    console.print(f"파이프라인 상태: {run_state.current_state.value}  (run: {run_id})")
    console.print(f"다음 단계: {hint}")


def _print_transitions_table(run_state: RunState) -> None:
    table = Table(title=f"전이 이력 — {run_state.run_id}")
    for column in ("from", "to", "actor", "at", "auto_approved", "note"):
        table.add_column(column)
    for transition in run_state.transitions:
        table.add_row(
            transition.from_state.value if transition.from_state else "-",
            transition.to_state.value,
            transition.actor,
            transition.at,
            str(transition.auto_approved),
            transition.note or "-",
        )
    console.print(table)


def _print_artifact_checklist(store: RunStore) -> None:
    """docs/OUTPUT_SCHEMA.md §0 산출물 체크리스트 — 미래 산출물도 '-'로 표시한다."""
    table = Table(title="산출물 체크리스트 (docs/OUTPUT_SCHEMA.md §0)")
    table.add_column("파일")
    table.add_column("존재")
    for filename in _ARTIFACT_CHECKLIST:
        exists = (store.run_dir / filename).exists()
        table.add_row(filename, "✓" if exists else "-")
    console.print(table)


# ---------------------------------------------------------------------------
# 상태 전이 헬퍼 — 명령별 회귀·재전진 규칙 (§5.3~§5.6, ALLOWED_REGRESSIONS 4종과 대응)
# ---------------------------------------------------------------------------


def _advance_analyst_view(run_state: RunState, *, actor: str) -> RunState:
    """AWAITING_ANALYST_VIEW→ANALYST_VIEW_APPROVED(전진 1회) 또는 재제출 시
    회귀 후 재전진(advance 2회) (§5.3)."""
    if run_state.current_state == PipelineState.ANALYST_VIEW_APPROVED:
        run_state = advance(
            run_state, PipelineState.AWAITING_ANALYST_VIEW, actor=actor, note="관점 재작성"
        )
    return advance(run_state, PipelineState.ANALYST_VIEW_APPROVED, actor=actor)


def _advance_hypothesis(run_state: RunState, target: PipelineState, *, actor: str) -> RunState:
    """가설 입력의 status(DRAFT/APPROVED)에 따라 필요한 전이를 수행한다 (§5.4).

    이미 ``target`` 상태면 전이 없이 그대로 반환한다(재제출로 인한 내용
    갱신만 반영한다).
    """
    current = run_state.current_state
    if current == target:
        return run_state
    if target == PipelineState.HYPOTHESIS_DRAFT:
        note = "가설 수정(회귀)" if current == PipelineState.HYPOTHESIS_APPROVED else None
        return advance(run_state, PipelineState.HYPOTHESIS_DRAFT, actor=actor, note=note)
    # target == HYPOTHESIS_APPROVED
    if current == PipelineState.ANALYST_VIEW_APPROVED:
        run_state = advance(run_state, PipelineState.HYPOTHESIS_DRAFT, actor=actor)
    return advance(run_state, PipelineState.HYPOTHESIS_APPROVED, actor=actor)


def _advance_strategy_review(run_state: RunState, *, actor: str) -> RunState:
    """AWAITING_STRATEGY_REVIEW→STRATEGY_APPROVED(전진) 또는 재승인 시
    회귀 후 재전진 (§5.5)."""
    if run_state.current_state == PipelineState.STRATEGY_APPROVED:
        run_state = advance(
            run_state, PipelineState.AWAITING_STRATEGY_REVIEW, actor=actor, note="전략 재검토"
        )
    return advance(run_state, PipelineState.STRATEGY_APPROVED, actor=actor)


def _advance_interpretation(run_state: RunState, *, actor: str) -> RunState:
    """AWAITING_INTERPRETATION→COMPLETE(전진) 또는 재제출 시 회귀 후 재전진 (§5.6)."""
    if run_state.current_state == PipelineState.COMPLETE:
        run_state = advance(
            run_state, PipelineState.AWAITING_INTERPRETATION, actor=actor, note="해석 재제출"
        )
    return advance(run_state, PipelineState.COMPLETE, actor=actor)


def _apply_hypothesis_decision(
    hypothesis: HumanInvestmentHypothesis, interpretation: BacktestInterpretation
) -> HumanInvestmentHypothesis:
    """가설 판정(1804 §10)을 status에 반영한 사본을 만든다 (§5.6).

    SUPPORTED/PARTIALLY_SUPPORTED/REJECTED/REVISED는 동명 status로,
    INCONCLUSIVE는 TESTED로 매핑한다. approved_by·approved_at은 유지한다.
    """
    new_status = _DECISION_TO_STATUS[interpretation.hypothesis_decision]
    payload = hypothesis.model_dump(mode="json")
    payload.update(status=new_status.value, updated_at=now_kst_iso())
    return HumanInvestmentHypothesis.model_validate(payload)


# ---------------------------------------------------------------------------
# 명령 — §5.1 create-run
# ---------------------------------------------------------------------------


def create_run(
    company: Annotated[str, typer.Option("--company", help="기업명 또는 6자리 종목코드")],
    as_of_date: Annotated[str, typer.Option("--as-of-date", help="분석 기준일 YYYY-MM-DD")],
) -> None:
    """실행(run)을 새로 등록한다 (§5.1).

    1804 §14의 8개 명령은 모두 ``--run-id``를 전제하므로, run_id를 발급하고
    필요한 데이터가 이미 수집되어 있는지 확인하는 진입점이 별도로
    필요하다 — docs/HUMAN_IN_THE_LOOP.md §2 "기업명·분석 기준일 입력" 단계의
    코드화다. 이 명령은 데이터 수집 자체를 트리거하지 않고 검사만 한다.
    """
    as_of = _parse_iso_date_option(as_of_date, "--as-of-date")
    if as_of is None:
        raise typer.BadParameter("--as-of-date는 필수입니다.")

    settings = get_settings()
    _create_run_impl(company, as_of, settings)


def _create_run_impl(company: str, as_of: date, settings: Settings) -> str:
    """run 생성 로직 — ``create-run``과 ``research``가 공유한다 (명세 W3b §2.2·§2.3).

    기업 식별 → 상장 확인 → 데이터 준비 검사 → RunManifest·RunState 저장까지
    수행하고 발급된 ``run_id``를 반환한다. 표준 출력(run 생성 완료·매니페스트
    경로·상태 2줄)도 이 함수가 낸다 — ``create_run``의 관측 가능한 출력·동작은
    추출 전과 동일하다.
    """
    corp = _resolve_corp(company, settings)

    if corp.stock_code is None:
        console.print(
            f"[red]'{corp.corp_name}'은(는) 비상장 법인입니다 — run을 생성할 수 없습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    _ensure_data_ready(corp, corp.stock_code, settings)

    now = datetime.now(KST)
    run_id = generate_run_id(corp.corp_eng_name or corp.corp_name, now)
    manifest = RunManifest(
        run_id=run_id,
        company_query=company,
        corp_code=corp.corp_code,
        corp_name=corp.corp_name,
        corp_eng_name=corp.corp_eng_name,
        stock_code=corp.stock_code,
        as_of_date=as_of.isoformat(),
        created_at=now_kst_iso(),
        code_version=_git_short_hash(),
    )
    store = RunStore(settings.outputs_dir, run_id)
    manifest_path = store.save_run_manifest(manifest)
    run_state = create_run_state(run_id, corp.corp_name, as_of.isoformat(), actor="user")
    store.save_run_state(run_state)

    console.print(f"[green]run 생성 완료: {run_id}[/green]")
    console.print(f"매니페스트 경로: {manifest_path}")
    _print_status_footer(run_id, run_state)
    return run_id


# ---------------------------------------------------------------------------
# 명령 — §5.2 runs · status
# ---------------------------------------------------------------------------


def list_runs() -> None:
    """등록된 모든 실행을 스캔해 요약 테이블로 보여준다 (§5.2).

    ``outputs_dir`` 하위 디렉토리 중 run_state.json이 있는 것만 대상으로
    하며, 없는 디렉토리는 건너뛰고 개수만 마지막에 알린다. run-id 기반
    명령이 아니므로 공통 상태 표시 footer(§3)는 출력하지 않는다.
    """
    settings = get_settings()
    outputs_dir = settings.outputs_dir

    table = Table(title="등록된 실행 (outputs/)")
    for column in ("run_id", "company", "as_of_date", "상태", "마지막 전이 시각"):
        table.add_column(column)

    ignored = 0
    if outputs_dir.exists():
        for run_dir in sorted(p for p in outputs_dir.iterdir() if p.is_dir()):
            if not (run_dir / "run_state.json").exists():
                ignored += 1
                continue
            try:
                run_state = RunStore(outputs_dir, run_dir.name).load_run_state()
            except (DataValidationError, PydanticValidationError):
                ignored += 1
                continue
            last_at = run_state.transitions[-1].at if run_state.transitions else "-"
            table.add_row(
                run_state.run_id,
                run_state.company,
                run_state.as_of_date,
                run_state.current_state.value,
                last_at,
            )
    else:
        console.print(f"[yellow]outputs 디렉토리가 없습니다: {outputs_dir}[/yellow]")
        console.print("create-run으로 첫 실행을 등록하세요.")

    console.print(table)
    if ignored:
        console.print(f"run_state 없는 디렉토리 {ignored}개 무시")


def show_status(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
) -> None:
    """실행의 현재 상태·전이 이력·산출물 체크리스트를 보여준다 (§5.2)."""
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    _print_transitions_table(run_state)
    _print_artifact_checklist(store)
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# 명령 — §5.3 create-analyst-view
# ---------------------------------------------------------------------------


def create_analyst_view(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
    input_path: Annotated[Path, typer.Option("--input", help="AnalystView JSON 경로")],
) -> None:
    """사용자가 작성한 분석 관점(AnalystView)을 등록한다 (§5.3)."""
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        _check_allowed_state(
            run_state,
            {PipelineState.AWAITING_ANALYST_VIEW, PipelineState.ANALYST_VIEW_APPROVED},
            command="create-analyst-view",
        )

    view = _load_model_or_exit(input_path, AnalystView)
    evidence_store = _load_evidence_store(store)

    with _validation_guard():
        validate_analyst_view(view, evidence_store)

    store.save_analyst_view(view)
    run_state = _advance_analyst_view(run_state, actor="user")
    store.save_run_state(run_state)

    console.print(f"[green]analyst_view 저장 완료: {store.run_dir / 'analyst_view.json'}[/green]")
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# 명령 — §5.4 create-hypothesis
# ---------------------------------------------------------------------------


def create_hypothesis(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
    input_path: Annotated[
        Path, typer.Option("--input", help="HumanInvestmentHypothesis JSON 경로")
    ],
) -> None:
    """사용자가 작성한 투자 가설을 등록한다 (§5.4).

    승인 여부는 CLI 플래그가 아니라 입력 JSON의 ``status`` 필드가 정본이다 —
    ``--approve`` 같은 플래그는 두지 않는다(1804 §14, docs/AI_ROLE_BOUNDARY.md §3).
    """
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        _check_allowed_state(
            run_state,
            {
                PipelineState.ANALYST_VIEW_APPROVED,
                PipelineState.HYPOTHESIS_DRAFT,
                PipelineState.HYPOTHESIS_APPROVED,
            },
            command="create-hypothesis",
        )

    hypothesis = _load_model_or_exit(input_path, HumanInvestmentHypothesis)

    with _validation_guard():
        analyst_view = store.load_analyst_view()
    if hypothesis.view_id != analyst_view.view_id:
        console.print(
            f"[red]가설의 view_id({hypothesis.view_id})가 저장된 analyst_view.view_id"
            f"({analyst_view.view_id})와 일치하지 않습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    supported = _supported_variables(hypothesis.selected_variables)
    evidence_store = _load_evidence_store(store)
    with _validation_guard():
        validate_hypothesis(hypothesis, evidence_store, supported)

    if hypothesis.status not in (HypothesisStatus.DRAFT, HypothesisStatus.APPROVED):
        console.print(
            f"[red]작성 단계에서는 DRAFT/APPROVED만 허용합니다: {hypothesis.status.value}[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    store.save_human_hypothesis(hypothesis)
    target = (
        PipelineState.HYPOTHESIS_DRAFT
        if hypothesis.status == HypothesisStatus.DRAFT
        else PipelineState.HYPOTHESIS_APPROVED
    )
    run_state = _advance_hypothesis(run_state, target, actor="user")
    store.save_run_state(run_state)

    console.print(
        "[green]human_investment_hypothesis 저장 완료: "
        f"{store.run_dir / 'human_investment_hypothesis.json'}[/green]"
    )
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# 명령 — §5.5 approve-strategy
# ---------------------------------------------------------------------------


def approve_strategy(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
    review_path: Annotated[Path, typer.Option("--review", help="StrategyReview JSON 경로")],
) -> None:
    """사용자가 전략 초안을 검토·수정·승인한 기록(StrategyReview)을 등록한다 (§5.5)."""
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        _check_allowed_state(
            run_state,
            {PipelineState.AWAITING_STRATEGY_REVIEW, PipelineState.STRATEGY_APPROVED},
            command="approve-strategy",
        )

    review = _load_model_or_exit(review_path, StrategyReview)

    with _validation_guard():
        hypothesis = store.load_human_hypothesis()
    with _gate_guard():
        ensure_hypothesis_approved(hypothesis)
    if review.hypothesis_id != hypothesis.hypothesis_id:
        console.print(
            f"[red]리뷰의 hypothesis_id({review.hypothesis_id})가 승인된 가설"
            f"({hypothesis.hypothesis_id})과 일치하지 않습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    with _validation_guard():
        draft = store.load_strategy_draft()
    if draft != review.llm_draft_strategy:
        console.print(
            "[red]review.llm_draft_strategy가 저장된 strategy_draft.json과 다릅니다"
            " — AI 초안이 위변조되었을 수 있습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    expected = {
        modification.field_path
        for modification in diff_strategies(
            review.llm_draft_strategy, review.final_strategy, modified_by=review.approved_by
        )
    }
    actual = {modification.field_path for modification in review.modifications}
    if expected != actual:
        console.print("[red]modifications가 draft/final 차이와 일치하지 않습니다.[/red]")
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        if missing:
            console.print(f"  누락된 field_path: {missing}")
        if extra:
            console.print(f"  불필요한 field_path: {extra}")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    with _validation_guard():
        spec = parse_strategy_spec(review.final_strategy)
        compile_strategy(spec)

    store.save_strategy_review(review)
    strategy_spec_path = store.run_dir / "strategy_spec.json"
    strategy_spec_path.write_text(
        json.dumps(review.final_strategy, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    run_state = _advance_strategy_review(run_state, actor="user")
    store.save_run_state(run_state)

    console.print(
        f"[green]strategy_review 저장 완료: {store.run_dir / 'strategy_review.json'}[/green]"
    )
    console.print(f"strategy_spec 저장 완료: {strategy_spec_path}")
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# 명령 — §5.6 submit-interpretation
# ---------------------------------------------------------------------------


def submit_interpretation(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
    input_path: Annotated[Path, typer.Option("--input", help="BacktestInterpretation JSON 경로")],
) -> None:
    """사용자가 백테스트 결과를 해석하고 가설을 채택·수정·기각한다 (§5.6)."""
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        _check_allowed_state(
            run_state,
            {PipelineState.AWAITING_INTERPRETATION, PipelineState.COMPLETE},
            command="submit-interpretation",
        )

    interpretation = _load_model_or_exit(input_path, BacktestInterpretation)

    with _validation_guard():
        hypothesis = store.load_human_hypothesis()
    if interpretation.hypothesis_id != hypothesis.hypothesis_id:
        console.print(
            f"[red]interpretation.hypothesis_id({interpretation.hypothesis_id})가 저장된 가설"
            f"({hypothesis.hypothesis_id})과 일치하지 않습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    with _validation_guard():
        strategy_name = _load_strategy_name(store)
    if interpretation.strategy_id != strategy_name:
        console.print(
            f"[red]interpretation.strategy_id({interpretation.strategy_id})가 strategy_spec.json"
            f"의 strategy_name({strategy_name})과 일치하지 않습니다.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    updated_hypothesis = _apply_hypothesis_decision(hypothesis, interpretation)
    evidence_store = _load_evidence_store(store)
    with _validation_guard():
        validate_hypothesis(
            updated_hypothesis,
            evidence_store,
            _supported_variables(updated_hypothesis.selected_variables),
        )

    store.save_backtest_interpretation(interpretation)
    store.save_human_hypothesis(updated_hypothesis)

    run_state = _advance_interpretation(run_state, actor="user")
    store.save_run_state(run_state)

    console.print(
        "[green]backtest_interpretation 저장 완료: "
        f"{store.run_dir / 'backtest_interpretation.json'}[/green]"
    )
    console.print("보고서 생성은 generate-report — C3' 예정")
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# 명령 — §5.7 상태 인지형 스텁 3종
# ---------------------------------------------------------------------------


def generate_candidates(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
    lookback_years: Annotated[int, typer.Option("--lookback-years", help="Evidence 조회 연수")] = 5,
) -> None:
    """AI 분석 후보·가설 후보를 생성한다 (C1' 실구현, 명세 W3b §2.2).

    Evidence Store를 빌드해 저장한 뒤 LLM(Haiku·구독 OAuth)으로 CandidateAnalysis와
    HypothesisCandidate 목록을 생성한다. 상태 정책: DATA_READY=정상 경로(끝에
    CANDIDATE_ANALYSIS_READY→AWAITING_ANALYST_VIEW로 전진), 후보가 이미 있는
    상태(CANDIDATE_ANALYSIS_READY·AWAITING_ANALYST_VIEW)=재생성(전이 없음),
    ANALYST_VIEW_APPROVED 이상=거부(exit 4, 새 run 안내). 종료 코드: 3 설정·인증
    오류, 1 검증·데이터 오류(재시도 소진 포함), 4 게이트 차단.
    """
    settings = get_settings()
    run_generate_candidates(settings, run_id, lookback_years=lookback_years)


def run_generate_candidates(settings: Settings, run_id: str, *, lookback_years: int) -> None:
    """generate-candidates 코어 — ``research`` CLI도 이 함수를 재사용한다 (명세 W3b §2.2·§2.3).

    상태 정책·Evidence 빌드·LLM 후보 생성·AIUsageRecord 2건 기록·상태 전이·출력을
    모두 담당한다. 예외는 종료 코드로 통일한다(ConfigError→3, FileNotFoundError·
    DataValidationError→1, ApprovalGateError→4).
    """
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _validation_guard():
        manifest = store.load_run_manifest()

    with _gate_guard():
        _ensure_candidates_stage(run_state)

    regenerate = run_state.current_state in {
        PipelineState.CANDIDATE_ANALYSIS_READY,
        PipelineState.AWAITING_ANALYST_VIEW,
    }
    if regenerate:
        console.print("[yellow]이미 생성된 후보가 있어 재생성합니다 — 상태 전이 없음.[/yellow]")

    as_of = date.fromisoformat(manifest.as_of_date)

    try:
        package = build_financial_evidence(
            manifest.corp_code,
            as_of=as_of,
            data_dir=settings.data_dir,
            lookback_years=lookback_years,
        )
        _, evidence_manifest_path = EvidencePackageStore(store.run_dir).save(package)
        config = load_llm_config()
        client = create_llm_client(config, settings)
        analysis, analysis_meta = generate_candidate_analysis(
            package, client=client, prompts_dir=PROMPTS_DIR, max_attempts=config.max_attempts
        )
        candidates, candidates_meta = generate_hypothesis_candidates(
            package,
            analysis,
            client=client,
            prompts_dir=PROMPTS_DIR,
            max_attempts=config.max_attempts,
        )
    except ConfigError as err:
        console.print(f"[red]LLM 설정·인증 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err
    except FileNotFoundError as err:
        console.print(f"[red]{err}[/red]")
        console.print("먼저 `r2b build-financials`로 재무 데이터셋을 생성하세요.")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err
    except DataValidationError as err:
        console.print(f"[red]{err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err

    store.save_candidate_analysis(analysis)
    store.save_hypothesis_candidates(candidates)
    store.append_ai_usage(
        _usage_record(
            "candidate_analysis",
            metadata=analysis_meta,
            prompt_name=CANDIDATE_ANALYSIS_PROMPT_NAME,
            ai_role="후보 정리",
            input_ids=["evidence_package.json"],
            output_ids=["candidate_analysis.json"],
        )
    )
    store.append_ai_usage(
        _usage_record(
            "hypothesis_candidate",
            metadata=candidates_meta,
            prompt_name=HYPOTHESIS_CANDIDATE_PROMPT_NAME,
            ai_role="가설 후보 제시",
            input_ids=["candidate_analysis.json", "evidence_package.json"],
            output_ids=["hypothesis_candidates.json"],
        )
    )

    if not regenerate:
        note = f"generate-candidates: model={analysis_meta.model}, 가설 후보 {len(candidates)}건"
        run_state = advance(
            run_state, PipelineState.CANDIDATE_ANALYSIS_READY, actor="system", note=note
        )
        run_state = advance(
            run_state, PipelineState.AWAITING_ANALYST_VIEW, actor="system", note="후보 검토 대기"
        )
        store.save_run_state(run_state)

    _print_candidates_summary(
        package,
        analysis,
        candidates,
        evidence_manifest_path=evidence_manifest_path,
        analysis_meta=analysis_meta,
        candidates_meta=candidates_meta,
    )
    _print_status_footer(run_id, run_state)


def _ensure_candidates_stage(run_state: RunState) -> None:
    """후보 재생성이 이후 산출물을 무효화하지 않도록 진입 상태를 검사한다 (명세 W3b §2.2).

    ANALYST_VIEW_APPROVED 이상(분석 관점 승인 후)에서는 후보를 재생성하면 이미
    작성된 관점·가설이 이전 후보에 기반하므로 어긋난다 — 거부하고 새 run을 안내한다.
    """
    if FORWARD_ORDER.index(run_state.current_state) >= FORWARD_ORDER.index(
        PipelineState.ANALYST_VIEW_APPROVED
    ):
        raise ApprovalGateError(
            f"이미 분석 관점 이후 단계({run_state.current_state.value})로 진행된 실행이라 "
            "후보를 재생성할 수 없습니다 — 이후 산출물이 이전 후보와 어긋납니다. 새 run을 "
            "만들어 다시 시작하세요(create-run 또는 research)."
        )


def _usage_record(
    stage: str,
    *,
    metadata: LlmCallMetadata,
    prompt_name: str,
    ai_role: str,
    input_ids: list[str],
    output_ids: list[str],
) -> AIUsageRecord:
    """LLM 호출 1건의 AIUsageRecord를 만든다 (과제 2 증빙, 명세 W3b §0).

    ``generated_by``/저작 필드는 코드가 주입한다 — ``model``은 실제 호출
    메타데이터에서, ``prompt_version``은 "v1", ``human_review_required``는 True.
    """
    now = datetime.now(KST)
    return AIUsageRecord(
        usage_id=f"usage-{stage}-{now:%Y%m%d%H%M%S}",
        stage=stage,
        model=metadata.model,
        prompt_name=prompt_name,
        prompt_version="v1",
        input_artifact_ids=input_ids,
        output_artifact_ids=output_ids,
        ai_role=ai_role,
        human_review_required=True,
        human_changes_summary=None,
        created_at=now_kst_iso(),
    )


def _print_candidates_summary(
    package: EvidencePackage,
    analysis: CandidateAnalysis,
    candidates: list[HypothesisCandidate],
    *,
    evidence_manifest_path: Path,
    analysis_meta: LlmCallMetadata,
    candidates_meta: LlmCallMetadata,
) -> None:
    """Evidence 요약·findings 카테고리별 건수·후보 제목·LLM 메타 테이블 출력 (명세 W3b §2.2)."""
    console.print(
        f"[green]Evidence {len(package.evidence)}건 저장 완료[/green] "
        f"(매니페스트: {evidence_manifest_path})"
    )

    findings_table = Table(title="분석 후보 요약 (CandidateAnalysis)")
    findings_table.add_column("항목")
    findings_table.add_column("건수", justify="right")
    for label, count in (
        ("financial_findings", len(analysis.financial_findings)),
        ("business_findings", len(analysis.business_findings)),
        ("industry_findings", len(analysis.industry_findings)),
        ("catalyst_candidates", len(analysis.catalyst_candidates)),
        ("risk_candidates", len(analysis.risk_candidates)),
        ("relationship_candidates", len(analysis.relationship_candidates)),
        ("conflicting_evidence", len(analysis.conflicting_evidence)),
        ("missing_information", len(analysis.missing_information)),
    ):
        findings_table.add_row(label, str(count))
    console.print(findings_table)

    console.print(f"가설 후보 {len(candidates)}건:")
    for candidate in candidates:
        console.print(f"  - {candidate.title}")

    meta_table = Table(title="LLM 호출 메타 (모델·시도수·토큰)")
    for column in ("stage", "model", "시도수", "input_tokens", "output_tokens"):
        meta_table.add_column(column)
    for stage, meta in (
        ("candidate_analysis", analysis_meta),
        ("hypothesis_candidate", candidates_meta),
    ):
        meta_table.add_row(
            stage,
            meta.model,
            str(meta.num_attempts),
            "-" if meta.input_tokens is None else str(meta.input_tokens),
            "-" if meta.output_tokens is None else str(meta.output_tokens),
        )
    console.print(meta_table)


def generate_strategy_draft(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
) -> None:
    """승인된 투자 가설을 전략 DSL 초안으로 변환한다 (명세 W3b-candidates-strategy.md §3.2).

    기존 게이트 2종(``ensure_state_at_least(HYPOTHESIS_APPROVED)``·
    ``ensure_hypothesis_approved``)을 그대로 유지하고, 그 위에 "이미 승인된
    전략은 초안 재생성으로 무효화하지 않는다"는 상태 정책을 추가한다:
    ``HYPOTHESIS_APPROVED``는 정상 경로(초안 생성 후 2단계 전진 —
    ``STRATEGY_DRAFT_READY``→``AWAITING_STRATEGY_REVIEW``), ``STRATEGY_DRAFT_READY``·
    ``AWAITING_STRATEGY_REVIEW``는 재생성(초안을 다시 만들되 상태 전이 없음),
    ``STRATEGY_APPROVED`` 이상은 거부한다(회귀는 ``approve-strategy`` 재승인
    경로로만 가능하다 — 원문 §13).
    """
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        ensure_state_at_least(run_state, PipelineState.HYPOTHESIS_APPROVED)

    with _validation_guard():
        hypothesis = store.load_human_hypothesis()
    with _gate_guard():
        ensure_hypothesis_approved(hypothesis)
    with _gate_guard():
        _check_allowed_state(
            run_state,
            {
                PipelineState.HYPOTHESIS_APPROVED,
                PipelineState.STRATEGY_DRAFT_READY,
                PipelineState.AWAITING_STRATEGY_REVIEW,
            },
            command="generate-strategy-draft",
        )

    with _validation_guard():
        manifest = store.load_run_manifest()

    try:
        llm_config = load_llm_config()
        client = create_llm_client(llm_config, settings)
    except ConfigError as err:
        console.print(f"[red]설정 오류: {err}[/red]")
        raise typer.Exit(code=CONFIG_ERROR_EXIT_CODE) from err

    with _validation_guard():
        draft, metadata = draft_strategy(
            hypothesis,
            stock_code=manifest.stock_code,
            client=client,
            prompts_dir=DEFAULT_PROMPTS_DIR,
            max_attempts=llm_config.max_attempts,
        )

    store.save_strategy_draft(draft)
    store.append_ai_usage(
        AIUsageRecord(
            usage_id=f"usage-strategy_translation-{datetime.now(KST).strftime('%Y%m%d%H%M%S')}",
            stage="strategy_translation",
            model=metadata.model,
            prompt_name="strategy_translation",
            prompt_version="v1",
            input_artifact_ids=["human_investment_hypothesis.json"],
            output_artifact_ids=["strategy_draft.json"],
            ai_role="전략 초안 변환",
            human_review_required=True,
            created_at=now_kst_iso(),
        )
    )

    if run_state.current_state == PipelineState.HYPOTHESIS_APPROVED:
        run_state = advance(run_state, PipelineState.STRATEGY_DRAFT_READY, actor="system")
        run_state = advance(run_state, PipelineState.AWAITING_STRATEGY_REVIEW, actor="system")
        store.save_run_state(run_state)
    else:
        console.print("[yellow]재생성 — 상태 전이 없음[/yellow]")

    console.print(
        f"[green]strategy_draft 저장 완료: {store.run_dir / 'strategy_draft.json'}[/green]"
    )
    console.print(json.dumps(draft, ensure_ascii=False, indent=2))

    table = Table(title="LLM 메타")
    for column in (
        "model",
        "num_attempts",
        "duration_ms",
        "input_tokens",
        "output_tokens",
        "cost_usd_notional",
    ):
        table.add_column(column)
    table.add_row(
        metadata.model,
        str(metadata.num_attempts),
        str(metadata.duration_ms),
        str(metadata.input_tokens) if metadata.input_tokens is not None else "-",
        str(metadata.output_tokens) if metadata.output_tokens is not None else "-",
        f"{metadata.cost_usd_notional:.6f}" if metadata.cost_usd_notional is not None else "-",
    )
    console.print(table)

    console.print("검토·수정 후 approve-strategy --review로 승인하세요.")
    _print_status_footer(run_id, run_state)


def generate_report(
    run_id: Annotated[str, typer.Option("--run-id", help="실행 ID")],
) -> None:
    """15개 섹션 보고서 + 강건성 분석을 생성한다 (명세 W3c §2.3, HITL §6, README §24.2·§24.3).

    상태 ``COMPLETE``\\ 가 전제다(미달 시 exit 4). ``backtest_result.json``\\ 의 종목·기간·
    비용을 재사용해 승인 백테스트와 **동일 창**으로 강건성 분석(조건 제거·비용·하위 기간)을
    실행·저장하고, LLM 결과 설명 초안(실패해도 보고서는 계속 생성 — 게이트 아님)을 덧붙여
    15-섹션 보고서를 만든다. 상태 전이는 없다(COMPLETE 유지, 재실행 시 덮어씀).
    """
    settings = get_settings()
    store = RunStore(settings.outputs_dir, run_id)
    run_state = _load_run_state_or_exit(store)

    with _gate_guard():
        ensure_state_at_least(run_state, PipelineState.COMPLETE)

    with _validation_guard():
        manifest = store.load_run_manifest()
        review = store.load_strategy_review()
        hypothesis = store.load_human_hypothesis()

    bt_path = store.run_dir / "backtest_result.json"
    if not bt_path.exists():
        console.print(
            f"[red]backtest_result.json이 없습니다 ({bt_path}). 먼저 backtest를 실행하세요.[/red]"
        )
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)
    try:
        backtest_result = BacktestResult.model_validate_json(bt_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as err:
        console.print(f"[red]backtest_result.json을 읽을 수 없습니다 ({bt_path}): {err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err

    # 강건성 분석 — 승인 백테스트와 동일 창(기간)·동일 비용으로 재현(명세 W3c §2.1·§2.3).
    base_config = BacktestConfig(
        commission_rate=backtest_result.commission_rate,
        sell_tax_rate=backtest_result.sell_tax_rate,
        slippage_rate=backtest_result.slippage_rate,
        initial_cash=backtest_result.initial_cash,
        benchmark=backtest_result.benchmark.name,
    )
    try:
        robustness = run_robustness(
            review,
            data_dir=settings.data_dir,
            stock_code=manifest.stock_code,
            corp_code=manifest.corp_code,
            start_date=backtest_result.start_date,
            end_date=backtest_result.end_date,
            base_config=base_config,
        )
    except (FileNotFoundError, DataValidationError) as err:
        console.print(f"[red]강건성 분석 실패: {err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err
    except AssertionError as err:
        console.print(f"[red]강건성 재현 검증 실패(연구용 경로 불일치): {err}[/red]")
        raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE) from err

    # 자기 검증: 비용 1배 결과(원 전략·승인 config)가 승인 백테스트와 일치해야 한다(명세 W3c §2.1).
    cost_1x = next((c for c in robustness.cost_sensitivity if c.multiplier == 1.0), None)
    if cost_1x is not None:
        cum_ok = (cost_1x.cumulative_return is None) == (
            backtest_result.cumulative_return is None
        ) and (
            cost_1x.cumulative_return is None
            or backtest_result.cumulative_return is None
            or abs(cost_1x.cumulative_return - backtest_result.cumulative_return) <= 1e-9
        )
        if cost_1x.num_trades != backtest_result.num_trades or not cum_ok:
            console.print(
                "[red]강건성 재현 검증 실패 — 연구용 경로 결과가 승인 백테스트와 다릅니다"
                f"(거래 {cost_1x.num_trades} vs {backtest_result.num_trades}).[/red]"
            )
            raise typer.Exit(code=VALIDATION_ERROR_EXIT_CODE)

    robustness_path = store.run_dir / "robustness_report.json"
    robustness_path.write_text(robustness.model_dump_json(indent=2) + "\n", encoding="utf-8")

    # LLM 결과 설명 초안 — 부가 기능(게이트 아님): 실패해도 보고서는 계속 생성한다.
    ai_explanation: str | None = None
    ai_explanation_origin = "AI_DRAFT_HUMAN_APPROVED"
    try:
        llm_config = load_llm_config()
        client = create_llm_client(llm_config, settings)
        ai_explanation, explanation_meta = draft_result_explanation(
            client=client,
            prompts_dir=DEFAULT_PROMPTS_DIR,
            result=backtest_result,
            hypothesis=hypothesis,
            robustness=robustness,
        )
        store.append_ai_usage(
            AIUsageRecord(
                usage_id=f"usage-result_explanation-{datetime.now(KST):%Y%m%d%H%M%S}",
                stage="result_explanation",
                model=explanation_meta.model,
                prompt_name="result_explanation",
                prompt_version="v1",
                input_artifact_ids=[
                    "backtest_result.json",
                    "human_investment_hypothesis.json",
                    "robustness_report.json",
                ],
                output_artifact_ids=["research_report.md"],
                ai_role="결과 설명 초안",
                human_review_required=True,
                created_at=now_kst_iso(),
            )
        )
    except (ConfigError, DataValidationError) as err:
        console.print(
            f"[yellow]AI 설명 초안 생성 실패 — 사용자 해석만 수록합니다(부가 기능): {err}[/yellow]"
        )
        ai_explanation = None

    with _validation_guard():
        report_md = build_research_report(
            store,
            robustness=robustness,
            ai_explanation=ai_explanation,
            ai_explanation_origin=ai_explanation_origin,
        )
    report_path = store.run_dir / "research_report.md"
    report_path.write_text(report_md, encoding="utf-8")

    title = report_md.splitlines()[0].lstrip("# ").strip()
    console.print(f"[green]research_report.md 저장 완료: {report_path}[/green]")
    console.print(f"강건성 리포트 저장 완료: {robustness_path}")
    console.print(f"보고서 제목: {title}")
    console.print(
        f"섹션 수: 15 · 조건 제거 변형 수: {len(robustness.condition_ablation)} · "
        f"비용 배율 {len(robustness.cost_sensitivity)}종 · 하위 기간 {len(robustness.subperiod)}종"
    )
    if ai_explanation is None:
        console.print("[yellow]AI 설명 초안: 미수록(생성 실패) — 사용자 해석만 포함[/yellow]")
    console.print("보고서 생성 완료 — 파이프라인 COMPLETE 유지(재실행 시 덮어씀).")
    _print_status_footer(run_id, run_state)


# ---------------------------------------------------------------------------
# §5.8 등록
# ---------------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """HITL 워크플로 명령 10종을 등록한다 (§5.8).

    ``app/cli.py``로의 연결(``register(app)`` 호출)은 병합 시 메인 세션이
    수행한다 — 이 모듈은 스스로 루트 앱을 소유하거나 import하지 않는다.
    """
    app.command("create-run")(create_run)
    app.command("runs")(list_runs)
    app.command("status")(show_status)
    app.command("create-analyst-view")(create_analyst_view)
    app.command("create-hypothesis")(create_hypothesis)
    app.command("approve-strategy")(approve_strategy)
    app.command("submit-interpretation")(submit_interpretation)
    app.command("generate-candidates")(generate_candidates)
    app.command("generate-strategy-draft")(generate_strategy_draft)
    app.command("generate-report")(generate_report)
