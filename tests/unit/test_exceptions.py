"""README §27 예외 분류 테스트."""

from research_backtest.core.exceptions import DartApiError


def test_rate_limit_error_is_retryable() -> None:
    assert DartApiError("020").retryable


def test_invalid_key_error_is_not_retryable() -> None:
    assert not DartApiError("010").retryable


def test_no_data_code_is_flagged() -> None:
    err = DartApiError("013")
    assert err.is_no_data
    assert "조회 데이터 없음" in str(err)


def test_unknown_code_gets_fallback_message() -> None:
    err = DartApiError("999")
    assert not err.retryable
    assert "999" in str(err)
