"""generate-report 실호출 integration 테스트 (명세 W3c §2.4) — 인증 없으면 skip.

로컬 구성한 COMPLETE run 픽스처(합성 parquet + 실제 백테스트 산출물)로 ``r2b
generate-report``\\ 를 실행해 LLM 결과 설명 초안을 **실호출 1회** 하고, 15-섹션
research_report.md·robustness_report.json이 생성되는지 확인한다. 구독 계정 rate limit을
고려해 실 호출은 1회로 최소화한다(다른 live 테스트와 동일 관례, 예산 2회 이내).

실행: 레포 루트에서 메인 레포 .env를 주입하고 ``pytest tests/integration/test_report_live.py``.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

from research_backtest.app.commands import hitl_flow
from research_backtest.core.config import Settings, get_settings
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
from research_backtest.quant.backtest.costs import BacktestConfig
from research_backtest.quant.backtest.runner import execute_approved_strategy
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    FinancialEvidence,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

STOCK = "000660"
CORP = "00164779"
INDEX = "1001"
STAMP = "2026-07-15T16:00:00+09:00"
CONFIG = BacktestConfig(
    commission_rate=0.00015, sell_tax_rate=0.0018, slippage_rate=0.001, initial_cash=10_000_000.0
)
_STRATEGY: dict[str, Any] = {
    "strategy_name": "LiveReportStrat",
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


@pytest.fixture
def live_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """실 LLM 인증(.env)은 유지하고 data_dir·outputs_dir만 tmp로 바꾼 Settings."""
    real = get_settings()
    if not real.anthropic_api_key and not real.claude_code_oauth_token:
        pytest.skip("LLM 인증(CLAUDE_CODE_OAUTH_TOKEN 또는 ANTHROPIC_API_KEY) 미설정 — 생략")
    test_settings = real.model_copy(
        update={"data_dir": tmp_path / "data", "outputs_dir": tmp_path / "outputs"}
    )
    monkeypatch.setattr(hitl_flow, "get_settings", lambda: test_settings)
    return test_settings


def _build_app() -> typer.Typer:
    app = typer.Typer()
    hitl_flow.register(app)
    return app


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


def _finding() -> Finding:
    return Finding(
        finding_id="F1",
        category="재무",
        statement="영업이익 흑자 전환",
        evidence_ids=["FIN_A"],
        confidence=0.7,
        source_type="financial_statement",
        limitations=[],
    )


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
            financial_findings=[_finding()],
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
            strategy_id="LiveReportStrat",
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


def test_generate_report_live_llm_explanation(live_settings: Settings) -> None:
    """실 LLM 1회 호출 — 15-섹션 보고서·강건성 리포트 생성, result_explanation 기록."""
    store = _make_complete_run(live_settings, "RUN-LIVE-RPT")

    result = runner.invoke(_build_app(), ["generate-report", "--run-id", "RUN-LIVE-RPT"])
    assert result.exit_code == 0, result.output

    report_path = store.run_dir / "research_report.md"
    assert report_path.exists()
    assert (store.run_dir / "robustness_report.json").exists()

    md = report_path.read_text(encoding="utf-8")
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", md, re.M)]
    assert numbers == list(range(1, 16))
    assert md.startswith("# SK하이닉스: 실적과 수급이 겹칠 때만 돌파를 신뢰한다")

    # 실 LLM 설명 초안이 기록된다(model != "fake").
    usage = store.load_ai_usage_log()
    explanation = [r for r in usage if r.stage == "result_explanation"]
    assert len(explanation) == 1
    assert explanation[0].model != "fake"
    assert explanation[0].output_artifact_ids == ["research_report.md"]
