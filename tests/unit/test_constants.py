"""README §6.2, §27 상수 정의 테스트."""

from research_backtest.core.constants import (
    DART_STATUS_MESSAGES,
    FATAL_DART_CODES,
    NO_DATA_DART_CODES,
    RETRYABLE_DART_CODES,
    ReprtCode,
)


def test_report_codes_match_dart_spec() -> None:
    # README §6.2 보고서 코드표
    assert ReprtCode.Q1.value == "11013"
    assert ReprtCode.HALF.value == "11012"
    assert ReprtCode.Q3.value == "11014"
    assert ReprtCode.ANNUAL.value == "11011"


def test_status_code_sets_are_disjoint() -> None:
    assert not RETRYABLE_DART_CODES & FATAL_DART_CODES
    assert not RETRYABLE_DART_CODES & NO_DATA_DART_CODES
    assert not FATAL_DART_CODES & NO_DATA_DART_CODES


def test_all_classified_codes_have_messages() -> None:
    classified = RETRYABLE_DART_CODES | FATAL_DART_CODES | NO_DATA_DART_CODES
    assert classified <= DART_STATUS_MESSAGES.keys()
