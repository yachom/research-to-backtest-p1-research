"""정정공시 버전 관리·Point-in-Time View (README §15, 명세 B4).

A1이 이미 파싱한 ``DartFiling``(report_type·fiscal_period_end·is_correction·
correction_kind)을 ``(report_type, fiscal_period_end)`` 단위로 묶어 정정 체인
(:class:`FilingVersionGroup`)을 구성하고, 분석 기준일에 맞는 버전을 고르는 두
함수를 제공한다:

- :func:`visible_version` — Point-in-Time View. 백테스트는 반드시 이것을 쓴다.
- :func:`current_version` — Current View. 정정 유무와 무관하게 최신 버전.

README §4.1 원칙("분석 기준일 이후 정보는 쓰지 않는다")을 정정공시 차원에서
지키는 것이 이 모듈의 목적이다(§15.1 "각 접수번호를 독립된 버전으로 저장한다").
"""

import json
import logging
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.disclosure_search import find_periodic_filings
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dates import TradingCalendar, available_from
from research_backtest.core.exceptions import DataValidationError

logger = logging.getLogger("r2b.dart.filing_versions")

FILING_VERSIONS_FILENAME = "filing_versions.json"


class FilingVersion(BaseModel):
    """정정공시 체인의 버전 1건 (README §15.2 필드 그대로).

    ``original_rcept_no``·``supersedes_rcept_no``는 이 버전이 원본이면 모두
    None이다. 원본이 수집되지 않은 그룹(정정만 존재)이면 체인의 모든 버전이
    ``original_rcept_no=None``을 갖는다(§2 "원본 미수집" 케이스) —
    ``supersedes_rcept_no``로 직전 버전은 여전히 추적 가능하다.
    """

    rcept_no: str
    original_rcept_no: str | None
    filing_date: str  # rcept_dt ISO(YYYY-MM-DD) — README §15.2 필드명 그대로
    report_name: str
    revision_type: str | None  # correction_kind (예: "기재정정") — 원본이면 None
    is_latest_version: bool
    supersedes_rcept_no: str | None


class FilingVersionGroup(BaseModel):
    """동일 (report_type, fiscal_period_end)의 정정 버전 체인 (README §15.2 파생).

    ``versions``는 filing_date(→rcept_no) 오름차순 — 인덱스 0이 그룹 내 최초 접수.
    """

    report_type: PeriodicReportType
    fiscal_period_end: date
    versions: list[FilingVersion]


# --- 버전 그래프 구축 (명세 §2) ------------------------------------------------


def build_version_groups(filings: Sequence[DartFiling]) -> list[FilingVersionGroup]:
    """DartFiling 목록을 (report_type, fiscal_period_end) 단위 정정 체인으로 묶는다.

    - report_type 또는 fiscal_period_end가 None인 filing은 그룹화할 수 없어
      제외한다(제외 개수를 로그로 남긴다).
    - 그룹 내 정렬은 (rcept_dt, rcept_no) 오름차순 — 인덱스 0이 최초 접수.
      is_correction=False인 filing이 하나도 없으면 "원본 미수집" 케이스로
      전 버전의 original_rcept_no를 None으로 둔다(경고 로그로 표시).
    - is_correction=False가 2건 이상(중복 원본)이면 예외 대신 REQUIRES_REVIEW로
      경고 로그만 남기고 rcept_dt 순으로 체인을 이어간다(방어적 — 실데이터
      미관측 케이스, 명세 §2).
    - 반환 순서는 (fiscal_period_end, report_type) 오름차순으로 결정적이다.
    """
    excluded = 0
    grouped: dict[tuple[PeriodicReportType, date], list[DartFiling]] = {}
    for filing in filings:
        if filing.report_type is None or filing.fiscal_period_end is None:
            excluded += 1
            continue
        grouped.setdefault((filing.report_type, filing.fiscal_period_end), []).append(filing)
    if excluded:
        logger.info("정정공시 그룹화 제외: report_type/fiscal_period_end 없음 %d건", excluded)

    groups = [
        FilingVersionGroup(
            report_type=report_type,
            fiscal_period_end=fiscal_period_end,
            versions=_build_chain(report_type, fiscal_period_end, group_filings),
        )
        for (report_type, fiscal_period_end), group_filings in grouped.items()
    ]
    groups.sort(key=lambda g: (g.fiscal_period_end, g.report_type.value))
    return groups


