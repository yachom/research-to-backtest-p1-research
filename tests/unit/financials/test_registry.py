"""registry.py 단위 테스트 (명세 A4 §2·§3, §9)."""

from pathlib import Path

import pytest

from research_backtest.core.exceptions import ConfigError
from research_backtest.core.financials.registry import (
    CanonicalAccount,
    load_registry,
    normalize_concept,
    normalize_label,
)

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "configs" / "account_registry.yaml"


def test_load_registry_new_schema() -> None:
    registry = load_registry(REGISTRY_PATH)
    assert set(registry) >= {"revenue", "operating_income", "net_income", "total_assets"}
    revenue = registry["revenue"]
    assert isinstance(revenue, CanonicalAccount)
    assert revenue.statement_types == ["IS", "CIS"]  # 손익은 IS·CIS 모두 허용
    assert revenue.period_type == "duration"


def test_registry_statement_types_all_valid() -> None:
    registry = load_registry(REGISTRY_PATH)
    for account in registry.values():
        assert account.statement_types
        assert all(sj in {"BS", "IS", "CIS", "CF", "SCE"} for sj in account.statement_types)


def test_empty_statement_types_rejected() -> None:
    with pytest.raises(ValueError, match="statement_types가 비어"):
        CanonicalAccount(
            canonical_id="x", korean_name="x", statement_types=[], period_type="duration"
        )


def test_invalid_statement_type_rejected() -> None:
    with pytest.raises(ValueError, match="허용되지 않은 sj_div"):
        CanonicalAccount(
            canonical_id="x", korean_name="x", statement_types=["ZZ"], period_type="duration"
        )


def test_normalize_concept_underscore_to_colon() -> None:
    # API account_id(언더스코어) → registry concept(콜론) 정규화
    assert normalize_concept("ifrs-full_Revenue") == "ifrs-full:Revenue"
    assert normalize_concept("dart_OperatingIncomeLoss") == "dart:OperatingIncomeLoss"
    # 하이픈은 보존, 이미 콜론이면 불변(멱등)
    assert normalize_concept("ifrs-full:Revenue") == "ifrs-full:Revenue"


def test_normalize_label_strips_all_whitespace() -> None:
    assert normalize_label("영업활동 현금흐름") == "영업활동현금흐름"
    assert normalize_label("매출채권 및 기타유동채권") == "매출채권및기타유동채권"
    assert normalize_label("영업활동　현금흐름") == "영업활동현금흐름"  # 전각 공백


def test_matches_by_concept_underscore_form() -> None:
    account = CanonicalAccount(
        canonical_id="revenue",
        korean_name="매출액",
        statement_types=["IS", "CIS"],
        period_type="duration",
        accepted_concepts=["ifrs-full:Revenue"],
        accepted_labels=["매출액"],
    )
    # CIS에서 언더스코어 concept으로 매칭 (label이 '영업수익'이어도 concept로 매칭)
    assert account.matches("CIS", "ifrs-full_Revenue", "영업수익")


def test_matches_rejects_wrong_sj_div() -> None:
    account = CanonicalAccount(
        canonical_id="net_income",
        korean_name="당기순이익",
        statement_types=["IS", "CIS"],
        period_type="duration",
        accepted_concepts=["ifrs-full:ProfitLoss"],
        accepted_labels=["당기순이익"],
    )
    # SCE에 ProfitLoss가 있어도 sj_div 필터로 불일치 (DATA_NOTES A2-③)
    assert not account.matches("SCE", "ifrs-full_ProfitLoss", "당기순이익")


def test_matches_label_only_for_nonstandard_id() -> None:
    account = CanonicalAccount(
        canonical_id="trade_receivables",
        korean_name="매출채권",
        statement_types=["BS"],
        period_type="instant",
        accepted_concepts=["ifrs-full:TradeAndOtherCurrentReceivables"],
        accepted_labels=["매출채권"],
    )
    # concept 미일치(CurrentTradeReceivables) → label로 매칭
    assert account.matches("BS", "ifrs-full_CurrentTradeReceivables", "매출채권")


def test_matches_non_standard_marker_is_concept_mismatch() -> None:
    account = CanonicalAccount(
        canonical_id="purchase_of_ppe",
        korean_name="유형자산의 취득",
        statement_types=["CF"],
        period_type="duration",
        accepted_concepts=[
            "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"
        ],
        accepted_labels=["유형자산의취득"],
    )
    # "-표준계정코드 미사용-"은 concept 불일치 → label('유형자산의취득')로만 매칭
    assert account.matches("CF", "-표준계정코드 미사용-", "유형자산의 취득")
    assert not account.matches("CF", "-표준계정코드 미사용-", "무관한계정명")


def test_flow_kind_classification() -> None:
    registry = load_registry(REGISTRY_PATH)
    assert registry["revenue"].is_period_flow()
    assert not registry["revenue"].is_cumulative_flow()
    assert registry["operating_cash_flow"].is_cumulative_flow()
    assert not registry["operating_cash_flow"].is_period_flow()
    assert registry["total_assets"].period_type == "instant"


def test_load_registry_missing_file() -> None:
    with pytest.raises(ConfigError, match="registry 파일이 없습니다"):
        load_registry(Path("/nonexistent/registry.yaml"))
