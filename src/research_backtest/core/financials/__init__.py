"""핵심 계정 정규화·재무 시계열 (Milestone A4, README §11~§13·§16~§17·§22).

전체 재무제표 API 수집분(A2)을 표준계정으로 정규화하고 단독분기 역산·지표·
available_from을 부여해 A6 백테스트가 as-of join할 4개 parquet을 발행한다.

공개 진입점: :func:`build_financial_datasets`.
"""

from research_backtest.core.financials.metrics import Metric, compute_metrics
from research_backtest.core.financials.normalizer import (
    NormalizationResult,
    normalize_financials,
    parse_amount,
)
from research_backtest.core.financials.pipeline import (
    FinancialBuildReport,
    build_financial_datasets,
    financials_out_dir,
)
from research_backtest.core.financials.quarterly import (
    DERIVED_QUARTER,
    REPORTED,
    Fact,
    apply_available_from,
    derive_facts,
)
from research_backtest.core.financials.registry import (
    CanonicalAccount,
    load_registry,
    normalize_concept,
    normalize_label,
)

__all__ = [
    "DERIVED_QUARTER",
    "REPORTED",
    "CanonicalAccount",
    "Fact",
    "FinancialBuildReport",
    "Metric",
    "NormalizationResult",
    "apply_available_from",
    "build_financial_datasets",
    "compute_metrics",
    "derive_facts",
    "financials_out_dir",
    "load_registry",
    "normalize_concept",
    "normalize_financials",
    "normalize_label",
    "parse_amount",
]
