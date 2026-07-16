"""Streamlit UI 테스트 공용 픽스처 (docs/specs/W3c-report-ui.md §3.2).

AppTest는 ``app/streamlit_app.py``\\ 를 실제로 실행하므로, 이 모듈이 만드는
run 픽스처는 ``research_backtest.core.hitl`` 모델을 직접 사용해 CLI
(``app/commands/hitl_flow.py``)가 만드는 것과 동일한 형식의 산출물을
디스크에 미리 써 둔다(참고: ``tests/unit/test_cli_hitl.py``\\ 의 픽스처
관례와 동일한 패턴).
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.config import Settings
from research_backtest.core.constants import FsDiv, PeriodicReportType, ReprtCode
from research_backtest.core.dart.financial_api import CollectionSummary, RequestOutcome
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dart.xbrl_downloader import XbrlDownloadOutcome
from research_backtest.core.financials.pipeline import (
    METRICS_FILENAME,
    CoverageReport,
    FileSummary,
    FinancialBuildReport,
    MatchingReport,
    ValidationCheck,
    financials_out_dir,
)
from research_backtest.core.hitl.models import (
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    Finding,
    HumanInvestmentHypothesis,
    HypothesisStatus,
    RunManifest,
    StrategyReview,
    now_kst_iso,
)
from research_backtest.core.hitl.states import (
    FORWARD_ORDER,
    PipelineState,
    advance,
    create_run_state,
)
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.market.collector import (
    DAILY_FILENAME,
    DatasetOutcome,
    MarketCollectionSummary,
    market_calendar_path,
    market_normalized_stock_dir,
)
from research_backtest.core.models import DartCorporation
from research_backtest.core.reconciliation.pipeline import (
    BucketSummary,
    ParseSummary,
    ReconciliationReport,
)
from research_backtest.quant.backtest.metrics import (
    BacktestResult,
    BenchmarkComparison,
    BuyHoldComparison,
)

CORP_CODE = "00164779"
CORP_NAME = "SK하이닉스"
STOCK_CODE = "000660"
AS_OF_DATE = "2025-12-31"

#: 화면① 데이터 준비 픽스처용 — DartCorporation 그 자체(§4). ``resolve_corp``를
#: 통째로 monkeypatch할 때 반환값으로 쓴다(실 DART 호출 없이 corp 식별을 대체).
SK_HYNIX = DartCorporation(
    corp_code=CORP_CODE,
    corp_name=CORP_NAME,
    corp_eng_name="SK hynix Inc.",
    stock_code=STOCK_CODE,
    modify_date="20250102",
)

#: 비상장 법인 픽스처 — create-run의 "비상장" 분기 테스트용(§4).
UNLISTED_CORP = DartCorporation(
    corp_code="99999999",
    corp_name="비상장테스트",
    corp_eng_name=None,
    stock_code=None,
    modify_date="20250102",
)

EVIDENCE_IDS = [
    "FIN_OP_INCOME_TURN_2024Q4",
    "FIN_NET_INCOME_TURN_FY2024",
    "FIN_REVENUE_YOY_2024Q3",
]


@pytest.fixture
def ui_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """AppTest가 실행할 ``streamlit_app.py``\\ 가 읽을 ``get_settings()``\\ 를
    tmp 경로로 고정한다 — ``.env`` 파일 유무와 무관하게 환경변수가 우선한다."""
    data_dir = tmp_path / "data"
    outputs_dir = tmp_path / "outputs"
    data_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("DATA_DIR", str(data_dir))
    monkeypatch.setenv("OUTPUTS_DIR", str(outputs_dir))
    monkeypatch.setenv("DART_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "")
    return Settings(_env_file=None, data_dir=data_dir, outputs_dir=outputs_dir)


def make_run_store(
    settings: Settings,
    run_id: str,
    *,
    target_state: PipelineState,
    company: str = CORP_NAME,
    as_of_date: str = AS_OF_DATE,
) -> RunStore:
    """``target_state``까지 정방향으로 전진시킨 run_manifest·run_state를 저장한다.

    ``tests/unit/test_cli_hitl.py``\\ 의 ``_make_run``\\ 과 동일한 관례
    (actor="test-fixture") — 전이 이력 자체를 검증하는 테스트가 아니라 화면
    잠금·산출물 존재를 검증하는 픽스처 셋업이다.
    """
    store = RunStore(settings.outputs_dir, run_id)
    manifest = RunManifest(
        run_id=run_id,
        company_query=company,
        corp_code=CORP_CODE,
        corp_name=company,
        corp_eng_name="SK hynix Inc.",
        stock_code=STOCK_CODE,
        as_of_date=as_of_date,
        created_at=now_kst_iso(),
        code_version=None,
    )
    store.save_run_manifest(manifest)

    run_state = create_run_state(run_id, company, as_of_date, actor="test-fixture")
    target_idx = FORWARD_ORDER.index(target_state)
    for target in FORWARD_ORDER[1 : target_idx + 1]:
        run_state = advance(run_state, target, actor="test-fixture")
    store.save_run_state(run_state)
    return store


def write_evidence_manifest(run_dir: Path, evidence_ids: list[str] = EVIDENCE_IDS) -> None:
    payload = {"evidence": [{"evidence_id": eid} for eid in evidence_ids]}
    (run_dir / "evidence_manifest.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )


def make_candidate_analysis(evidence_ids: list[str] = EVIDENCE_IDS) -> CandidateAnalysis:
    """financial_findings 1건 + conflicting_evidence 1건 — 빈 목록·채워진 목록을 모두 예시한다."""
    return CandidateAnalysis(
        financial_findings=[
            Finding(
                finding_id="F-1",
                category="PROFITABILITY",
                statement="영업이익이 흑자로 전환되었다.",
                evidence_ids=[evidence_ids[0]],
                confidence=0.8,
                source_type="financial_metrics",
                limitations=[],
            )
        ],
        business_findings=[],
        industry_findings=[],
        catalyst_candidates=[],
        risk_candidates=[],
        relationship_candidates=[],
        conflicting_evidence=[
            Finding(
                finding_id="F-2",
                category="PROFITABILITY",
                statement="순이익은 여전히 변동성이 크다.",
                evidence_ids=[evidence_ids[1]],
                confidence=0.4,
                source_type="financial_metrics",
                limitations=["표본이 1개 분기뿐"],
            )
        ],
        missing_information=["업황 지표 데이터 없음"],
    )


def make_analyst_view(
    *, view_id: str = "view-1", selected: list[str] | None = None, rejected: list[str] | None = None
) -> AnalystView:
    selected = selected if selected is not None else EVIDENCE_IDS[:2]
    rejected = rejected if rejected is not None else []
    now = now_kst_iso()
    return AnalystView(
        view_id=view_id,
        author="테스트 작성자",
        research_question="실적 회복이 주가에 선반영되었는가?",
        core_thesis="서프라이즈 여부가 향후 주가를 결정한다.",
        selected_evidence_ids=selected,
        rejected_evidence_ids=rejected,
        evidence_selection_reason="1차 공시 자료를 우선한다.",
        rejected_evidence_reasons=dict.fromkeys(rejected, "이번 검증 범위 밖"),
        interpretation="흑자 전환 이후 모멘텀이 이어진다.",
        expected_mechanism="실적 확인 → 수급 유입 → 추세 지속",
        counterarguments=["이미 선반영되었을 수 있다."],
        uncertainties=["업황 사이클 판단"],
        created_at=now,
        updated_at=now,
    )


def make_hypothesis(
    *,
    hypothesis_id: str = "hyp-1",
    view_id: str = "view-1",
    evidence_ids: list[str] | None = None,
    status: HypothesisStatus = HypothesisStatus.APPROVED,
    approved_by: str | None = "테스트 작성자",
) -> HumanInvestmentHypothesis:
    evidence_ids = evidence_ids if evidence_ids is not None else EVIDENCE_IDS[:1]
    now = now_kst_iso()
    return HumanInvestmentHypothesis(
        hypothesis_id=hypothesis_id,
        view_id=view_id,
        author="테스트 작성자",
        thesis="흑자 전환이 이익률 개선으로 이어진다.",
        economic_rationale="원가 구조 개선이 지속된다.",
        expected_mechanism="ASP 상승 → 이익률 개선",
        selected_variables=["operating_income_yoy"],
        expected_direction="up",
        investment_horizon_days=60,
        evidence_ids=evidence_ids,
        falsification_conditions=["2개 분기 연속 컨센서스 하회 시 기각"],
        limitations=[],
        status=status,
        created_at=now,
        updated_at=now,
        approved_by=approved_by if status == HypothesisStatus.APPROVED else None,
        approved_at=now if status == HypothesisStatus.APPROVED else None,
    )


def make_strategy(right: float = 0.2) -> dict[str, object]:
    return {
        "strategy_name": "demo_strategy",
        "version": "1.0",
        "universe": {"type": "single_asset", "tickers": [STOCK_CODE]},
        "entry": {"all": [{"left": "operating_income_yoy", "operator": ">", "right": right}]},
        "exit": {"any": [{"type": "max_holding_days", "value": 60}]},
        "execution": {"signal_time": "close", "trade_time": "next_open"},
    }


def make_strategy_review(
    *, hypothesis_id: str = "hyp-1", draft: dict[str, object] | None = None
) -> StrategyReview:
    draft = draft if draft is not None else make_strategy()
    now = now_kst_iso()
    return StrategyReview(
        review_id="review-1",
        hypothesis_id=hypothesis_id,
        llm_draft_strategy=draft,
        final_strategy=draft,
        modifications=[],
        approval_reason="초안을 그대로 승인",
        approved_by="테스트 작성자",
        approved_at=now,
    )


def make_backtest_result(*, strategy_name: str = "demo_strategy") -> BacktestResult:
    return BacktestResult(
        strategy_name=strategy_name,
        start_date="2016-01-01",
        end_date=AS_OF_DATE,
        trading_days=500,
        fs_scope="CFS",
        initial_cash=100_000_000.0,
        commission_rate=0.00015,
        sell_tax_rate=0.0018,
        slippage_rate=0.001,
        cumulative_return=0.35,
        cagr=0.08,
        annual_volatility=0.22,
        sharpe=0.9,
        sortino=1.1,
        max_drawdown=-0.18,
        calmar=0.44,
        win_rate=0.55,
        avg_win=1_200_000.0,
        avg_loss=-800_000.0,
        payoff_ratio=1.5,
        profit_factor=1.8,
        num_trades=12,
        avg_holding_days=25.0,
        market_exposure=0.4,
        benchmark=BenchmarkComparison(
            name="KOSPI", cumulative_return=0.2, excess_return=0.15, information_ratio=0.5
        ),
        buy_hold=BuyHoldComparison(cumulative_return=0.25, cagr=0.05, max_drawdown=-0.3),
        has_trades=True,
    )


def write_backtest_artifacts(run_dir: Path, result: BacktestResult) -> None:
    (run_dir / "strategy_spec.json").write_text(
        json.dumps(make_strategy(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run_dir / "backtest_result.json").write_text(
        result.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    (run_dir / "daily_portfolio.csv").write_text(
        "date,position,shares,cash,equity,daily_return\n"
        "2016-01-04,0,0,100000000.0,100000000.0,0.0\n"
        "2016-01-05,1,100,50000000.0,101000000.0,0.01\n"
        "2016-01-06,1,100,50000000.0,102000000.0,0.0099\n",
        encoding="utf-8",
    )
    (run_dir / "trade_log.csv").write_text(
        "entry_signal_date,entry_date,entry_price,shares,exit_signal_date,exit_date,exit_price,"
        "holding_days,pnl,pnl_pct,exit_reason,costs\n"
        "2016-01-04,2016-01-05,50000.0,100,2016-02-04,2016-02-05,55000.0,20,480000.0,0.096,"
        "condition,20000.0\n",
        encoding="utf-8",
    )


def make_backtest_interpretation(
    *, hypothesis_id: str = "hyp-1", strategy_id: str = "demo_strategy"
) -> BacktestInterpretation:
    return BacktestInterpretation(
        interpretation_id="interp-1",
        hypothesis_id=hypothesis_id,
        strategy_id=strategy_id,
        author="테스트 작성자",
        main_findings="흑자 전환 이후 초과수익이 관측되었다.",
        supporting_results=["누적수익률이 벤치마크를 상회했다."],
        contradicting_results=["최대낙폭이 예상보다 컸다."],
        regime_dependence=None,
        limitations=["표본 기간이 짧다."],
        hypothesis_decision="SUPPORTED",
        decision_reason="유리한 결과가 우세하다.",
        revised_hypothesis=None,
        followup_tests=["다음 분기 재검증"],
        created_at=now_kst_iso(),
    )


# ---------------------------------------------------------------------------
# 화면① 데이터 준비 픽스처 (docs/specs/W3d-ui-data-prep.md §4) — test_data_prep.py·
# test_streamlit_app.py가 공유한다. collector 반환값은 core 모델 그대로 구성해
# (SimpleNamespace 등 대역 없이) 타입 안전성을 유지한다(mypy strict).
# ---------------------------------------------------------------------------


def mark_financials_ready(settings: Settings, corp_code: str = CORP_CODE) -> None:
    """financial_metrics.parquet을 빈 파일로 만들어 ensure_data_ready를 통과시킨다."""
    path = financials_out_dir(settings.data_dir, corp_code) / METRICS_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def mark_market_ready(settings: Settings, stock_code: str = STOCK_CODE) -> None:
    """daily.parquet·거래일 캘린더를 빈 파일로 만들어 ensure_data_ready를 통과시킨다."""
    daily = market_normalized_stock_dir(settings.data_dir, stock_code) / DAILY_FILENAME
    daily.parent.mkdir(parents=True, exist_ok=True)
    daily.write_bytes(b"")
    calendar = market_calendar_path(settings.data_dir)
    calendar.parent.mkdir(parents=True, exist_ok=True)
    calendar.write_bytes(b"")


def make_collection_summary(*, corp_code: str = CORP_CODE, count: int = 4) -> CollectionSummary:
    return CollectionSummary(
        corp_code=corp_code,
        fetched_at=now_kst_iso(),
        outcomes=[
            RequestOutcome(
                bsns_year="2024",
                reprt_code=ReprtCode.ANNUAL,
                fs_div=FsDiv.CFS,
                result="FETCHED",
                row_count=120,
                sj_div_counts={"BS": 60, "IS": 60},
                rcept_nos=["20250101000001"],
            )
            for _ in range(count)
        ],
    )


def make_market_summary(
    *, stock_code: str = STOCK_CODE, skipped_no_auth: bool = False
) -> MarketCollectionSummary:
    investor_result = "SKIPPED_NO_AUTH" if skipped_no_auth else "FETCHED"
    return MarketCollectionSummary(
        stock_code=stock_code,
        index_code="1001",
        outcomes=[
            DatasetOutcome(dataset="OHLCV", result="FETCHED", row_count=2000),
            DatasetOutcome(
                dataset="INVESTOR_VALUE",
                result=investor_result,
                row_count=0 if skipped_no_auth else 2000,
            ),
        ],
    )


def make_build_report(*, corp_code: str = CORP_CODE, fact_count: int = 42) -> FinancialBuildReport:
    return FinancialBuildReport(
        corp_code=corp_code,
        generated_at=now_kst_iso(),
        scopes=["CFS", "OFS"],
        fact_count=fact_count,
        matching=MatchingReport(
            per_account_matched_rows={},
            unmatched_row_count=0,
            sce_skipped_count=0,
            processed_row_count=fact_count,
            unresolved=[],
        ),
        derivation_gaps=[],
        validations=[
            ValidationCheck(name="accounting_identity", checked=1, passed=True, violations=[])
        ],
        coverage=CoverageReport(
            annual_required_complete=True,
            recent_quarters_income_complete=True,
            missing_annual_required=[],
            missing_recent_quarter_income=[],
            recent_quarters_checked=[],
        ),
        files=FileSummary(
            normalized_facts_rows=fact_count,
            quarterly_financials_rows=10,
            annual_financials_rows=5,
            financial_metrics_rows=5,
        ),
    )


def make_reconciliation_report(
    *, corp_code: str = CORP_CODE, total: int = 100
) -> ReconciliationReport:
    bucket = BucketSummary(total=total, by_status={"MATCH": total}, match_rate=1.0)
    return ReconciliationReport(
        corp_code=corp_code,
        generated_at=now_kst_iso(),
        scopes=["CFS", "OFS"],
        parse=ParseSummary(newly_parsed=[], already_parsed=[], failed=[]),
        total=total,
        by_status={"MATCH": total},
        annual=bucket,
        quarterly=bucket,
        account_year_matrix={},
        failures=[],
        records=[],
    )


def make_filing(
    *, corp_code: str = CORP_CODE, corp_name: str = CORP_NAME, rcept_no: str = "20250301000001"
) -> DartFiling:
    return DartFiling(
        corp_code=corp_code,
        corp_name=corp_name,
        stock_code=STOCK_CODE,
        report_nm="사업보고서(2024.12)",
        rcept_no=rcept_no,
        flr_nm=corp_name,
        rcept_dt=date(2025, 3, 1),
        rm=None,
        report_type=PeriodicReportType.ANNUAL,
        fiscal_period_end=date(2024, 12, 31),
    )


def make_xbrl_outcomes(*, failed: bool = False) -> list[XbrlDownloadOutcome]:
    if failed:
        return [
            XbrlDownloadOutcome(
                rcept_no="20250301000001",
                reprt_code="11011",
                report_name="사업보고서",
                result="FAILED",
                reason="네트워크 오류",
            )
        ]
    return [
        XbrlDownloadOutcome(
            rcept_no="20250301000001",
            reprt_code="11011",
            report_name="사업보고서",
            result="FETCHED",
        )
    ]
