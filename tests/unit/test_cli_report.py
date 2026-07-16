"""generate-report CLI 단위 테스트 (명세 W3c §2.3·§2.4) — 오프라인(FakeLlm·합성 parquet).

상태 게이트(COMPLETE 미달 → exit 4), 정상 경로(강건성·보고서·result_explanation 기록),
LLM 실패 시 보고서 계속 생성(게이트 아님)을 검증한다. backtest_result.json은 합성
데이터에 ``execute_approved_strategy``\\ 를 실제로 돌려 만들어, generate-report의 자기
검증(비용 1배 == 승인 백테스트)이 통과하도록 한다. 실 API·실데이터·실 LLM은 쓰지 않는다.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import typer
from rich.console import Console
from typer.testing import CliRunner

from research_backtest.app.commands import hitl_flow
from research_backtest.core.config import Settings
from research_backtest.core.exceptions import ConfigError
from research_backtest.core.hitl.models import (
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    Finding,
    HumanInvestmentHypothesis,
    RunManifest,
    StrategyReview,
)
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    advance,
    create_run_state,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.llm.testing import FakeLlmClient
from research_backtest.quant.backtest.costs import BacktestConfig
from research_backtest.quant.backtest.runner import execute_approved_strategy
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    FinancialEvidence,
)

runner = CliRunner()

STOCK = "000660"
CORP = "00164779"
INDEX = "1001"
STAMP = "2026-07-15T16:00:00+09:00"
CONFIG = BacktestConfig(
    commission_rate=0.00015, sell_tax_rate=0.0018, slippage_rate=0.001, initial_cash=10_000_000.0
)

_STRATEGY: dict[str, Any] = {
    "strategy_name": "CliReportStrat",
    "version": "1.0",
    "universe": {"type": "single_asset", "tickers": [STOCK]},
    "entry": {
        "all": [
            {"left": "operating_income_yoy", "operator": ">", "right": 0.0},
            {"left": "foreign_net_buy_5d", "operator": ">", "right": 0.0},
            {"left": "close", "operator": ">", "right": "sma_5"},
        ]
    },
    "exit": {"any": [{"type": "max_holding_days", "value": 3}]},
    "execution": {"signal_time": "close", "trade_time": "next_open"},
}


def _build_app() -> typer.Typer:
    app = typer.Typer()
    hitl_flow.register(app)
    return app


@pytest.fixture(autouse=True)
def _wide_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hitl_flow, "console", Console(width=200))


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    active = Settings(
        _env_file=None,
        dart_api_key="unit-test-key",
        data_dir=tmp_path / "data",
        outputs_dir=tmp_path / "outputs",
    )
    monkeypatch.setattr(hitl_flow, "get_settings", lambda: active)
    return active


def _weekdays(start: date, n: int) -> list[date]:
    out: list[date] = []
    cursor = start
    while len(out) < n:
        if cursor.weekday() < 5:
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def _write_data(data_dir: Path, n_days: int = 40) -> list[date]:
    dates = _weekdays(date(2024, 1, 1), n_days)
    closes = [100 + (10 if i % 6 < 3 else -5) + i for i in range(n_days)]
    opens = [c - 1 for c in closes]

    stock_dir = data_dir / "normalized" / "market" / STOCK
    stock_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": [c + 2 for c in closes],
            "low": [o - 2 for o in opens],
            "close": closes,
            "volume": [1000] * n_days,
            "foreign_net_buy_value": [1000 * (1 if i % 2 == 0 else -1) for i in range(n_days)],
            "institution_net_buy_value": [0] * n_days,
        }
    ).to_parquet(stock_dir / "daily.parquet", engine="pyarrow", index=False)

    index_dir = data_dir / "normalized" / "market" / f"index_{INDEX}"
    index_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "date": dates,
            "open": [2000.0] * n_days,
            "high": [2010.0] * n_days,
            "low": [1990.0] * n_days,
            "close": [2000.0 + i for i in range(n_days)],
            "volume": [1] * n_days,
            "trading_value": [1] * n_days,
        }
    ).to_parquet(index_dir / "daily.parquet", engine="pyarrow", index=False)

    fin_dir = data_dir / "normalized" / "financials" / CORP
    fin_dir.mkdir(parents=True)
    pd.DataFrame(
        {
            "metric_id": ["operating_income_yoy"],
            "fs_scope": ["CFS"],
            "fiscal_year": [2024],
            "fiscal_quarter": [1],
            "period_end": [date(2024, 1, 5)],
            "value": [0.5],
            "rcept_no": ["r1"],
            "rcept_dt": [date(2024, 1, 3)],
            "available_from": [date(2024, 1, 4)],
            "inputs_derived": [False],
        }
    ).to_parquet(fin_dir / "financial_metrics.parquet", engine="pyarrow", index=False)
    return dates


def _evidence(evidence_id: str) -> FinancialEvidence:
    return FinancialEvidence(
        evidence_id=evidence_id,
        category="PROFITABILITY",
        statement=f"{evidence_id} 흑자 전환",
        current_value=None,
        comparison_value=None,
        change_rate=0.5,
        period="2024Q1",
        comparison_period="2023Q1",
        source_fact_ids=[f"FACT_{evidence_id}"],
        rcept_no="20250319000665",
        filing_date="2025-03-19",
        significance_score=0.9,
        fs_scope="CFS",
        available_from="2025-03-20",
    )


def _make_complete_run(settings_obj: Settings, run_id: str) -> RunStore:
    """합성 데이터 + COMPLETE run 산출물 전부(실 backtest_result.json 포함)를 만든다."""
    dates = _write_data(settings_obj.data_dir)
    store = RunStore(settings_obj.outputs_dir, run_id)
    store.run_dir.mkdir(parents=True, exist_ok=True)

    store.save_run_manifest(
        RunManifest(
            run_id=run_id,
            company_query=STOCK,
            corp_code=CORP,
            corp_name="SK하이닉스",
            corp_eng_name="SK hynix Inc.",
            stock_code=STOCK,
            as_of_date="2025-12-31",
            created_at=STAMP,
        )
    )
    review = StrategyReview(
        review_id="review-1",
        hypothesis_id="hyp-1",
        llm_draft_strategy=_STRATEGY,
        final_strategy=_STRATEGY,
        modifications=[],
        approval_reason="초안을 그대로 승인",
        approved_by="검증자",
        approved_at=STAMP,
    )
    # 실제 백테스트를 돌려 backtest_result.json(+ trade/daily)을 산출 — 자기 검증 통과용.
    execute_approved_strategy(
        review,
        data_dir=settings_obj.data_dir,
        stock_code=STOCK,
        corp_code=CORP,
        start_date=dates[0],
        end_date=dates[-1],
        out_dir=store.run_dir,
        backtest_config=CONFIG,
    )

    EvidencePackageStore(store.run_dir).save(
        EvidencePackage(
            corp_code=CORP,
            as_of_date="2025-12-31",
            lookback_years=5,
            fs_scope="CFS",
            generated_at=STAMP,
            evidence=[_evidence("FIN_A"), _evidence("FIN_B")],
        )
    )
    store.save_candidate_analysis(
        CandidateAnalysis(
            financial_findings=[
                Finding(
                    finding_id="F1",
                    category="재무",
                    statement="영업이익 흑자 전환",
                    evidence_ids=["FIN_A"],
                    confidence=0.7,
                    source_type="financial_statement",
                    limitations=[],
                )
            ],
            business_findings=[],
            industry_findings=[],
            catalyst_candidates=[],
            risk_candidates=[],
            relationship_candidates=[],
            conflicting_evidence=[],
            missing_information=[],
        )
    )
    store.save_analyst_view(
        AnalystView(
            view_id="view-1",
            author="검증자",
            research_question="흑자 전환 후 돌파가 지속되는가",
            core_thesis="실적과 수급이 겹칠 때만 돌파를 신뢰한다",
            selected_evidence_ids=["FIN_A", "FIN_B"],
            rejected_evidence_ids=[],
            evidence_selection_reason="가설과 직접 연결된다",
            rejected_evidence_reasons={},
            interpretation="모멘텀 구간에서 신호 질이 높다",
            expected_mechanism="실적 → 수급 → 돌파",
            counterarguments=["고점 되돌림 위험"],
            uncertainties=["사이클 판단"],
            created_at=STAMP,
            updated_at=STAMP,
        )
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis(
            hypothesis_id="hyp-1",
            view_id="view-1",
            author="검증자",
            thesis="실적·수급·돌파 동시 충족 시 초과수익",
            economic_rationale="확인된 돌파는 거짓 신호가 적다",
            expected_mechanism="실적 → 수급 → 추세",
            selected_variables=["operating_income_yoy"],
            expected_direction="positive",
            investment_horizon_days=60,
            evidence_ids=["FIN_A"],
            falsification_conditions=["승률 50% 미만이면 기각"],
            limitations=["단일 종목"],
            status="APPROVED",
            created_at=STAMP,
            updated_at=STAMP,
            approved_by="검증자",
            approved_at=STAMP,
        )
    )
    store.save_strategy_review(review)
    store.save_backtest_interpretation(
        BacktestInterpretation(
            interpretation_id="interp-1",
            hypothesis_id="hyp-1",
            strategy_id="CliReportStrat",
            author="검증자",
            main_findings="손익비 우수, 노출률 낮음",
            supporting_results=["Profit Factor 우위"],
            contradicting_results=["표본 적음"],
            limitations=["표본"],
            hypothesis_decision="PARTIALLY_SUPPORTED",
            decision_reason="방향성 지지, 보완 필요",
            followup_tests=["추가 검증"],
            created_at=STAMP,
        )
    )

    run_state = create_run_state(run_id, "SK하이닉스", "2025-12-31", actor="test-fixture")
    for target in FORWARD_ORDER[1 : FORWARD_ORDER.index(PipelineState.COMPLETE) + 1]:
        run_state = advance(run_state, target, actor="test-fixture")
    store.save_run_state(run_state)
    return store


def _make_partial_run(settings_obj: Settings, run_id: str, state: PipelineState) -> RunStore:
    store = RunStore(settings_obj.outputs_dir, run_id)
    run_state = create_run_state(run_id, "SK하이닉스", "2025-12-31", actor="test-fixture")
    for target in FORWARD_ORDER[1 : FORWARD_ORDER.index(state) + 1]:
        run_state = advance(run_state, target, actor="test-fixture")
    store.save_run_state(run_state)
    return store


# --- 상태 게이트 -------------------------------------------------------------


def test_generate_report_not_complete_exits_4(settings: Settings) -> None:
    _make_partial_run(settings, "RUN-GR-GATE", PipelineState.AWAITING_INTERPRETATION)
    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR-GATE"])
    assert result.exit_code == 4


def test_generate_report_missing_run_exits_1(settings: Settings) -> None:
    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "NO-SUCH-RUN"])
    assert result.exit_code == 1


# --- 정상 경로 (FakeLlm 성공) -------------------------------------------------


def test_generate_report_success_writes_report_and_robustness(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _make_complete_run(settings, "RUN-GR-OK")
    client = FakeLlmClient(["SK하이닉스 전략은 합성 데이터에서 관측된 결과를 낸다."])
    monkeypatch.setattr(hitl_flow, "create_llm_client", lambda config, settings: client)

    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR-OK"])
    assert result.exit_code == 0, result.output

    report_path = store.run_dir / "research_report.md"
    robustness_path = store.run_dir / "robustness_report.json"
    assert report_path.exists()
    assert robustness_path.exists()

    md = report_path.read_text(encoding="utf-8")
    assert md.startswith("# SK하이닉스: 실적과 수급이 겹칠 때만 돌파를 신뢰한다")
    assert "## 15. " in md
    assert "SK하이닉스 전략은 합성 데이터에서 관측된 결과를 낸다." in md  # AI 설명 초안 수록

    # result_explanation AIUsageRecord가 기록된다(과제 2 증빙, 명세 W3c §2.3).
    usage = store.load_ai_usage_log()
    assert any(r.stage == "result_explanation" for r in usage)
    expl = next(r for r in usage if r.stage == "result_explanation")
    assert expl.output_artifact_ids == ["research_report.md"]
    assert expl.model == "fake"

    assert "섹션 수: 15" in result.output
    assert "COMPLETE" in result.output  # 상태 표시 유지(전이 없음)

    # 재실행 덮어쓰기 허용(상태 전이 없음).
    client2 = FakeLlmClient(["재실행 설명 초안."])
    monkeypatch.setattr(hitl_flow, "create_llm_client", lambda config, settings: client2)
    rerun = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR-OK"])
    assert rerun.exit_code == 0, rerun.output
    assert store.load_run_state().current_state == PipelineState.COMPLETE


# --- LLM 실패 시 보고서 계속 생성 (게이트 아님) ------------------------------


def test_generate_report_llm_failure_still_writes_report(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _make_complete_run(settings, "RUN-GR-LLM")

    def _raise_config(config: Any, settings: Any) -> Any:
        raise ConfigError("LLM 인증 정보가 없습니다")

    monkeypatch.setattr(hitl_flow, "create_llm_client", _raise_config)

    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR-LLM"])
    assert result.exit_code == 0, result.output

    md = (store.run_dir / "research_report.md").read_text(encoding="utf-8")
    assert "AI 설명 초안 생성 실패" in md
    assert (store.run_dir / "robustness_report.json").exists()
    # LLM 실패 시 result_explanation 사용 기록은 남기지 않는다.
    assert not any(r.stage == "result_explanation" for r in store.load_ai_usage_log())


# --- 산출물 부재 -------------------------------------------------------------


def test_generate_report_missing_backtest_result_exits_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _make_complete_run(settings, "RUN-GR-NOBT")
    (store.run_dir / "backtest_result.json").unlink()
    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-GR-NOBT"])
    assert result.exit_code == 1
