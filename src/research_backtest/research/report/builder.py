"""15-섹션 보고서 마크다운 생성기 (docs/HUMAN_IN_THE_LOOP.md §6, 1804 §16, 명세 W3c §2.2).

:func:`build_research_report`\\ 는 ``RunStore``\\ 로 run 산출물 전부를 로드해 HITL §6의
15개 섹션을 **그 순서 그대로** 마크다운으로 조립한다. 각 섹션 제목 옆에 저작 주체
태그(``[사용자 작성]``·``[Python 계산]``·``[AI 후보·초안 — 사용자 승인]``)를 달아
1804 §16의 출처 표시를 지킨다 — AI 초안과 사용자 해석의 구분을 흐리지 않는다.

결정성: 같은 입력이면 같은 출력이다. 실행 시각은 ``generated_at`` 한 곳에만 들어가며
인자로 주입할 수 있어(테스트 결정성) 나머지 본문은 산출물만으로 재현된다. 산출물
부재는 ``RunStore``·:class:`~research_backtest.research.evidence.EvidencePackageStore`\\ 의
``DataValidationError``\\ 로 전파된다(→CLI exit 1). COMPLETE 상태 run은 전부 존재한다.
"""

from __future__ import annotations

import json
from pathlib import Path

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
    now_kst_iso,
)
from research_backtest.core.hitl.states import RunState
from research_backtest.core.hitl.store import RunStore
from research_backtest.core.llm.client import LlmCallMetadata, LlmTextClient
from research_backtest.core.llm.prompts import load_prompt
from research_backtest.quant.backtest.metrics import BacktestResult
from research_backtest.quant.backtest.robustness import RobustnessReport
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    FinancialEvidence,
)

# 저작 주체 태그(1804 §16, ContentOrigin — 명세 W3c §2.2 표기).
_TAG_USER = "[사용자 작성]"
_TAG_PYTHON = "[Python 계산]"
_TAG_AI = "[AI 후보·초안 — 사용자 승인]"
_TAG_SOURCE = "[데이터 사실]"

_NA = "—"

# result_explanation 프롬프트 — quant/prompts/result_explanation_v1.txt (명세 W3c §2.3).
_EXPLANATION_PROMPT_NAME = "result_explanation"
_EXPLANATION_PROMPT_VERSION = 1
_EXPLANATION_SYSTEM_PROMPT = (
    "당신은 백테스트 성과지표를 사실 위주로 요약하는 보조자다. 성과의 원인을 단정하지 "
    "말고, 유리·불리 양면을 균형 있게 제시하며, 투자 의견·추천을 하지 않는다. 마크다운 "
    "헤더나 코드펜스 없이 한국어 일반 문단(2~4문단)으로만 답하라."
)


def draft_result_explanation(
    *,
    client: LlmTextClient,
    prompts_dir: Path,
    result: BacktestResult,
    hypothesis: HumanInvestmentHypothesis,
    robustness: RobustnessReport | None,
) -> tuple[str, LlmCallMetadata]:
    """성과지표·가설 요지·강건성 요약으로 LLM 결과 설명 **초안**을 생성한다 (명세 W3c §2.3).

    출력은 JSON이 아니라 일반 텍스트이므로 ``complete_validated``\\ 가 아니라
    :meth:`LlmTextClient.complete_text`\\ 를 직접 쓴다. 이 초안은 부가 기능이며 최종
    해석은 사용자가 작성한다 — 호출부(``generate-report``)는 실패해도 보고서를 계속
    생성한다.
    """
    prompt = load_prompt(prompts_dir, _EXPLANATION_PROMPT_NAME, _EXPLANATION_PROMPT_VERSION)
    user_prompt = prompt.render(
        metrics_summary=_metrics_summary(result),
        hypothesis_summary=_hypothesis_summary(hypothesis),
        robustness_summary=_robustness_summary(robustness),
    )
    text, metadata = client.complete_text(
        system_prompt=_EXPLANATION_SYSTEM_PROMPT, user_prompt=user_prompt
    )
    return text.strip(), metadata


