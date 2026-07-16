"""configs/ YAML 파일 정합성 스모크 테스트."""

from datetime import date
from pathlib import Path

import yaml

from research_backtest.core.config import load_dart_config, load_market_config

CONFIG_DIR = Path(__file__).resolve().parents[2] / "configs"

VALID_STATEMENT_TYPES = {"BS", "IS", "CIS", "CF", "SCE"}
VALID_PERIOD_TYPES = {"instant", "duration"}


def test_account_registry_entries_have_required_keys() -> None:
    # A4 §2: statement_type(단일) → statement_types(복수)로 스키마 개정
    registry = yaml.safe_load((CONFIG_DIR / "account_registry.yaml").read_text(encoding="utf-8"))
    assert registry, "레지스트리가 비어 있다"
    required = {"korean_name", "statement_types", "period_type", "accepted_labels"}
    for canonical_id, entry in registry.items():
        missing = required - entry.keys()
        assert not missing, f"{canonical_id}에 필수 키 누락: {missing}"
        statement_types = entry["statement_types"]
        assert isinstance(statement_types, list) and statement_types, canonical_id
        assert all(sj in VALID_STATEMENT_TYPES for sj in statement_types), canonical_id
        assert entry["period_type"] in VALID_PERIOD_TYPES, canonical_id
        assert entry["accepted_labels"], canonical_id


def test_income_accounts_allow_is_and_cis() -> None:
    # DATA_NOTES A2-①: SK하이닉스는 손익이 전부 CIS → 손익 계정은 IS·CIS 모두 허용
    registry = yaml.safe_load((CONFIG_DIR / "account_registry.yaml").read_text(encoding="utf-8"))
    for canonical_id in ("revenue", "operating_income", "net_income"):
        assert set(registry[canonical_id]["statement_types"]) == {"IS", "CIS"}, canonical_id


def test_account_registry_covers_milestone5_targets() -> None:
    # README §31 Milestone 5 완료 조건의 11개 계정
    registry = yaml.safe_load((CONFIG_DIR / "account_registry.yaml").read_text(encoding="utf-8"))
    expected = {
        "revenue",
        "operating_income",
        "net_income",
        "total_assets",
        "total_liabilities",
        "total_equity",
        "cash_and_cash_equivalents",
        "operating_cash_flow",
        "purchase_of_ppe",
        "inventories",
        "trade_receivables",
    }
    assert expected <= registry.keys()


def test_other_configs_parse_as_mappings() -> None:
    for name in ["dart.yaml", "backtest.yaml", "market.yaml"]:
        data = yaml.safe_load((CONFIG_DIR / name).read_text(encoding="utf-8"))
        assert isinstance(data, dict), name


def test_dart_yaml_loads_into_dart_config_with_min_interval() -> None:
    # request.min_interval_seconds는 A2에서 신설 (명세 A2 §4)
    config = load_dart_config(CONFIG_DIR / "dart.yaml")
    assert config.min_interval_seconds == 0.1
    assert config.timeout_seconds == 30.0


def test_market_yaml_loads_into_market_config() -> None:
    # configs/market.yaml은 A3에서 신설 (명세 A3 §5)
    config = load_market_config(CONFIG_DIR / "market.yaml")
    assert config.source == "pykrx"
    assert config.min_interval_seconds == 0.3
    assert config.default_start_date == date(2015, 1, 1)
    assert config.default_index_code == "1001"
