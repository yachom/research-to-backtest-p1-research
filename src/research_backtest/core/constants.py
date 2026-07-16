"""DART API 및 재무제표 관련 상수 (README §6, §27)."""

from enum import StrEnum


class ReprtCode(StrEnum):
    """정기보고서 코드 (README §6.2)."""

    Q1 = "11013"
    HALF = "11012"
    Q3 = "11014"
    ANNUAL = "11011"


class PeriodicReportType(StrEnum):
    """정기보고서 유형 (README §6.2 정기보고서 필터).

    분기보고서는 회계기간 말월로 Q1/Q3를 구분한다(12월 결산 가정,
    core.dart.disclosure_search 참고).
    """

    ANNUAL = "ANNUAL"
    HALF = "HALF"
    Q1 = "Q1"
    Q3 = "Q3"


# 정기보고서 유형 ↔ reprt_code 매핑 (README §6.2 보고서 코드표)
PERIODIC_REPORT_TO_REPRT_CODE: dict[PeriodicReportType, ReprtCode] = {
    PeriodicReportType.ANNUAL: ReprtCode.ANNUAL,
    PeriodicReportType.HALF: ReprtCode.HALF,
    PeriodicReportType.Q1: ReprtCode.Q1,
    PeriodicReportType.Q3: ReprtCode.Q3,
}


class FsDiv(StrEnum):
    """재무제표 구분: 연결(CFS) / 별도(OFS) (README §7)."""

    CFS = "CFS"
    OFS = "OFS"


class StatementType(StrEnum):
    """재무제표 종류 sj_div (README §6.4)."""

    BS = "BS"
    IS = "IS"
    CIS = "CIS"
    CF = "CF"
    SCE = "SCE"


DART_BASE_URL = "https://opendart.fss.or.kr/api"

# DART 응답 상태 코드 (README §27.1)
DART_STATUS_MESSAGES: dict[str, str] = {
    "000": "정상",
    "010": "등록되지 않은 키",
    "011": "사용할 수 없는 키",
    "012": "접근할 수 없는 IP",
    "013": "조회 데이터 없음",
    "014": "파일 없음",
    "020": "요청 제한 초과",
    "021": "조회 회사 수 초과",
    "100": "부적절한 필드",
    "101": "부적절한 접근",
    "800": "시스템 점검",
    "900": "정의되지 않은 오류",
    "901": "개인정보 보유기간 만료 키",
}

# 재시도 가능 상태 코드 (README §27.2)
RETRYABLE_DART_CODES: frozenset[str] = frozenset({"020", "800", "900"})

# 즉시 실패 상태 코드 (README §27.2)
FATAL_DART_CODES: frozenset[str] = frozenset({"010", "011", "012", "100", "101", "901"})

# 조회 결과 없음 계열 — 오류가 아닌 빈 결과로 처리
NO_DATA_DART_CODES: frozenset[str] = frozenset({"013", "014"})
