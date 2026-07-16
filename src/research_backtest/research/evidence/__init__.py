"""Evidence Store — A4 재무 parquet → 결정적 재무 Evidence (명세 W3a-E1, README §18).

Point-in-Time(``available_from <= as_of``)를 강제해 기준일 이후 공시의 수치가
Evidence에 새지 않게 한다(절대 규칙 #1). Evidence는 전부 결정적 Python 계산이며
LLM·난수가 개입하지 않는다 — LLM은 이 Evidence를 소비할 뿐 재계산하지 않는다.

공개 API:

- :func:`build_financial_evidence` — (corp_code, as_of) → :class:`EvidencePackage`
- :class:`EvidencePackageStore` — 패키지·매니페스트 저장/로드(HITL 매니페스트 호환)
- :class:`FinancialEvidence`·:class:`EvidencePackage`·:class:`EvidenceCategory`
"""

from research_backtest.research.evidence.builder import build_financial_evidence
from research_backtest.research.evidence.models import (
    EvidenceCategory,
    EvidencePackage,
    FinancialEvidence,
)
from research_backtest.research.evidence.store import EvidencePackageStore

__all__ = [
    "EvidenceCategory",
    "EvidencePackage",
    "EvidencePackageStore",
    "FinancialEvidence",
    "build_financial_evidence",
]