def _build_chain(
    report_type: PeriodicReportType,
    fiscal_period_end: date,
    filings: Sequence[DartFiling],
) -> list[FilingVersion]:
    """단일 그룹의 filing들을 rcept_dt 오름차순 정정 체인으로 잇는다 (명세 §2)."""
    ordered = sorted(filings, key=lambda f: (f.rcept_dt, f.rcept_no))
    non_corrections = [f for f in ordered if not f.is_correction]

    if len(non_corrections) >= 2:
        logger.warning(
            "REQUIRES_REVIEW: %s %s 그룹에 원본(is_correction=False) 중복 %d건 — "
            "rcept_dt 순으로 체인 처리(방어적, 실데이터 미관측 케이스)",
            report_type,
            fiscal_period_end,
            len(non_corrections),
        )
    if not non_corrections:
        logger.warning(
            "원본 미수집: %s %s 그룹이 정정 공시만 보유(%d건) — 전 버전 original_rcept_no=None",
            report_type,
            fiscal_period_end,
            len(ordered),
        )

    original_rcept_no = ordered[0].rcept_no if non_corrections else None
    last_index = len(ordered) - 1
    versions: list[FilingVersion] = []
    for i, filing in enumerate(ordered):
        is_first = i == 0
        versions.append(
            FilingVersion(
                rcept_no=filing.rcept_no,
                original_rcept_no=None if is_first else original_rcept_no,
                filing_date=filing.rcept_dt.isoformat(),
                report_name=filing.report_nm,
                revision_type=filing.correction_kind,
                is_latest_version=(i == last_index),
                supersedes_rcept_no=None if is_first else ordered[i - 1].rcept_no,
            )
        )
    return versions


# --- Point-in-Time 선택 (명세 §3, README §15.3) --------------------------------


def visible_version(
    group: FilingVersionGroup, *, as_of_date: date, calendar: TradingCalendar
) -> FilingVersion | None:
    """분석 기준일에 이용 가능했던 최신 버전 (README §15.3 Point-in-Time View).

    이용 가능 판정은 ``available_from(접수일 다음 거래일, README §4.3) <=
    as_of_date``. 백테스트는 반드시 이 함수를 쓴다 — Current View(마지막 버전
    무조건)는 :func:`current_version`으로 별도 제공한다. as_of 이전에 이용
    가능해진 버전이 하나도 없으면 None.
    """
    candidates = [
        v
        for v in group.versions
        if available_from(date.fromisoformat(v.filing_date), calendar) <= as_of_date
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda v: (v.filing_date, v.rcept_no))


def current_version(group: FilingVersionGroup) -> FilingVersion:
    """Current View — 정정 유무와 무관하게 그룹의 최신 버전 (README §15.3).

    PIT가 아니므로 백테스트에는 쓰지 않는다 — :func:`visible_version`을 쓴다.
    """
    for v in group.versions:
        if v.is_latest_version:
            return v
    return max(group.versions, key=lambda v: (v.filing_date, v.rcept_no))


# --- 저장·로드 (명세 §4) --------------------------------------------------------


def filing_versions_path(data_dir: Path, corp_code: str) -> Path:
    """저장 경로 — ``{data_dir}/normalized/dart/{corp_code}/filing_versions.json``."""
    return data_dir / "normalized" / "dart" / corp_code / FILING_VERSIONS_FILENAME


def save_version_groups(
    corp_code: str, groups: Sequence[FilingVersionGroup], *, data_dir: Path
) -> Path:
    """FilingVersionGroup 목록을 JSON으로 저장한다 (명세 §4)."""
    path = filing_versions_path(data_dir, corp_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [group.model_dump(mode="json") for group in groups]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info(
        "정정공시 버전 그래프 저장 corp_code=%s groups=%d path=%s", corp_code, len(groups), path
    )
    return path


def load_version_groups(corp_code: str, *, data_dir: Path) -> list[FilingVersionGroup]:
    """저장된 filing_versions.json을 읽는다 (명세 §4). 파일이 없으면 DataValidationError."""
    path = filing_versions_path(data_dir, corp_code)
    if not path.exists():
        raise DataValidationError(f"정정공시 버전 그래프 파일이 없습니다: {path}")
    raw: Any = json.loads(path.read_text(encoding="utf-8"))
    return [FilingVersionGroup.model_validate(item) for item in raw]


# --- 빌드 오케스트레이션 (명세 §4) ----------------------------------------------


def build_and_save(
    corp_code: str,
    *,
    client: DartClient,
    data_dir: Path,
    as_of_date: date,
    lookback_years: int = 6,
) -> list[FilingVersionGroup]:
    """정기공시 조회 → 버전 그래프 구축 → 저장까지 한 번에 수행한다 (명세 §4).

    ``find_periodic_filings``는 이미 ``last_reprt_at="N"``으로 정정 전 원본까지
    포함해 조회한다(README §6.2, A1) — PIT 재현에 필요한 전제 조건.
    """
    filings = find_periodic_filings(
        client, corp_code, as_of_date=as_of_date, lookback_years=lookback_years
    )
    groups = build_version_groups(filings)
    save_version_groups(corp_code, groups, data_dir=data_dir)
    return groups
