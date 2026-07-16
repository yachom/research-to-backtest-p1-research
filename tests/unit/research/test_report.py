"""15-섹션 보고서 빌더(research.report.builder) 단위 테스트 (명세 W3c §2.2·§2.4) — 오프라인.

tmp outputs에 COMPLETE run 산출물 전부를 합성해 보고서를 만들고, 15섹션 존재·순서·
저작 태그·논지형 제목·무수정 승인 표기·결정성·강건성 표를 검증한다. LLM은 호출하지
않는다(ai_explanation을 인자로 주입).
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    Finding,
    HumanInvestmentHypothesis,
    RunManifest,
    StrategyModification,
    StrategyReview,
)
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    advance,
    create_run_state,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.quant.backtest.metrics import (
    BacktestResult,
    BenchmarkComparison,
    BuyHoldComparison,
)
from research_backtest.quant.backtest.robustness import (
    AblationResult,
    CostSensitivityResult,
    RobustnessReport,
    SubperiodResult,
)
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    FinancialEvidence,
)
from research_backtest.research.report.builder import build_research_report

STAMP = "2026-07-15T16:00:00+09:00"

_STRATEGY = {
    "strategy_name": "TestStrat",
    "version": "1.0",
    "universe": {"type": "single_asset", "tickers": ["000660"]},
    "entry": {"all": [{"left": "operating_income_yoy", "operator": ">", "right": 0.2}]},
    "exit": {"any": [{"type": "max_holding_days", "value": 60}]},
    "execution": {"signal_time": "close", "trade_time": "next_open"},
}


def _evidence(evidence_id: str) -> FinancialEvidence:
    return FinancialEvidence(
        evidence_id=evidence_id,
        category="PROFITABILITY",
        statement=f"{evidence_id} 흑자 전환",
        current_value=None,
        comparison_value=None,
        change_rate=0.5,
        period="2024Q4",
        comparison_period="2023Q4",
        source_fact_ids=[f"FACT_{evidence_id}"],
        rcept_no="20250319000665",
        filing_date="2025-03-19",
        significance_score=0.9,
        fs_scope="CFS",
        available_from="2025-03-20",
    )


def _finding(finding_id: str, category: str) -> Finding:
    return Finding(
        finding_id=finding_id,
        category=category,
        statement=f"{category} 관련 관측 {finding_id}",
        evidence_ids=["FIN_A"],
        confidence=0.7,
        source_type="financial_statement",
        limitations=[],
    )


def _backtest_result() -> BacktestResult:
    return BacktestResult(
        strategy_name="TestStrat",
        start_date=date(2016, 1, 1),
        end_date=date(2025, 12, 31),
        trading_days=2452,
        fs_scope="CFS",
        initial_cash=100_000_000.0,
        commission_rate=0.00015,
        sell_tax_rate=0.0018,
        slippage_rate=0.001,
        cumulative_return=1.1076,
        cagr=0.0796,
        annual_volatility=0.133,
        sharpe=0.642,
        sortino=0.247,
        max_drawdown=-0.1687,
        calmar=0.472,
        win_rate=0.6,
        avg_win=42_097_398.0,
        avg_loss=-7_764_012.0,
        payoff_ratio=5.42,
        profit_factor=8.13,
        num_trades=5,
        avg_holding_days=26.0,
        market_exposure=0.053,
        benchmark=BenchmarkComparison(
            name="KOSPI",
            cumulative_return=1.1963,
            excess_return=-0.0887,
            information_ratio=-0.056,
        ),
        buy_hold=BuyHoldComparison(cumulative_return=20.28, cagr=0.369, max_drawdown=-0.4949),
        has_trades=True,
    )


def _robustness() -> RobustnessReport:
    return RobustnessReport(
        strategy_name="TestStrat",
        start_date=date(2016, 1, 1),
        end_date=date(2025, 12, 31),
        condition_ablation=[
            AblationResult(
                variant="가격 모멘텀만",
                sources=["PRICE"],
                num_conditions=1,
                num_trades=31,
                cumulative_return=2.61,
                max_drawdown=-0.36,
                win_rate=0.45,
                profit_factor=3.2,
            ),
            AblationResult(
                variant="실적 + 수급 + 가격",
                sources=["FINANCIAL", "FLOW", "PRICE"],
                num_conditions=3,
                num_trades=5,
                cumulative_return=1.1076,
                max_drawdown=-0.1687,
                win_rate=0.6,
                profit_factor=8.13,
            ),
        ],
        cost_sensitivity=[
            CostSensitivityResult(
                multiplier=0.0,
                commission_rate=0.0,
                sell_tax_rate=0.0,
                slippage_rate=0.0,
                num_trades=5,
                cumulative_return=1.15,
                max_drawdown=-0.165,
                win_rate=0.6,
                profit_factor=8.5,
            ),
            CostSensitivityResult(
                multiplier=1.0,
                commission_rate=0.00015,
                sell_tax_rate=0.0018,
                slippage_rate=0.001,
                num_trades=5,
                cumulative_return=1.1076,
                max_drawdown=-0.1687,
                win_rate=0.6,
                profit_factor=8.13,
            ),
        ],
        subperiod=[
            SubperiodResult(
                label="전반부",
                start_date=date(2016, 1, 1),
                end_date=date(2020, 12, 30),
                num_trades=0,
                cumulative_return=0.0,
                max_drawdown=0.0,
                win_rate=None,
                profit_factor=None,
            ),
        ],
        skipped=["인샘플/아웃오브샘플 분리 — 후순위(제출 후 확장)"],
    )


def _make_complete_run(
    tmp_path: Path,
    *,
    modifications: list[StrategyModification] | None = None,
) -> RunStore:
    store = RunStore(tmp_path / "outputs", "RUN-REPORT-1")
    store.save_run_manifest(
        RunManifest(
            run_id="RUN-REPORT-1",
            company_query="000660",
            corp_code="00164779",
            corp_name="SK하이닉스",
            corp_eng_name="SK hynix Inc.",
            stock_code="000660",
            as_of_date="2025-12-31",
            created_at="2026-07-15T15:00:00+09:00",
            code_version="abc1234",
        )
    )
    run_state = create_run_state("RUN-REPORT-1", "SK하이닉스", "2025-12-31", actor="test-fixture")
    for target in FORWARD_ORDER[1 : FORWARD_ORDER.index(PipelineState.COMPLETE) + 1]:
        run_state = advance(run_state, target, actor="test-fixture")
    store.save_run_state(run_state)

    EvidencePackageStore(store.run_dir).save(
        EvidencePackage(
            corp_code="00164779",
            as_of_date="2025-12-31",
            lookback_years=5,
            fs_scope="CFS",
            generated_at=STAMP,
            evidence=[_evidence("FIN_A"), _evidence("FIN_B"), _evidence("FIN_C")],
        )
    )
    store.save_candidate_analysis(
        CandidateAnalysis(
            financial_findings=[_finding("F1", "재무")],
            business_findings=[],
            industry_findings=[_finding("I1", "산업")],
            catalyst_candidates=[],
            risk_candidates=[],
            relationship_candidates=[],
            conflicting_evidence=[],
            missing_information=["추가 데이터 필요"],
        )
    )
    store.save_analyst_view(
        AnalystView(
            view_id="view-1",
            author="검증자",
            research_question="흑자 전환 국면에서 돌파가 지속되는가",
            core_thesis="실적 턴어라운드와 외인 순매수가 겹칠 때만 돌파를 신뢰한다",
            selected_evidence_ids=["FIN_A", "FIN_B"],
            rejected_evidence_ids=["FIN_C"],
            evidence_selection_reason="턴어라운드 근거가 가설과 직접 연결된다",
            rejected_evidence_reasons={"FIN_C": "이번 검증 범위 밖"},
            interpretation="이익 모멘텀 구간에서 신호 질이 높다",
            expected_mechanism="실적 확인 → 외인 유입 → 돌파",
            counterarguments=["사이클 고점 되돌림 위험"],
            uncertainties=["업황 사이클 국면 판단"],
            created_at=STAMP,
            updated_at=STAMP,
        )
    )
    store.save_human_hypothesis(
        HumanInvestmentHypothesis(
            hypothesis_id="hyp-1",
            view_id="view-1",
            author="검증자",
            thesis="영업이익 개선 + 외인 순매수 + 돌파 동시 충족 시 초과수익",
            economic_rationale="확인된 돌파는 거짓 신호가 적다",
            expected_mechanism="실적 서프라이즈 → 자금 유입 → 추세",
            selected_variables=["operating_income_yoy"],
            expected_direction="positive",
            investment_horizon_days=60,
            evidence_ids=["FIN_A"],
            falsification_conditions=["승률 50% 미만이면 기각"],
            limitations=["단일 종목"],
            status="PARTIALLY_SUPPORTED",
            created_at=STAMP,
            updated_at=STAMP,
            approved_by="검증자",
            approved_at=STAMP,
        )
    )
    store.save_strategy_review(
        StrategyReview(
            review_id="review-1",
            hypothesis_id="hyp-1",
            llm_draft_strategy=_STRATEGY,
            final_strategy=_STRATEGY,
            modifications=modifications or [],
            approval_reason="초안이 가설·체결 규칙을 그대로 반영해 승인",
            approved_by="검증자",
            approved_at=STAMP,
        )
    )
    (store.run_dir / "backtest_result.json").write_text(
        _backtest_result().model_dump_json(indent=2), encoding="utf-8"
    )
    store.save_backtest_interpretation(
        BacktestInterpretation(
            interpretation_id="interp-1",
            hypothesis_id="hyp-1",
            strategy_id="TestStrat",
            author="검증자",
            main_findings="손익비는 우수하나 노출률이 낮다",
            supporting_results=["Profit Factor > 1, 승률 우위"],
            contradicting_results=["B&H 대비 절대수익 미달 구간 존재"],
            limitations=["표본 거래 수 적음"],
            hypothesis_decision="PARTIALLY_SUPPORTED",
            decision_reason="방향성은 지지되나 절대수익 보완 필요",
            followup_tests=["노출률 통제 후 비교"],
            created_at=STAMP,
        )
    )
    store.append_ai_usage(
        AIUsageRecord(
            usage_id="usage-candidate_analysis-1",
            stage="candidate_analysis",
            model="claude-haiku-4-5",
            prompt_name="candidate_analysis",
            prompt_version="v1",
            input_artifact_ids=["evidence_package.json"],
            output_artifact_ids=["candidate_analysis.json"],
            ai_role="후보 정리",
            human_review_required=True,
            created_at=STAMP,
        )
    )
    store.append_ai_usage(
        AIUsageRecord(
            usage_id="usage-strategy_translation-1",
            stage="strategy_translation",
            model="claude-haiku-4-5",
            prompt_name="strategy_translation",
            prompt_version="v1",
            input_artifact_ids=["human_investment_hypothesis.json"],
            output_artifact_ids=["strategy_draft.json"],
            ai_role="전략 초안 변환",
            human_review_required=True,
            created_at=STAMP,
        )
    )
    return store


def _build(store: RunStore, *, ai_explanation: str | None = "AI 초안 설명입니다.") -> str:
    return build_research_report(
        store,
        robustness=_robustness(),
        ai_explanation=ai_explanation,
        ai_explanation_origin="AI_DRAFT_HUMAN_APPROVED",
        generated_at=STAMP,
    )


# --- 15 섹션·순서·제목 ------------------------------------------------------


def test_all_fifteen_sections_present_and_ordered(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))
    numbers = [int(n) for n in re.findall(r"^## (\d+)\. ", md, re.M)]
    assert numbers == list(range(1, 16))


def test_argumentative_title_uses_core_thesis(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))
    first_line = md.splitlines()[0]
    assert first_line == "# SK하이닉스: 실적 턴어라운드와 외인 순매수가 겹칠 때만 돌파를 신뢰한다"


def test_authorship_tags_on_key_sections(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))
    # 명세 W3c §2.2 태그 매핑
    assert re.search(r"^## 1\. .*\[Python 계산\]$", md, re.M)
    assert re.search(r"^## 2\. .*\[사용자 작성\]$", md, re.M)
    assert re.search(r"^## 4\. .*\[AI 후보·초안 — 사용자 승인\]$", md, re.M)
    assert re.search(r"^## 9\. .*\[AI 후보·초안 — 사용자 승인\]$", md, re.M)
    assert re.search(r"^## 10\. .*\[Python 계산\]$", md, re.M)
    assert re.search(r"^## 15\. .*\[Python 계산\]$", md, re.M)
    # 세 태그가 모두 등장한다
    assert "[사용자 작성]" in md
    assert "[Python 계산]" in md
    assert "[AI 후보·초안 — 사용자 승인]" in md


# --- 무수정 승인 vs 수정 내역 -----------------------------------------------


def test_no_modification_approval_labeled(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))  # modifications=[]
    assert "무수정 승인" in md
    assert "초안이 가설·체결 규칙을 그대로 반영해 승인" in md


def test_modifications_rendered_as_table(tmp_path: Path) -> None:
    mods = [
        StrategyModification(
            field_path="entry.all[0].right",
            draft_value=0.2,
            final_value=0.3,
            reason="임계값 상향",
            modified_by="검증자",
        )
    ]
    md = _build(_make_complete_run(tmp_path, modifications=mods))
    assert "무수정 승인" not in md
    assert "entry.all[0].right" in md
    assert "임계값 상향" in md


# --- 백테스트·AI 설명·강건성 -------------------------------------------------


def test_performance_metrics_and_robustness_tables(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))
    assert "누적수익률" in md and "110.76%" in md
    assert "조건 제거 분석" in md
    assert "가격 모멘텀만" in md
    assert "거래비용 민감도" in md
    assert "하위 기간 분석" in md
    assert "인샘플/아웃오브샘플" in md  # §24.2 잔여 skipped 노출


def test_ai_explanation_disclaimer_present(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path), ai_explanation="전략은 누적 110% 수익을 냈다.")
    assert "아래는 AI가 작성한 초안 설명이며" in md
    assert "최종 해석(§11~14)은 사용자가 작성했다" in md
    assert "전략은 누적 110% 수익을 냈다." in md


def test_ai_explanation_failure_note_when_none(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path), ai_explanation=None)
    assert "AI 설명 초안 생성 실패" in md
    # 실패해도 15 섹션은 모두 생성된다
    assert [int(n) for n in re.findall(r"^## (\d+)\. ", md, re.M)] == list(range(1, 16))


def test_selected_evidence_and_ai_usage_rendered(tmp_path: Path) -> None:
    md = _build(_make_complete_run(tmp_path))
    assert "FIN_A" in md and "FIN_B" in md  # 선택 근거 상세
    assert "candidate_analysis" in md and "strategy_translation" in md  # ai_usage_log 표


# --- 결정성·부재 처리 --------------------------------------------------------


def test_deterministic_same_input_same_output(tmp_path: Path) -> None:
    store = _make_complete_run(tmp_path)
    assert _build(store) == _build(store)


def test_missing_backtest_result_raises(tmp_path: Path) -> None:
    store = _make_complete_run(tmp_path)
    (store.run_dir / "backtest_result.json").unlink()
    with pytest.raises(DataValidationError):
        _build(store)


def test_missing_analyst_view_raises(tmp_path: Path) -> None:
    store = _make_complete_run(tmp_path)
    (store.run_dir / "analyst_view.json").unlink()
    with pytest.raises(DataValidationError):
        _build(store)