def _metrics_summary(result: BacktestResult) -> str:
    """LLM 프롬프트에 넣는 성과지표 요약(사실 나열, 결정적)."""
    return "\n".join(
        [
            f"- 기간: {result.start_date} ~ {result.end_date} (거래일 {result.trading_days}일)",
            f"- 누적수익률: {_pct(result.cumulative_return)}, CAGR: {_pct(result.cagr)}",
            f"- 최대낙폭(MDD): {_pct(result.max_drawdown)}, "
            f"연환산 변동성: {_pct(result.annual_volatility)}",
            f"- Sharpe: {_ratio(result.sharpe)}, Sortino: {_ratio(result.sortino)}, "
            f"Calmar: {_ratio(result.calmar)}",
            f"- 승률: {_pct(result.win_rate)}, Profit Factor: {_ratio(result.profit_factor)}, "
            f"Payoff: {_ratio(result.payoff_ratio)}",
            f"- 거래 횟수: {result.num_trades}, 평균 보유기간: "
            f"{_ratio(result.avg_holding_days)}거래일, 시장 노출률: {_pct(result.market_exposure)}",
            f"- 벤치마크({result.benchmark.name}) 누적수익률: "
            f"{_pct(result.benchmark.cumulative_return)}, 초과수익률: "
            f"{_pct(result.benchmark.excess_return)}",
            f"- Buy & Hold 누적수익률: {_pct(result.buy_hold.cumulative_return)}, MDD: "
            f"{_pct(result.buy_hold.max_drawdown)}",
        ]
    )


def _hypothesis_summary(hypothesis: HumanInvestmentHypothesis) -> str:
    """LLM 프롬프트에 넣는 가설 요지(재작성 금지 — 참고용)."""
    return "\n".join(
        [
            f"- 가설: {hypothesis.thesis}",
            f"- 예상 방향: {hypothesis.expected_direction}, 목표 보유기간: "
            f"{hypothesis.investment_horizon_days}거래일",
            f"- 선택 변수: {', '.join(hypothesis.selected_variables) or _NA}",
        ]
    )


def _robustness_summary(robustness: RobustnessReport | None) -> str:
    """LLM 프롬프트에 넣는 강건성 요약(조건 제거·비용·기간)."""
    if robustness is None:
        return "- 강건성 분석 미수행"
    parts: list[str] = []
    for ablation in robustness.condition_ablation:
        parts.append(
            f"- 조건제거[{ablation.variant}]: 거래 {ablation.num_trades}회, 누적수익률 "
            f"{_pct(ablation.cumulative_return)}, Profit Factor {_ratio(ablation.profit_factor)}"
        )
    for cost in robustness.cost_sensitivity:
        parts.append(
            f"- 비용 {cost.multiplier:g}배: 누적수익률 {_pct(cost.cumulative_return)}, MDD "
            f"{_pct(cost.max_drawdown)}"
        )
    for sub in robustness.subperiod:
        parts.append(
            f"- 하위기간[{sub.label}]: 거래 {sub.num_trades}회, 누적수익률 "
            f"{_pct(sub.cumulative_return)}"
        )
    return "\n".join(parts) if parts else "- 강건성 변형 없음"


