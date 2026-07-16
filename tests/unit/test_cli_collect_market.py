"""collect-market CLI 단위 테스트 — 수집기·DART 계층 전부 mock (명세 A3 §6~7)."""

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from typer.testing import CliRunner

from research_backtest.app import cli
from research_backtest.core.config import DartConfig, MarketConfig, Settings
from research_backtest.core.dart.corp_code import CorpCodeRegistry
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.market.collector import DatasetOutcome, MarketCollectionSummary
from research_backtest.core.models import DartCorporation

runner = CliRunner()

KST = ZoneInfo("Asia/Seoul")

SK_HYNIX = DartCorporation(
    corp_code="00164779",
    corp_name="SK하이닉스",
    corp_eng_name="SK hynix Inc.",
    stock_code="000660",
    modify_date="20250102",
)
UNLISTED = DartCorporation(
    corp_code="99999999",
    corp_name="비상장테스트",
    corp_eng_name=None,
    stock_code=None,
    modify_date="20250102",
)

FULL_OUTCOMES = [
    DatasetOutcome(
        dataset="OHLCV",
        result="FETCHED",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
    DatasetOutcome(
        dataset="INVESTOR_VALUE",
        result="FETCHED",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
    DatasetOutcome(
        dataset="INDEX",
        result="CACHED",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
    DatasetOutcome(
        dataset="CALENDAR",
        result="BUILT",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
    DatasetOutcome(
        dataset="DAILY_MERGED",
        result="BUILT",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
]

PARTIAL_OUTCOMES = [
    DatasetOutcome(
        dataset="OHLCV",
        result="FETCHED",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
    DatasetOutcome(dataset="INVESTOR_VALUE", result="SKIPPED_NO_AUTH"),
    DatasetOutcome(dataset="INDEX", result="SKIPPED_NO_AUTH"),
    DatasetOutcome(dataset="CALENDAR", result="SKIPPED_NO_AUTH"),
    DatasetOutcome(
        dataset="DAILY_MERGED",
        result="BUILT",
        row_count=2803,
        date_min=date(2015, 1, 2),
        date_max=date(2026, 7, 13),
    ),
]


def _make_settings(tmp_path: Path, *, dart_api_key: str = "") -> Settings:
    return Settings(_env_file=None, dart_api_key=dart_api_key, data_dir=tmp_path / "data")


@pytest.fixture
def collect_calls() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def patch_market(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, collect_calls: list[dict[str, Any]]
) -> Any:
    """설정·수집기 계층을 오프라인 mock으로 대체하는 팩토리 (A2 CLI 테스트 패턴)."""

    def _patch(
        *,
        outcomes: list[DatasetOutcome] | None = None,
        settings: Settings | None = None,
        raise_error: Exception | None = None,
    ) -> Settings:
        active_settings = settings if settings is not None else _make_settings(tmp_path)
        active_outcomes = outcomes if outcomes is not None else FULL_OUTCOMES
        monkeypatch.setattr(cli, "get_settings", lambda: active_settings)
        monkeypatch.setattr(cli, "load_market_config", lambda: MarketConfig())

        def fake_collect(
            source: Any,
            *,
            stock_code: str,
            index_code: str,
            from_date: date,
            to_date: date,
            data_dir: Path,
            force: bool = False,
            min_interval_seconds: float = 0.3,
        ) -> MarketCollectionSummary:
            collect_calls.append(
                {
                    "stock_code": stock_code,
                    "index_code": index_code,
                    "from_date": from_date,
                    "to_date": to_date,
                    "data_dir": data_dir,
                    "force": force,
                    "min_interval_seconds": min_interval_seconds,
                }
            )
            if raise_error is not None:
                raise raise_error
            return MarketCollectionSummary(
                stock_code=stock_code, index_code=index_code, outcomes=active_outcomes
            )

        monkeypatch.setattr(cli, "run_collect_market_data", fake_collect)
        return active_settings

    return _patch


def _patch_dart_layers(
    monkeypatch: pytest.MonkeyPatch, corporations: list[DartCorporation]
) -> None:
    monkeypatch.setattr(cli, "load_dart_config", lambda: DartConfig())
    registry = CorpCodeRegistry(corporations)

    def fake_load_registry(
        client: Any,
        cache_dir: Path,
        *,
        refresh_days: int,
        force: bool = False,
        now: datetime | None = None,
    ) -> CorpCodeRegistry:
        return registry

    monkeypatch.setattr(cli, "load_corp_code_registry", fake_load_registry)


# --- 상호배타 옵션 (명세 §6) ---------------------------------------------------


def test_both_company_and_stock_code_rejected(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(
        cli.app, ["collect-market", "--company", "SK하이닉스", "--stock-code", "000660"]
    )
    assert result.exit_code == 2  # typer.BadParameter → usage error
    assert collect_calls == []


def test_neither_company_nor_stock_code_rejected(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(cli.app, ["collect-market"])
    assert result.exit_code == 2
    assert collect_calls == []


# --- --stock-code 경로 (DART 없이 동작, 명세 §6) --------------------------------


def test_stock_code_partial_mode_warns_and_exits_0(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    # dart_api_key가 비어 있어도 --stock-code는 동작해야 한다
    patch_market(outcomes=PARTIAL_OUTCOMES)
    result = runner.invoke(cli.app, ["collect-market", "--stock-code", "000660"])
    assert result.exit_code == 0, result.output
    assert "SKIPPED_NO_AUTH" in result.output
    assert "KRX_ID/KRX_PW" in result.output  # 노란 안내 경고 (명세 §6)
    assert len(collect_calls) == 1
    assert collect_calls[0]["stock_code"] == "000660"


def test_stock_code_defaults_from_config_and_kst_yesterday(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(cli.app, ["collect-market", "--stock-code", "000660"])
    assert result.exit_code == 0, result.output
    call = collect_calls[0]
    assert call["from_date"] == MarketConfig().default_start_date  # 2015-01-01
    assert call["to_date"] == datetime.now(KST).date() - timedelta(days=1)  # KST 어제
    assert call["index_code"] == MarketConfig().default_index_code  # 1001
    assert call["force"] is False
    assert call["min_interval_seconds"] == MarketConfig().min_interval_seconds


def test_full_mode_has_no_auth_warning(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market(outcomes=FULL_OUTCOMES)
    result = runner.invoke(cli.app, ["collect-market", "--stock-code", "000660"])
    assert result.exit_code == 0, result.output
    assert "KRX 로그인 필요" not in result.output


def test_explicit_options_are_passed_through(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(
        cli.app,
        [
            "collect-market",
            "--stock-code",
            "000660",
            "--from-date",
            "2024-01-02",
            "--to-date",
            "2024-01-31",
            "--index",
            "2001",
            "--force-download",
        ],
    )
    assert result.exit_code == 0, result.output
    call = collect_calls[0]
    assert call["from_date"] == date(2024, 1, 2)
    assert call["to_date"] == date(2024, 1, 31)
    assert call["index_code"] == "2001"
    assert call["force"] is True


def test_invalid_stock_code_rejected(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(cli.app, ["collect-market", "--stock-code", "66"])
    assert result.exit_code == 2
    assert collect_calls == []


def test_bad_date_format_rejected(patch_market: Any, collect_calls: list[dict[str, Any]]) -> None:
    patch_market()
    result = runner.invoke(
        cli.app, ["collect-market", "--stock-code", "000660", "--from-date", "2024/01/02"]
    )
    assert result.exit_code == 2
    assert collect_calls == []


def test_from_date_after_to_date_rejected(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()
    result = runner.invoke(
        cli.app,
        [
            "collect-market",
            "--stock-code",
            "000660",
            "--from-date",
            "2024-02-01",
            "--to-date",
            "2024-01-01",
        ],
    )
    assert result.exit_code == 2
    assert collect_calls == []


# --- --company 경로 (DART resolve 재사용, 명세 §6) -------------------------------


def test_company_resolves_stock_code_via_dart(
    patch_market: Any,
    collect_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patch_market(settings=_make_settings(tmp_path, dart_api_key="unit-test-key"))
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    result = runner.invoke(cli.app, ["collect-market", "--company", "SK하이닉스"])
    assert result.exit_code == 0, result.output
    assert collect_calls[0]["stock_code"] == "000660"
    assert "SK하이닉스" in result.output


def test_company_without_dart_key_exits_3(
    patch_market: Any, collect_calls: list[dict[str, Any]]
) -> None:
    patch_market()  # dart_api_key=""
    result = runner.invoke(cli.app, ["collect-market", "--company", "SK하이닉스"])
    assert result.exit_code == 3
    assert "DART_API_KEY" in result.output
    assert collect_calls == []


def test_unlisted_company_exits_1(
    patch_market: Any,
    collect_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patch_market(settings=_make_settings(tmp_path, dart_api_key="unit-test-key"))
    _patch_dart_layers(monkeypatch, [UNLISTED])
    result = runner.invoke(cli.app, ["collect-market", "--company", "비상장테스트"])
    assert result.exit_code == 1
    assert "비상장" in result.output
    assert collect_calls == []


def test_company_not_found_exits_1(
    patch_market: Any,
    collect_calls: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    patch_market(settings=_make_settings(tmp_path, dart_api_key="unit-test-key"))
    _patch_dart_layers(monkeypatch, [SK_HYNIX])
    result = runner.invoke(cli.app, ["collect-market", "--company", "없는회사이름"])
    assert result.exit_code == 1
    assert "NOT_FOUND" in result.output
    assert collect_calls == []


# --- 오류 경로 (명세 §6 종료 코드) ----------------------------------------------


def test_validation_error_exits_1(patch_market: Any) -> None:
    patch_market(raise_error=DataValidationError("OHLCV에 high < low 행이 1개 있습니다"))
    result = runner.invoke(cli.app, ["collect-market", "--stock-code", "000660"])
    assert result.exit_code == 1
    assert "수집 실패" in result.output
