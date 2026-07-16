"""후보 생성 실호출 integration 테스트 (명세 W3b §2.4 DoD) — 인증·실데이터 없으면 skip.

실행: 레포 루트에서 메인 레포 .env를 주입하고
``DATA_DIR=$PWD/data pytest -m integration tests/integration/test_candidates_live.py``.
구독 계정 rate limit을 고려해 실 호출은 최소화한다(분석 1회 + 가설 1회 — §2.4 허용).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.financials.pipeline import METRICS_FILENAME, financials_out_dir
from research_backtest.core.llm.client import LlmTextClient, create_llm_client
from research_backtest.core.llm.config import LlmConfig, load_llm_config
from research_backtest.research.candidates.generator import (
    PROMPTS_DIR,
    generate_candidate_analysis,
    generate_hypothesis_candidates,
    select_evidence_for_prompt,
)
from research_backtest.research.evidence import EvidencePackage, build_financial_evidence

pytestmark = pytest.mark.integration

SK_HYNIX = "00164779"
_AS_OF = date(2025, 12, 31)


@pytest.fixture(scope="module")
def data_dir() -> Path:
    dd = get_settings().data_dir
    if not (financials_out_dir(dd, SK_HYNIX) / METRICS_FILENAME).exists():
        pytest.skip(f"실데이터 없음(financial_metrics.parquet) — DATA_DIR 확인: {dd}")
    return dd


@pytest.fixture(scope="module")
def llm_config() -> LlmConfig:
    return load_llm_config()


@pytest.fixture(scope="module")
def llm_client(llm_config: LlmConfig) -> LlmTextClient:
    settings = get_settings()
    if not settings.anthropic_api_key and not settings.claude_code_oauth_token:
        pytest.skip("LLM 인증(CLAUDE_CODE_OAUTH_TOKEN 또는 ANTHROPIC_API_KEY) 미설정 — 생략")
    return create_llm_client(llm_config, settings)


@pytest.fixture(scope="module")
def package(data_dir: Path) -> EvidencePackage:
    return build_financial_evidence(SK_HYNIX, as_of=_AS_OF, data_dir=data_dir)


def test_candidate_analysis_live_call_all_ids_real(
    package: EvidencePackage, llm_client: LlmTextClient, llm_config: LlmConfig
) -> None:
    """실데이터 Evidence로 CandidateAnalysis 실호출 — 검증 통과·evidence_id 전 실존·finding≥1."""
    analysis, metadata = generate_candidate_analysis(
        package,
        client=llm_client,
        prompts_dir=PROMPTS_DIR,
        max_attempts=llm_config.max_attempts,
    )

    allowed = {e.evidence_id for e in select_evidence_for_prompt(package)}
    finding_count = (
        len(analysis.financial_findings)
        + len(analysis.business_findings)
        + len(analysis.industry_findings)
        + len(analysis.catalyst_candidates)
        + len(analysis.risk_candidates)
    )
    assert finding_count >= 1
    for findings in (
        analysis.financial_findings,
        analysis.business_findings,
        analysis.industry_findings,
        analysis.catalyst_candidates,
        analysis.risk_candidates,
        analysis.conflicting_evidence,
    ):
        for finding in findings:
            assert set(finding.evidence_ids) <= allowed
    assert metadata.model.startswith(llm_config.model)

    # 후보 생성까지 end-to-end(호출 2회 — §2.4 허용): 저작 필드는 코드가 주입.
    candidates, cand_meta = generate_hypothesis_candidates(
        package,
        analysis,
        client=llm_client,
        prompts_dir=PROMPTS_DIR,
        max_attempts=llm_config.max_attempts,
    )
    assert 1 <= len(candidates) <= 5
    for candidate in candidates:
        assert candidate.prompt_version == "v1"
        assert candidate.generated_by == cand_meta.model
        assert set(candidate.evidence_ids) <= allowed