def build_research_report(
    store: RunStore,
    *,
    robustness: RobustnessReport | None,
    ai_explanation: str | None,
    ai_explanation_origin: str,
    generated_at: str | None = None,
) -> str:
    """run 산출물을 15-섹션 마크다운 보고서로 조립한다 (HITL §6, 명세 W3c §2.2).

    ``robustness``\\ 는 §12의 강건성 표에, ``ai_explanation``\\ 은 §10의 AI 설명 초안
    단락에 쓰인다(둘 다 없어도 보고서는 생성되며 부재 사유를 본문에 남긴다).
    ``ai_explanation_origin``\\ 은 AI 설명 초안의 저작 출처 태그 표기용이다.
    """
    manifest = store.load_run_manifest()
    run_state = store.load_run_state()
    evidence_package = EvidencePackageStore(store.run_dir).load()
    candidate_analysis = store.load_candidate_analysis()
    analyst_view = store.load_analyst_view()
    hypothesis = store.load_human_hypothesis()
    review = store.load_strategy_review()
    interpretation = store.load_backtest_interpretation()
    backtest_result = _load_backtest_result(store)
    ai_usage_log = store.load_ai_usage_log()

    stamp = generated_at if generated_at is not None else now_kst_iso()

    lines: list[str] = []
    # 제목은 논지형(HITL §6): 기업명 나열이 아니라 사용자의 핵심 논지를 그대로 부제로 쓴다.
    lines.append(f"# {manifest.corp_name}: {analyst_view.core_thesis}")
    lines.append("")
    lines.append(
        f"*생성 시각(generated_at): {stamp} · run: {manifest.run_id} · "
        f"파이프라인 상태: {run_state.current_state.value}*"
    )
    lines.append("")
    lines.append(
        "> 저작 주체 표기(1804 §16): 각 섹션 제목의 태그는 그 내용을 누가 작성했는지를 뜻한다 — "
        f"{_TAG_USER}(사용자 해석·가설·판단), {_TAG_PYTHON}(결정적 Python 계산), "
        f"{_TAG_AI}(AI가 만든 후보·초안을 사용자가 검토·승인), {_TAG_SOURCE}(원천 공시 사실)."
    )
    lines.append("")

    sections = [
        _section_1(manifest, run_state, stamp),
        _section_2(analyst_view),
        _section_3(analyst_view, interpretation),
        _section_4(analyst_view, evidence_package, candidate_analysis),
        _section_5(analyst_view),
        _section_6(analyst_view),
        _section_7(analyst_view),
        _section_8(hypothesis),
        _section_9(review),
        _section_10(backtest_result, ai_explanation, ai_explanation_origin),
        _section_11(interpretation),
        _section_12(interpretation, robustness),
        _section_13(interpretation),
        _section_14(interpretation, hypothesis),
        _section_15(ai_usage_log),
    ]
    for section in sections:
        lines.extend(section)
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 산출물 로드 보강 (store에 로더가 없는 backtest_result)
# ---------------------------------------------------------------------------


def _load_backtest_result(store: RunStore) -> BacktestResult:
    """backtest_result.json을 로드한다 — 부재는 다른 산출물과 동일하게 DataValidationError."""
    path = store.run_dir / "backtest_result.json"
    if not path.exists():
        raise DataValidationError(
            f"backtest_result.json이(가) 없습니다 ({path}). 백테스트(backtest)를 먼저 실행하세요."
        )
    try:
        return BacktestResult.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise DataValidationError(
            f"backtest_result.json이(가) 올바르지 않습니다 ({path})."
        ) from err


# ---------------------------------------------------------------------------
# 섹션 1~15 (HITL §6 순서 그대로)
# ---------------------------------------------------------------------------


def _section_1(manifest: RunManifest, run_state: RunState, stamp: str) -> list[str]:
    """1. 분석 대상과 기준일 [Python 계산] — manifest 메타."""
    rows = [
        ("기업명", manifest.corp_name),
        ("영문명", manifest.corp_eng_name or _NA),
        ("종목코드", manifest.stock_code),
        ("DART corp_code", manifest.corp_code),
        ("분석 기준일(as-of)", manifest.as_of_date),
        ("run_id", manifest.run_id),
        ("코드 버전", manifest.code_version or _NA),
        ("파이프라인 상태", run_state.current_state.value),
        ("보고서 생성 시각", stamp),
    ]
    lines = [f"## 1. 분석 대상과 기준일 {_TAG_PYTHON}", ""]
    lines.extend(_kv_table(rows))
    return lines


def _section_2(view: AnalystView) -> list[str]:
    """2. 분석 질문 [사용자 작성]."""
    return [
        f"## 2. 분석 질문 {_TAG_USER}",
        "",
        f"> {_inline(view.research_question)}",
        "",
        f"*작성자: {_inline(view.author)}*",
    ]


