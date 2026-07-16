"""normalizer.py 단위 테스트 (명세 A4 §3~§4, §9)."""

from pathlib import Path
from typing import Any

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.financials.normalizer import (
    normalize_financials,
    parse_amount,
)
from research_backtest.core.financials.registry import load_registry

REGISTRY_PATH = Path(__file__).resolve().parents[3] / "configs" / "account_registry.yaml"


def _wrap(
    reprt: str, sj_div: str, account_id: str, account_nm: str, **fields: Any
) -> dict[str, Any]:
    row = {
        "rcept_no": "20240516000001",
        "reprt_code": reprt,
        "bsns_year": "2024",
        "corp_code": "00000000",
        "sj_div": sj_div,
        "sj_nm": sj_div,
        "account_id": account_id,
        "account_nm": account_nm,
        "account_detail": fields.pop("account_detail", "-"),
        **fields,
    }
    return {"bsns_year": "2024", "reprt_code": reprt, "fs_div": "CFS", "row": row}


@pytest.fixture
def registry() -> dict[str, Any]:
    return load_registry(REGISTRY_PATH)


# --- parse_amount (명세 §4, README §9.6) -------------------------------------


def test_parse_amount_empty_is_none_not_zero() -> None:
    assert parse_amount("") is None
    assert parse_amount(None) is None
    assert parse_amount("   ") is None


def test_parse_amount_zero_is_zero() -> None:
    # 빈 값과 0을 구분한다
    assert parse_amount("0") == 0


def test_parse_amount_commas_and_negative() -> None:
    assert parse_amount("1,234,567") == 1234567
    assert parse_amount("-2,886,000,000") == -2886000000


def test_parse_amount_invalid_raises() -> None:
    with pytest.raises(DataValidationError, match="파싱할 수 없"):
        parse_amount("N/A")


# --- 계정 매칭 (명세 §3) -----------------------------------------------------


def test_cis_income_matched_by_concept(registry: dict[str, Any]) -> None:
    rows = [_wrap("11013", "CIS", "ifrs-full_Revenue", "영업수익", thstrm_amount="12429598000000")]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    obs = result.get("revenue", "CFS", 2024, "11013")
    assert obs is not None
    assert obs.sj_div == "CIS"
    assert obs.thstrm_amount == 12429598000000
    assert result.matched_row_counts["revenue"] == 1


def test_label_only_match_nonstandard_id(registry: dict[str, Any]) -> None:
    # trade_receivables: concept 미일치, label '매출채권'으로 매칭
    rows = [
        _wrap(
            "11011",
            "BS",
            "ifrs-full_CurrentTradeReceivables",
            "매출채권",
            thstrm_amount="13019006000000",
        )
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    obs = result.get("trade_receivables", "CFS", 2024, "11011")
    assert obs is not None
    assert obs.source_account_id == "ifrs-full_CurrentTradeReceivables"
    assert obs.thstrm_amount == 13019006000000


def test_sce_profitloss_ignored(registry: dict[str, Any]) -> None:
    # SCE에 ProfitLoss가 있어도 sj_div 필터로 무시(net_income 미매칭)
    rows = [
        _wrap(
            "11011",
            "SCE",
            "ifrs-full_ProfitLoss",
            "당기순이익",
            account_detail="자본 [구성요소]",
            thstrm_amount="999",
        ),
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    assert result.get("net_income", "CFS", 2024, "11011") is None
    assert result.sce_skipped_count == 1
    assert result.matched_row_counts["net_income"] == 0


def test_empty_amount_becomes_none(registry: dict[str, Any]) -> None:
    rows = [
        _wrap("11011", "CIS", "ifrs-full_Revenue", "매출액", thstrm_amount="", thstrm_add_amount="")
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    obs = result.get("revenue", "CFS", 2024, "11011")
    assert obs is not None
    assert obs.thstrm_amount is None  # 0이 아니라 None


def test_unresolved_on_ambiguous_multiple_match(registry: dict[str, Any]) -> None:
    # 같은 canonical(inventories)에 두 행 매칭, 둘 다 account_detail != '-' → UNRESOLVED
    rows = [
        _wrap(
            "11011",
            "BS",
            "ifrs-full_Inventories",
            "재고자산",
            account_detail="제품",
            thstrm_amount="100",
        ),
        _wrap(
            "11011",
            "BS",
            "ifrs-full_Inventories",
            "재고자산",
            account_detail="원재료",
            thstrm_amount="200",
        ),
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    assert result.get("inventories", "CFS", 2024, "11011") is None  # 값 미확정
    assert len(result.unresolved) == 1
    assert result.unresolved[0].canonical_id == "inventories"


def test_account_detail_dash_preference_resolves(registry: dict[str, Any]) -> None:
    # 복수 매칭이라도 account_detail=='-'인 행이 하나면 그 행을 채택
    rows = [
        _wrap(
            "11011",
            "BS",
            "ifrs-full_Inventories",
            "재고자산",
            account_detail="-",
            thstrm_amount="300",
        ),
        _wrap(
            "11011",
            "BS",
            "ifrs-full_Inventories",
            "재고자산",
            account_detail="원재료",
            thstrm_amount="200",
        ),
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    obs = result.get("inventories", "CFS", 2024, "11011")
    assert obs is not None and obs.thstrm_amount == 300
    assert not result.unresolved


def test_unmatched_rows_counted(registry: dict[str, Any]) -> None:
    rows = [
        _wrap("11011", "BS", "ifrs-full_OtherAsset", "기타자산", thstrm_amount="1"),  # 미매칭
        _wrap("11011", "BS", "ifrs-full_Assets", "자산총계", thstrm_amount="1000"),  # 매칭
    ]
    result = normalize_financials(rows, registry, scopes=["CFS"])
    assert result.unmatched_row_count == 1
    assert result.processed_row_count == 2


def test_scope_filter_excludes_other_scope(registry: dict[str, Any]) -> None:
    rows = [_wrap("11011", "BS", "ifrs-full_Assets", "자산총계", thstrm_amount="1000")]
    result = normalize_financials(rows, registry, scopes=["OFS"])  # CFS 래퍼인데 OFS만 요청
    assert not result.observations
