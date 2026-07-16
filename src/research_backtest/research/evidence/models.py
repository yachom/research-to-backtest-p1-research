"""Evidence 모델·분류 상수 (README §18.2, 명세 W3a-E1 §3.2·§3.4).

:class:`FinancialEvidence`는 README §18.2의 모델을 그대로 따르되, 구현에 필요한
두 필드(``fs_scope``·``available_from``)를 보강한다 — 전자는 MVP가 CFS 기준임을
명시하고, 후자는 **Point-in-Time 검증 근거를 evidence 안에 보존**하기 위함이다
(절대 규칙 #1: 재무값은 ``available_from`` 이후에만 사용). Evidence는 전부 결정적
Python 계산의 산물이며 LLM·난수가 개입하지 않는다(content_origin 성격:
PYTHON_CALCULATION, README §18.1).

이 모듈은 **표시·분류 메타데이터만** 자체 상수로 보유한다. 계정 매칭은 이미
core(A4)가 수행해 wide parquet의 컬럼을 canonical_id로 확정했으므로, E1은 그
컬럼을 소비할 뿐 :mod:`core.financials.registry`를 다시 로드해 재매칭하지 않는다
(경계: E1은 registry의 *매칭* 규칙을 재구현하지 않는다). 표시용 한국어명은
account_registry.yaml의 korean_name과 정렬돼 있다.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class EvidenceCategory(StrEnum):
    """Evidence 분류 (명세 §3.4 — README는 예시 "PROFITABILITY"만 제공)."""

    GROWTH = "GROWTH"  # 전년 동기 대비 성장률(A4 YoY 지표)
    PROFITABILITY = "PROFITABILITY"  # 영업이익률·이익 수준·흑자/적자 전환
    STABILITY = "STABILITY"  # 부채·자본·현금 잔액(재무구조)
    CASH_FLOW = "CASH_FLOW"  # 영업·투자 현금흐름
    SCALE = "SCALE"  # 매출·자산·재고·매출채권 규모 추세


# --- 계정·지표 → 표시 메타데이터 (account_registry.yaml 11계정과 정렬) -----------

# canonical account → evidence_id 코드(짧은 대문자 토큰)
ACCOUNT_CODE: dict[str, str] = {
    "revenue": "REVENUE",
    "operating_income": "OP_INCOME",
    "net_income": "NET_INCOME",
    "total_assets": "ASSETS",
    "total_liabilities": "LIABILITIES",
    "total_equity": "EQUITY",
    "cash_and_cash_equivalents": "CASH",
    "operating_cash_flow": "OCF",
    "purchase_of_ppe": "CAPEX",
    "inventories": "INVENTORY",
    "trade_receivables": "RECEIVABLES",
}

# canonical account → 한국어 표시명(statement 템플릿용, registry korean_name 정렬)
ACCOUNT_DISPLAY: dict[str, str] = {
    "revenue": "매출",
    "operating_income": "영업이익",
    "net_income": "당기순이익",
    "total_assets": "자산총계",
    "total_liabilities": "부채총계",
    "total_equity": "자본총계",
    "cash_and_cash_equivalents": "현금및현금성자산",
    "operating_cash_flow": "영업활동현금흐름",
    "purchase_of_ppe": "유형자산 취득",
    "inventories": "재고자산",
    "trade_receivables": "매출채권",
}

# canonical account → 분류(§3.4). 성장률(YoY)은 지표 evidence에서 GROWTH로 분류하고,
# 계정 evidence는 계정의 경제적 성격으로 분류한다(예: 매출 규모=SCALE, 이익=PROFITABILITY).
ACCOUNT_CATEGORY: dict[str, EvidenceCategory] = {
    "revenue": EvidenceCategory.SCALE,
    "operating_income": EvidenceCategory.PROFITABILITY,
    "net_income": EvidenceCategory.PROFITABILITY,
    "total_assets": EvidenceCategory.SCALE,
    "total_liabilities": EvidenceCategory.STABILITY,
    "total_equity": EvidenceCategory.STABILITY,
    "cash_and_cash_equivalents": EvidenceCategory.STABILITY,
    "operating_cash_flow": EvidenceCategory.CASH_FLOW,
    "purchase_of_ppe": EvidenceCategory.CASH_FLOW,
    "inventories": EvidenceCategory.SCALE,
    "trade_receivables": EvidenceCategory.SCALE,
}

# 흑자↔적자 전환(부호 전환)을 탐지할 계정 — 음수를 취할 수 있는 손익·현금흐름 계정.
# 매출·재무상태표 잔액은 부호가 뒤집히지 않으므로 제외한다.
SIGNABLE_ACCOUNTS: frozenset[str] = frozenset(
    {"operating_income", "net_income", "operating_cash_flow"}
)

# A4 지표(financial_metrics.parquet) → evidence_id 코드
METRIC_CODE: dict[str, str] = {
    "revenue_yoy": "REVENUE_YOY",
    "operating_income_yoy": "OP_INCOME_YOY",
    "net_income_yoy": "NET_INCOME_YOY",
    "operating_margin": "OP_MARGIN",
}

# A4 지표 → 분류(§3.4). *_yoy는 성장률이므로 GROWTH, operating_margin은 PROFITABILITY.
METRIC_CATEGORY: dict[str, EvidenceCategory] = {
    "revenue_yoy": EvidenceCategory.GROWTH,
    "operating_income_yoy": EvidenceCategory.GROWTH,
    "net_income_yoy": EvidenceCategory.GROWTH,
    "operating_margin": EvidenceCategory.PROFITABILITY,
}

# A4 지표 → 한국어 표시명(statement 템플릿용)
METRIC_DISPLAY: dict[str, str] = {
    "revenue_yoy": "매출",
    "operating_income_yoy": "영업이익",
    "net_income_yoy": "당기순이익",
    "operating_margin": "영업이익률",
}


class FinancialEvidence(BaseModel):
    """재무 Evidence 1건 (README §18.2 + 구현 보강 2필드).

    evidence_id 규약(결정적, 패키지 내 유일):

    - 지표 evidence: ``FIN_{METRIC_CODE}_{PERIOD}`` (예: ``FIN_OP_MARGIN_2025Q3``)
    - 계정 evidence: ``FIN_{ACCOUNT_CODE}_{VARIANT}_{PERIOD}``
      (VARIANT ∈ {YOY, TURN, TREND}, 예: ``FIN_OP_INCOME_TURN_2024Q1``)

    PERIOD 규약: 분기는 ``{연도}Q{분기}``, 연간은 ``FY{연도}``.

    ``source_fact_ids`` 규약(명세 §3.3 — normalized_facts에 fact_id 컬럼이 없어
    결정적으로 생성): ``FACT_{account_id|metric_id}_{fs_scope}_{연도}Q{분기|A}``.
    비교 대상이 다른 행이면 두 행의 fact_id를 모두 기록한다.
    """

    model_config = ConfigDict(extra="forbid")

    evidence_id: str
    category: str
    statement: str  # 한국어 서술 1문장 — Python 템플릿 생성(LLM 아님)
    current_value: Decimal | None
    comparison_value: Decimal | None
    change_rate: float | None
    period: str
    comparison_period: str | None
    source_fact_ids: list[str]
    rcept_no: str
    filing_date: str  # rcept_dt ISO
    significance_score: float  # [0, 1]

    # --- 구현 보강 (README 모델에 없음) ---
    fs_scope: str  # "CFS"(MVP 기본) — 명세 §3.3
    available_from: str  # PIT 검증 근거 보존 (ISO)


class EvidencePackage(BaseModel):
    """한 (기업, 기준일, lookback) 조합의 Evidence 묶음 — 생성 파라미터의 재현성 보존.

    ``as_of_date``·``lookback_years``·``fs_scope``는 필터 파라미터이며, 같은 입력과
    같은 소스 parquet에 대해 evidence 리스트가 결정적으로 재현된다(``generated_at``
    타임스탬프만 실행 시각에 따라 달라진다).
    """

    model_config = ConfigDict(extra="forbid")

    corp_code: str
    as_of_date: str  # ISO
    lookback_years: int
    fs_scope: str
    generated_at: str  # ISO (KST)
    evidence: list[FinancialEvidence]


__all__ = [
    "ACCOUNT_CATEGORY",
    "ACCOUNT_CODE",
    "ACCOUNT_DISPLAY",
    "METRIC_CATEGORY",
    "METRIC_CODE",
    "METRIC_DISPLAY",
    "SIGNABLE_ACCOUNTS",
    "EvidenceCategory",
    "EvidencePackage",
    "FinancialEvidence",
]