def _section_3(view: AnalystView, interpretation: BacktestInterpretation) -> list[str]:
    """3. 핵심 결론 [사용자 작성] — core_thesis + interpretation.main_findings."""
    return [
        f"## 3. 핵심 결론 {_TAG_USER}",
        "",
        "**핵심 논지(core thesis)**",
        "",
        f"> {_inline(view.core_thesis)}",
        "",
        "**백테스트 후 주요 발견(사용자 해석)**",
        "",
        f"> {_inline(interpretation.main_findings)}",
    ]


def _section_4(
    view: AnalystView, package: EvidencePackage, analysis: CandidateAnalysis
) -> list[str]:
    """4. 주요 재무·산업 근거 — 선택 evidence 상세 표 + CandidateAnalysis 요약."""
    lines = [f"## 4. 주요 재무·산업 근거 {_TAG_AI}", ""]

    by_id: dict[str, FinancialEvidence] = {e.evidence_id: e for e in package.evidence}
    lines.append(f"### 4.1 사용자가 선택한 근거 상세 {_TAG_SOURCE}")
    lines.append("")
    lines.append("| evidence_id | 서술 | 기간 | 변화율 | 공시일 | 이용가능일 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for eid in view.selected_evidence_ids:
        evidence = by_id.get(eid)
        if evidence is None:
            lines.append(f"| {eid} | (evidence 패키지에 없음) | {_NA} | {_NA} | {_NA} | {_NA} |")
            continue
        lines.append(
            f"| {eid} | {_inline(evidence.statement)} | {evidence.period} | "
            f"{_pct(evidence.change_rate)} | {evidence.filing_date} | {evidence.available_from} |"
        )
    lines.append("")
    lines.append(
        "*근거 수치·서술은 결정적 Python 계산의 산물이며(DART·XBRL 원천), Point-in-Time"
        "(이용가능일 이후에만 사용)을 준수한다.*"
    )
    lines.append("")

    lines.append(f"### 4.2 AI가 정리한 분석 후보 요약 {_TAG_AI}")
    lines.append("")
    lines.append(
        "아래는 AI가 Evidence를 정리해 **참고용 후보**로 제시한 것이며, 최종 채택 여부는 "
        "사용자가 §5에서 판단했다."
    )
    lines.append("")
    counts = [
        ("재무 findings", len(analysis.financial_findings)),
        ("사업 findings", len(analysis.business_findings)),
        ("산업 findings", len(analysis.industry_findings)),
        ("촉매 후보", len(analysis.catalyst_candidates)),
        ("위험 후보", len(analysis.risk_candidates)),
        ("관계 후보", len(analysis.relationship_candidates)),
        ("상충 근거", len(analysis.conflicting_evidence)),
        ("정보 공백", len(analysis.missing_information)),
    ]
    lines.extend(_kv_table([(label, str(count)) for label, count in counts]))
    lines.append("")
    lines.extend(_findings_block("재무 근거 후보", analysis.financial_findings))
    lines.extend(_findings_block("산업 근거 후보", analysis.industry_findings))
    return lines


def _section_5(view: AnalystView) -> list[str]:
    """5. 선택한 근거와 이유 [사용자 작성]."""
    lines = [f"## 5. 선택한 근거와 이유 {_TAG_USER}", ""]
    lines.append("**선택한 근거**: " + (", ".join(view.selected_evidence_ids) or _NA))
    lines.append("")
    lines.append("**선택 이유**")
    lines.append("")
    lines.append(f"> {_inline(view.evidence_selection_reason)}")
    return lines


def _section_6(view: AnalystView) -> list[str]:
    """6. 제외한 근거와 이유 [사용자 작성]."""
    lines = [f"## 6. 제외한 근거와 이유 {_TAG_USER}", ""]
    if not view.rejected_evidence_ids:
        lines.append("제외한 근거가 없다.")
        return lines
    lines.append("| evidence_id | 제외 이유 |")
    lines.append("| --- | --- |")
    for eid in view.rejected_evidence_ids:
        reason = view.rejected_evidence_reasons.get(eid, _NA)
        lines.append(f"| {eid} | {_inline(reason)} |")
    return lines


def _section_7(view: AnalystView) -> list[str]:
    """7. 반대 논리와 불확실성 [사용자 작성]."""
    lines = [f"## 7. 반대 논리와 불확실성 {_TAG_USER}", ""]
    lines.append("**반대 논리(counterarguments)**")
    lines.append("")
    lines.extend(_bullets(view.counterarguments))
    lines.append("")
    lines.append("**불확실성(uncertainties)**")
    lines.append("")
    lines.extend(_bullets(view.uncertainties))
    return lines


def _section_8(hypothesis: HumanInvestmentHypothesis) -> list[str]:
    """8. 투자 가설 [사용자 작성] — 가설 전문 + 승인 기록."""
    lines = [f"## 8. 투자 가설 {_TAG_USER}", ""]
    lines.append(f"**가설(thesis)**: {_inline(hypothesis.thesis)}")
    lines.append("")
    rows = [
        ("경제적 근거", _inline(hypothesis.economic_rationale)),
        ("예상 메커니즘", _inline(hypothesis.expected_mechanism)),
        ("선택 변수", ", ".join(hypothesis.selected_variables) or _NA),
        ("예상 방향", hypothesis.expected_direction),
        ("목표 보유기간(거래일)", str(hypothesis.investment_horizon_days)),
        ("근거 evidence_ids", ", ".join(hypothesis.evidence_ids) or _NA),
    ]
    lines.extend(_kv_table(rows))
    lines.append("")
    lines.append("**반증 조건(falsification_conditions)**")
    lines.append("")
    lines.extend(_bullets(hypothesis.falsification_conditions))
    lines.append("")
    lines.append("**한계(limitations)**")
    lines.append("")
    lines.extend(_bullets(hypothesis.limitations))
    lines.append("")
    lines.append(
        f"**승인 기록**: status={hypothesis.status.value}, "
        f"approved_by={hypothesis.approved_by or _NA}, "
        f"approved_at={hypothesis.approved_at or _NA}, "
        f"content_origin={hypothesis.content_origin}"
    )
    return lines


def _section_9(review: StrategyReview) -> list[str]:
    """9. 전략 규칙과 사용자 수정 내역 [AI 후보·초안 — 사용자 승인]."""
    lines = [f"## 9. 전략 규칙과 사용자 수정 내역 {_TAG_AI}", ""]
    lines.append("**최종 승인 전략(final_strategy)**")
    lines.append("")
    lines.append("```json")
    lines.append(json.dumps(review.final_strategy, ensure_ascii=False, indent=2))
    lines.append("```")
    lines.append("")
    lines.append(f"**사용자 수정 내역** {_TAG_USER}")
    lines.append("")
    if not review.modifications:
        lines.append(
            "AI 초안을 **무수정 승인**했다(초안 = 최종 전략). 승인 사유: "
            f"{_inline(review.approval_reason)}"
        )
    else:
        lines.append("| 필드 경로 | 초안 값 | 최종 값 | 수정 이유 | 수정자 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for mod in review.modifications:
            lines.append(_modification_row(mod))
        lines.append("")
        lines.append(f"승인 사유: {_inline(review.approval_reason)}")
    lines.append("")
    lines.append(
        f"*승인 주체: {_inline(review.approved_by)} · 승인 시각: {review.approved_at}. "
        "전략 초안은 AI가 작성했고, 임계값·조건의 최종 확정과 승인은 사용자가 수행했다.*"
    )
    return lines


def _section_10(
    result: BacktestResult, ai_explanation: str | None, ai_explanation_origin: str
) -> list[str]:
    """10. 백테스트 결과 [Python 계산] 성과표 + AI 설명 초안 [AI 후보·초안]."""
    lines = [f"## 10. 백테스트 결과 {_TAG_PYTHON}", ""]
    lines.append(
        f"기간: {result.start_date} ~ {result.end_date} · 거래일 {result.trading_days}일 · "
        f"fs_scope={result.fs_scope} · 초기자본 {_money(result.initial_cash)}"
    )
    lines.append("")
    lines.append("### 10.1 성과지표 (README §24.1)")
    lines.append("")
    lines.append("| 지표 | 값 |")
    lines.append("| --- | --- |")
    metric_rows = [
        ("누적수익률", _pct(result.cumulative_return)),
        ("CAGR", _pct(result.cagr)),
        ("연환산 변동성", _pct(result.annual_volatility)),
        ("Sharpe", _ratio(result.sharpe)),
        ("Sortino", _ratio(result.sortino)),
        ("최대낙폭(MDD)", _pct(result.max_drawdown)),
        ("Calmar", _ratio(result.calmar)),
        ("승률", _pct(result.win_rate)),
        ("평균 수익 거래", _money(result.avg_win)),
        ("평균 손실 거래", _money(result.avg_loss)),
        ("Payoff Ratio", _ratio(result.payoff_ratio)),
        ("Profit Factor", _ratio(result.profit_factor)),
        ("거래 횟수", str(result.num_trades)),
        ("평균 보유기간(거래일)", _ratio(result.avg_holding_days)),
        ("시장 노출률", _pct(result.market_exposure)),
    ]
    for label, value in metric_rows:
        lines.append(f"| {label} | {value} |")
    lines.append("")
    lines.append(
        f"**벤치마크({result.benchmark.name})**: 누적수익률 "
        f"{_pct(result.benchmark.cumulative_return)} · 초과수익률 "
        f"{_pct(result.benchmark.excess_return)} · Information Ratio "
        f"{_ratio(result.benchmark.information_ratio)}"
    )
    lines.append("")
    lines.append(
        f"**Buy & Hold**: 누적수익률 {_pct(result.buy_hold.cumulative_return)} · CAGR "
        f"{_pct(result.buy_hold.cagr)} · MDD {_pct(result.buy_hold.max_drawdown)}"
    )
    lines.append("")
    lines.append(f"### 10.2 AI 설명 초안 {_TAG_AI}")
    lines.append("")
    lines.append(
        "아래는 AI가 작성한 초안 설명이며(성과지표를 사실 위주로 요약), 최종 해석"
        "(§11~14)은 사용자가 작성했다. 투자 의견이 아니다."
    )
    lines.append("")
    if ai_explanation is None:
        lines.append(
            "AI 설명 초안 생성 실패 — 사용자 해석만 수록(§11~14). "
            "LLM 초안은 부가 기능이며 보고서 생성의 게이트가 아니다."
        )
    else:
        lines.append(ai_explanation.strip())
        lines.append("")
        lines.append(f"*출처 태그: {ai_explanation_origin} — AI 초안, 사용자 검토 대상.*")
    return lines


def _section_11(interpretation: BacktestInterpretation) -> list[str]:
    """11. 가설에 유리한 결과 [사용자 작성]."""
    lines = [f"## 11. 가설에 유리한 결과 {_TAG_USER}", ""]
    if interpretation.supporting_results:
        lines.extend(_bullets(interpretation.supporting_results))
    else:
        lines.append("사용자가 제시한 유리한 결과가 없다.")
    return lines


def _section_12(
    interpretation: BacktestInterpretation, robustness: RobustnessReport | None
) -> list[str]:
    """12. 가설에 불리한 결과 [사용자 작성] + 강건성 표 [Python 계산]."""
    lines = [f"## 12. 가설에 불리한 결과 {_TAG_USER}", ""]
    if interpretation.contradicting_results:
        lines.extend(_bullets(interpretation.contradicting_results))
    else:
        lines.append("사용자가 제시한 불리한 결과가 없다.")
    lines.append("")
    lines.append(f"### 12.1 강건성 분석 {_TAG_PYTHON}")
    lines.append("")
    if robustness is None:
        lines.append("강건성 분석이 수행되지 않았다(robustness_report 부재).")
        return lines
    lines.extend(_robustness_block(robustness))
    return lines


def _section_13(interpretation: BacktestInterpretation) -> list[str]:
    """13. 최종 판단 [사용자 작성]."""
    lines = [f"## 13. 최종 판단 {_TAG_USER}", ""]
    lines.append(f"> {_inline(interpretation.decision_reason)}")
    lines.append("")
    if interpretation.regime_dependence:
        lines.append(f"**국면 의존성**: {_inline(interpretation.regime_dependence)}")
        lines.append("")
    lines.append("**한계(limitations)**")
    lines.append("")
    lines.extend(_bullets(interpretation.limitations))
    return lines


def _section_14(
    interpretation: BacktestInterpretation, hypothesis: HumanInvestmentHypothesis
) -> list[str]:
    """14. 가설 채택·수정·기각 여부 [사용자 작성]."""
    lines = [f"## 14. 가설 채택·수정·기각 여부 {_TAG_USER}", ""]
    lines.append(
        f"**판정(hypothesis_decision)**: {interpretation.hypothesis_decision} "
        f"→ 갱신된 가설 상태: {hypothesis.status.value}"
    )
    lines.append("")
    if interpretation.revised_hypothesis:
        lines.append(f"**수정된 가설**: {_inline(interpretation.revised_hypothesis)}")
        lines.append("")
    lines.append("**추가 검증 제안(followup_tests)**")
    lines.append("")
    lines.extend(_bullets(interpretation.followup_tests))
    return lines


def _section_15(ai_usage_log: list[AIUsageRecord]) -> list[str]:
    """15. AI가 수행한 작업과 사용자가 수행한 작업 [Python 계산] — ai_usage_log 집계."""
    lines = [f"## 15. AI가 수행한 작업과 사용자가 수행한 작업 {_TAG_PYTHON}", ""]
    lines.append("### 15.1 AI 호출 기록 (ai_usage_log)")
    lines.append("")
    if not ai_usage_log:
        lines.append("기록된 AI 호출이 없다.")
    else:
        lines.append("| 단계(stage) | 모델 | 프롬프트 | 버전 | 역할 | 입력 → 출력 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for record in ai_usage_log:
            lines.append(_usage_row(record))
    lines.append("")
    lines.append("### 15.2 AI vs 사용자 역할 구분 (docs/AI_ROLE_BOUNDARY.md)")
    lines.append("")
    lines.append(
        "- **AI 수행**: Evidence 정리(후보), 가설 후보 제시, 전략 DSL 초안 변환, 백테스트 "
        "결과 설명 초안. 모두 사용자 검토·승인을 전제로 한 후보·초안이다."
    )
    lines.append(
        "- **사용자 수행**: 분석 관점 작성, 근거 선택·제외, 투자 가설 작성·승인, 전략 초안 "
        "검토·수정·승인, 백테스트 결과 해석·가설 판정. 최종 투자 판단은 전적으로 사용자의 몫이다."
    )
    return lines


# ---------------------------------------------------------------------------
# 렌더링 헬퍼
# ---------------------------------------------------------------------------


def _robustness_block(robustness: RobustnessReport) -> list[str]:
    """강건성 3표(조건 제거·비용 민감도·하위 기간) + 미수행 항목."""
    lines: list[str] = []
    lines.append("**조건 제거 분석 (README §24.3)**")
    lines.append("")
    if robustness.condition_ablation:
        lines.append("| 변형 | 조건 수 | 거래 | 누적수익률 | MDD | 승률 | Profit Factor |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for ablation in robustness.condition_ablation:
            lines.append(
                f"| {ablation.variant} | {ablation.num_conditions} | {ablation.num_trades} | "
                f"{_pct(ablation.cumulative_return)} | {_pct(ablation.max_drawdown)} | "
                f"{_pct(ablation.win_rate)} | {_ratio(ablation.profit_factor)} |"
            )
    else:
        lines.append("구성 가능한 조건 제거 변형이 없다(아래 미수행 항목 참조).")
    lines.append("")

    lines.append("**거래비용 민감도 (0배/1배/2배)**")
    lines.append("")
    lines.append("| 배율 | 수수료 | 매도세 | 슬리피지 | 거래 | 누적수익률 | MDD |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for cost in robustness.cost_sensitivity:
        lines.append(
            f"| {cost.multiplier:g}배 | {_pct(cost.commission_rate)} | "
            f"{_pct(cost.sell_tax_rate)} | {_pct(cost.slippage_rate)} | {cost.num_trades} | "
            f"{_pct(cost.cumulative_return)} | {_pct(cost.max_drawdown)} |"
        )
    lines.append("")

    lines.append("**하위 기간 분석 (이분할)**")
    lines.append("")
    if robustness.subperiod:
        lines.append("| 구간 | 시작 | 종료 | 거래 | 누적수익률 | MDD | 승률 |")
        lines.append("| --- | --- | --- | --- | --- | --- | --- |")
        for sub in robustness.subperiod:
            lines.append(
                f"| {sub.label} | {sub.start_date} | {sub.end_date} | {sub.num_trades} | "
                f"{_pct(sub.cumulative_return)} | {_pct(sub.max_drawdown)} | {_pct(sub.win_rate)} |"
            )
    else:
        lines.append("하위 기간 분석을 수행하지 못했다(아래 미수행 항목 참조).")
    lines.append("")

    lines.append("**미수행·후순위 항목 (§24.2 잔여 — 조용한 누락 금지)**")
    lines.append("")
    lines.extend(_bullets(robustness.skipped))
    return lines


def _findings_block(title: str, findings: list[Finding]) -> list[str]:
    """Finding 목록을 소제목 + 불릿으로 렌더링(빈 목록이면 생략)."""
    if not findings:
        return []
    lines = [f"**{title}**", ""]
    for finding in findings:
        lines.append(
            f"- {_inline(finding.statement)} "
            f"(confidence={finding.confidence:.2f}, evidence={', '.join(finding.evidence_ids)})"
        )
    lines.append("")
    return lines


def _modification_row(mod: StrategyModification) -> str:
    """StrategyModification 1건을 표 행으로 렌더링한다."""
    return (
        f"| {_inline(mod.field_path)} | {_inline(_stringify(mod.draft_value))} | "
        f"{_inline(_stringify(mod.final_value))} | {_inline(mod.reason)} | "
        f"{_inline(mod.modified_by)} |"
    )


def _usage_row(record: AIUsageRecord) -> str:
    """AIUsageRecord 1건을 표 행으로 렌더링한다."""
    inputs = ", ".join(record.input_artifact_ids)
    outputs = ", ".join(record.output_artifact_ids)
    return (
        f"| {record.stage} | {record.model} | {record.prompt_name} | {record.prompt_version} | "
        f"{_inline(record.ai_role)} | {inputs} → {outputs} |"
    )


def _kv_table(rows: list[tuple[str, str]]) -> list[str]:
    """(키, 값) 목록을 2열 마크다운 표로 만든다."""
    lines = ["| 항목 | 값 |", "| --- | --- |"]
    for key, value in rows:
        lines.append(f"| {key} | {_inline(value)} |")
    return lines


def _bullets(items: list[str]) -> list[str]:
    """문자열 목록을 마크다운 불릿으로 만든다(빈 목록이면 '(없음)')."""
    if not items:
        return ["- (없음)"]
    return [f"- {_inline(item)}" for item in items]


def _stringify(value: object) -> str:
    """StrategyModification의 draft/final 값(임의 JSON)을 표 셀 문자열로 만든다."""
    if value is None:
        return _NA
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _inline(text: str) -> str:
    """표·인용에 안전하도록 개행을 공백으로, 파이프를 이스케이프한다."""
    return text.replace("\n", " ").replace("\r", " ").replace("|", "\\|").strip()


def _pct(value: float | None) -> str:
    """비율을 백분율(소수 2자리)로 — None은 대시."""
    return f"{value * 100:.2f}%" if value is not None else _NA


def _ratio(value: float | None) -> str:
    """비율·배수를 소수 4자리로 — None은 대시."""
    return f"{value:.4f}" if value is not None else _NA


def _money(value: float | None) -> str:
    """금액을 천단위 구분 + '원'으로 — None은 대시."""
    return f"{value:,.0f}원" if value is not None else _NA


__all__ = ["build_research_report", "draft_result_explanation"]
