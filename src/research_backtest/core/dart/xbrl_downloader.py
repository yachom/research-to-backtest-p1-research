"""XBRL 재무제표 원본파일 수집·보존 (README §6.5, §8, §19.4, Milestone B1).

``GET /api/fnlttXbrl.xml``(rcept_no·reprt_code → ZIP binary)를 호출해 원본을
무수정 보존한다. :meth:`DartClient.get_bytes`가 ZIP magic을 검증해 오류
XML/JSON을 ZIP으로 오인하지 않으므로(README §19.4), 이 모듈은 성공 응답의
무결성 검증·안전한 압축 해제·멱등 캐시에 집중한다.

저장 레이아웃 (README §8.1) — ``{data_dir}/raw/dart/xbrl/{corp_code}/{rcept_no}/``:

- ``response.zip``   : API 응답 ZIP 원본(무수정)
- ``manifest.json``  : README §8.2 메타 — **커밋 마커(마지막에 기록)**
- ``extracted/``     : 압축 해제(ZIP 내부 파일명 고정 가정 금지, zip-slip 방어)
- ``checksum.sha256``: response.zip의 sha256 한 줄

멱등성 (README §8.3): manifest.json 존재 ∧ checksum·response.zip sha256 일치면
재다운로드하지 않는다(``force=True``로 무시). 013(조회 데이터 없음)·014(파일
없음)은 zip 없이 manifest에 ``status``만 기록해 negative cache로 쓴다 — 미제출
보고서를 매 실행마다 재조회하지 않는다(financial_api와 동일 원칙).

쓰기 실패(손상 ZIP·zip-slip 등) 시 부분 산출물을 제거하고 예외를 던진다 —
미완성 rcept_no 디렉토리를 남기지 않는다(manifest 부재 = 미커밋).
"""

import hashlib
import json
import logging
import shutil
import time
import zipfile
from collections.abc import Callable, Sequence
from datetime import datetime
from io import BytesIO
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from research_backtest.core.constants import (
    NO_DATA_DART_CODES,
    PERIODIC_REPORT_TO_REPRT_CODE,
    ReprtCode,
)
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.exceptions import DartApiError, DataValidationError

logger = logging.getLogger("r2b.dart.xbrl_downloader")

KST = ZoneInfo("Asia/Seoul")

XBRL_API_PATH = "fnlttXbrl.xml"
ZIP_FILENAME = "response.zip"
MANIFEST_FILENAME = "manifest.json"
CHECKSUM_FILENAME = "checksum.sha256"
EXTRACTED_DIRNAME = "extracted"

XBRL_SOURCE = "OPEN_DART_XBRL"
# get_bytes는 검증된 2xx ZIP만 반환하므로(비-ZIP은 DartApiError) 이 둘은 상수다.
HTTP_STATUS_OK = 200
CONTENT_TYPE_ZIP = "application/zip"

XbrlDownloadResult = Literal[
    "FETCHED",  # 신규 다운로드·저장
    "CACHED",  # manifest·checksum 일치, 재다운로드 없음
    "NO_DATA",  # 013/014 — negative cache 신규 기록
    "NO_DATA_CACHED",  # 013/014 negative cache 히트
    "SKIPPED",  # report_type 미상 등으로 reprt_code 결정 불가
    "FAILED",  # 기타 오류 — 배치 진행을 위해 건별 기록(전체 중단하지 않음)
]


class XbrlDownloadOutcome(BaseModel):
    """rcept_no 1건의 수집 결과 (명세 §1.2).

    ``reason``은 SKIPPED/FAILED/NO_DATA일 때의 사유(코드·redact된 메시지),
    ``sha256``은 FETCHED/CACHED일 때 response.zip 해시다.
    """

    rcept_no: str
    reprt_code: str | None
    report_name: str
    result: XbrlDownloadResult
    reason: str | None = None
    sha256: str | None = None


class XbrlManifest(BaseModel):
    """response.zip 옆에 저장하는 수집 메타 (README §8.2).

    성공 시 전 필드가 채워지고, negative cache(013/014)면 ``status``에 코드가
    담기고 ``sha256``·``content_type``은 None이다(zip 없음).
    """

    corp_code: str
    stock_code: str | None
    rcept_no: str
    reprt_code: str
    report_name: str
    filing_date: str  # rcept_no[:8] → YYYY-MM-DD
    downloaded_at: str  # KST ISO8601
    source: str = XBRL_SOURCE
    http_status: int | None = HTTP_STATUS_OK
    content_type: str | None = CONTENT_TYPE_ZIP
    sha256: str | None = None
    status: str | None = None  # negative cache(013/014)일 때만 세팅


def xbrl_out_dir(data_dir: Path, corp_code: str) -> Path:
    """corp_code 단위 XBRL 저장 루트 — ``{data_dir}/raw/dart/xbrl/{corp_code}``."""
    return data_dir / "raw" / "dart" / "xbrl" / corp_code


def xbrl_filing_dir(data_dir: Path, corp_code: str, rcept_no: str) -> Path:
    """rcept_no 단위 저장 디렉토리 — ``.../xbrl/{corp_code}/{rcept_no}``."""
    return xbrl_out_dir(data_dir, corp_code) / rcept_no


def _filing_date_from_rcept_no(rcept_no: str) -> str:
    """rcept_no 선두 8자리를 ``YYYY-MM-DD``로 (README §8.2 filing_date)."""
    if len(rcept_no) >= 8 and rcept_no[:8].isdigit():
        return f"{rcept_no[:4]}-{rcept_no[4:6]}-{rcept_no[6:8]}"
    return rcept_no


def download_xbrl_filing(
    client: DartClient,
    filing: DartFiling,
    *,
    reprt_code: ReprtCode,
    data_dir: Path,
    force: bool = False,
) -> XbrlDownloadOutcome:
    """정기보고서 1건의 XBRL 원본을 다운로드·보존한다 (README §8, §19.4).

    캐시 히트(force=False ∧ manifest 존재 ∧ negative cache이거나 checksum 일치)면
    API를 호출하지 않는다. 013/014는 negative cache로 기록하고, 그 외
    :class:`DartApiError`·손상 ZIP은 예외로 전파한다(배치 래퍼가 건별 처리).
    """
    filing_dir = xbrl_filing_dir(data_dir, filing.corp_code, filing.rcept_no)
    if not force:
        cached = _cached_outcome(filing_dir, filing, reprt_code)
        if cached is not None:
            return cached
    return _fetch_and_store(client, filing, reprt_code=reprt_code, filing_dir=filing_dir)


def download_xbrl_filings(
    client: DartClient,
    filings: Sequence[DartFiling],
    *,
    data_dir: Path,
    force: bool = False,
    min_interval_seconds: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> list[XbrlDownloadOutcome]:
    """정기보고서 목록의 XBRL 원본을 일괄 다운로드한다 (명세 §1.2).

    - ``filing.report_type`` → :data:`PERIODIC_REPORT_TO_REPRT_CODE`로 reprt_code
      결정. report_type이 None이면 건너뛰고 SKIPPED로 기록한다.
    - **실제 API 호출 사이에만** ``min_interval_seconds``를 대기한다(캐시 히트는
      대기 없음, financial_api와 동일).
    - 013/014는 NO_DATA로 negative cache에 기록하고, 그 외 오류(:class:`DartApiError`·
      손상 ZIP·전송 오류)는 해당 건을 FAILED로 기록한 뒤 다음 건으로 진행한다 —
      한 건의 실패가 나머지 수집을 막지 않게 한다(배치 산출물이 B3 실데이터).

    ``sleep``은 테스트 주입용. 반환은 입력 순서와 동일한 결과 목록이다.
    """
    outcomes: list[XbrlDownloadOutcome] = []
    api_called = False
    for filing in filings:
        reprt_code = (
            PERIODIC_REPORT_TO_REPRT_CODE.get(filing.report_type)
            if filing.report_type is not None
            else None
        )
        if reprt_code is None:
            outcomes.append(
                XbrlDownloadOutcome(
                    rcept_no=filing.rcept_no,
                    reprt_code=None,
                    report_name=filing.report_nm,
                    result="SKIPPED",
                    reason="report_type 미상 — reprt_code 결정 불가",
                )
            )
            continue

        filing_dir = xbrl_filing_dir(data_dir, filing.corp_code, filing.rcept_no)
        if not force:
            cached = _cached_outcome(filing_dir, filing, reprt_code)
            if cached is not None:
                outcomes.append(cached)
                continue

        if api_called:
            sleep(min_interval_seconds)
        api_called = True
        try:
            outcomes.append(
                _fetch_and_store(client, filing, reprt_code=reprt_code, filing_dir=filing_dir)
            )
        except (DartApiError, DataValidationError) as err:
            logger.warning("XBRL 수집 실패 rcept_no=%s: %s", filing.rcept_no, err)
            outcomes.append(
                XbrlDownloadOutcome(
                    rcept_no=filing.rcept_no,
                    reprt_code=reprt_code.value,
                    report_name=filing.report_nm,
                    result="FAILED",
                    reason=str(err),
                )
            )
    logger.info(
        "XBRL 배치 수집 완료 filings=%d FETCHED=%d CACHED=%d NO_DATA=%d FAILED=%d",
        len(filings),
        sum(o.result == "FETCHED" for o in outcomes),
        sum(o.result in {"CACHED"} for o in outcomes),
        sum(o.result in {"NO_DATA", "NO_DATA_CACHED"} for o in outcomes),
        sum(o.result == "FAILED" for o in outcomes),
    )
    return outcomes


# --- 캐시 판정 (README §8.3) -------------------------------------------------


def _cached_outcome(
    filing_dir: Path, filing: DartFiling, reprt_code: ReprtCode
) -> XbrlDownloadOutcome | None:
    """캐시 히트면 CACHED/NO_DATA_CACHED 결과를, 미스면 None을 반환한다.

    성공 캐시는 manifest.sha256 == checksum 파일 == response.zip 실제 sha256이
    모두 일치할 때만 유효로 본다(무결성 손상 시 재다운로드).
    """
    manifest = _load_manifest(filing_dir / MANIFEST_FILENAME)
    if manifest is None:
        return None
    if manifest.status in NO_DATA_DART_CODES:
        return XbrlDownloadOutcome(
            rcept_no=filing.rcept_no,
            reprt_code=reprt_code.value,
            report_name=filing.report_nm,
            result="NO_DATA_CACHED",
            reason=manifest.status,
        )
    zip_path = filing_dir / ZIP_FILENAME
    checksum_path = filing_dir / CHECKSUM_FILENAME
    if not zip_path.exists() or not checksum_path.exists() or manifest.sha256 is None:
        return None
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    recorded = checksum_path.read_text(encoding="utf-8").strip()
    if actual != recorded or actual != manifest.sha256:
        logger.warning("XBRL 캐시 무결성 불일치 — 재다운로드: %s", filing.rcept_no)
        return None
    return XbrlDownloadOutcome(
        rcept_no=filing.rcept_no,
        reprt_code=reprt_code.value,
        report_name=filing.report_nm,
        result="CACHED",
        sha256=actual,
    )


# --- 수집·저장 (README §8.1~8.2, §19.4) --------------------------------------


def _fetch_and_store(
    client: DartClient,
    filing: DartFiling,
    *,
    reprt_code: ReprtCode,
    filing_dir: Path,
) -> XbrlDownloadOutcome:
    """XBRL 1건을 호출하고 원본·압축해제·checksum·manifest를 기록한다.

    쓰기 순서: response.zip → extracted/ → checksum.sha256 → manifest.json.
    manifest가 커밋 마커이므로 마지막에 쓴다. 013/014는 manifest만(negative cache).
    """
    downloaded_at = datetime.now(KST).isoformat()
    try:
        raw = client.get_bytes(XBRL_API_PATH, rcept_no=filing.rcept_no, reprt_code=reprt_code.value)
    except DartApiError as err:
        if err.status_code not in NO_DATA_DART_CODES:
            raise
        _write_negative_cache(
            filing_dir, filing, reprt_code, status=err.status_code, downloaded_at=downloaded_at
        )
        logger.info(
            "XBRL 조회 데이터 없음(%s) — negative cache: rcept_no=%s",
            err.status_code,
            filing.rcept_no,
        )
        return XbrlDownloadOutcome(
            rcept_no=filing.rcept_no,
            reprt_code=reprt_code.value,
            report_name=filing.report_nm,
            result="NO_DATA",
            reason=err.status_code,
        )

    sha256 = hashlib.sha256(raw).hexdigest()
    # 무결성 검증은 산출물을 쓰기 전에 — 손상 시 디렉토리를 만들지 않는다.
    _verify_zip_integrity(raw, rcept_no=filing.rcept_no)

    if filing_dir.exists():
        # 미완성 잔여물(manifest 없는 부분 산출물)을 지우고 새로 쓴다.
        shutil.rmtree(filing_dir)
    try:
        filing_dir.mkdir(parents=True, exist_ok=True)
        (filing_dir / ZIP_FILENAME).write_bytes(raw)
        _extract_zip_safely(raw, filing_dir / EXTRACTED_DIRNAME, rcept_no=filing.rcept_no)
        (filing_dir / CHECKSUM_FILENAME).write_text(sha256 + "\n", encoding="utf-8")
        manifest = XbrlManifest(
            corp_code=filing.corp_code,
            stock_code=filing.stock_code,
            rcept_no=filing.rcept_no,
            reprt_code=reprt_code.value,
            report_name=filing.report_nm,
            filing_date=_filing_date_from_rcept_no(filing.rcept_no),
            downloaded_at=downloaded_at,
            sha256=sha256,
        )
        _write_manifest(filing_dir, manifest)
    except BaseException:
        # 부분 산출물 제거 — 미커밋 rcept_no 디렉토리를 남기지 않는다.
        shutil.rmtree(filing_dir, ignore_errors=True)
        raise
    logger.debug("XBRL 저장 완료 rcept_no=%s sha256=%s", filing.rcept_no, sha256[:12])
    return XbrlDownloadOutcome(
        rcept_no=filing.rcept_no,
        reprt_code=reprt_code.value,
        report_name=filing.report_nm,
        result="FETCHED",
        sha256=sha256,
    )


def _verify_zip_integrity(raw: bytes, *, rcept_no: str) -> None:
    """ZIP CRC 무결성을 검증한다 (README §19.4 ZIP 무결성 검증).

    ``testzip()``이 손상 엔트리 이름을 반환하거나 ZIP 열기에 실패하면
    :class:`DataValidationError`.
    """
    try:
        with zipfile.ZipFile(BytesIO(raw)) as zf:
            bad = zf.testzip()
    except zipfile.BadZipFile as err:
        raise DataValidationError(
            f"XBRL 응답이 유효한 ZIP이 아닙니다: rcept_no={rcept_no}"
        ) from err
    if bad is not None:
        raise DataValidationError(
            f"XBRL ZIP 무결성 검증 실패(손상 엔트리={bad!r}): rcept_no={rcept_no}"
        )


def _extract_zip_safely(raw: bytes, extracted_dir: Path, *, rcept_no: str) -> None:
    """ZIP을 zip-slip 방어와 함께 압축 해제한다 (명세 §1.1 압축 해제 보안).

    엔트리 이름에 절대경로·드라이브·``..``가 있거나 목적지가 extracted_dir 밖으로
    벗어나면 :class:`DataValidationError`. 디렉토리 엔트리는 mkdir만 한다.
    """
    extracted_dir.mkdir(parents=True, exist_ok=True)
    root = extracted_dir.resolve()
    with zipfile.ZipFile(BytesIO(raw)) as zf:
        for info in zf.infolist():
            name = info.filename
            if _is_unsafe_member_name(name):
                raise DataValidationError(
                    f"XBRL ZIP에 안전하지 않은 경로가 있습니다(zip-slip 방어): "
                    f"rcept_no={rcept_no}, entry={name!r}"
                )
            dest = (extracted_dir / name).resolve()
            if root != dest and root not in dest.parents:
                raise DataValidationError(
                    f"XBRL ZIP 엔트리가 대상 디렉토리를 벗어납니다(zip-slip 방어): "
                    f"rcept_no={rcept_no}, entry={name!r}"
                )
            if info.is_dir():
                dest.mkdir(parents=True, exist_ok=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(zf.read(info))


def _is_unsafe_member_name(name: str) -> bool:
    """엔트리 이름이 절대경로·드라이브·상위참조(``..``)를 포함하는지."""
    if not name or name.startswith("/") or name.startswith("\\"):
        return True
    posix = PurePosixPath(name)
    windows = PureWindowsPath(name)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        return True
    return ".." in posix.parts or ".." in windows.parts


# --- manifest I/O ------------------------------------------------------------


def _write_negative_cache(
    filing_dir: Path,
    filing: DartFiling,
    reprt_code: ReprtCode,
    *,
    status: str,
    downloaded_at: str,
) -> None:
    """013/014 negative cache manifest만 기록한다(zip 없음)."""
    filing_dir.mkdir(parents=True, exist_ok=True)
    manifest = XbrlManifest(
        corp_code=filing.corp_code,
        stock_code=filing.stock_code,
        rcept_no=filing.rcept_no,
        reprt_code=reprt_code.value,
        report_name=filing.report_nm,
        filing_date=_filing_date_from_rcept_no(filing.rcept_no),
        downloaded_at=downloaded_at,
        http_status=None,
        content_type=None,
        sha256=None,
        status=status,
    )
    _write_manifest(filing_dir, manifest)


def _write_manifest(filing_dir: Path, manifest: XbrlManifest) -> None:
    (filing_dir / MANIFEST_FILENAME).write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )


def _load_manifest(manifest_path: Path) -> XbrlManifest | None:
    """manifest.json을 읽는다 — 없거나 파싱 불가면 None(캐시 미스)."""
    if not manifest_path.exists():
        return None
    try:
        payload: Any = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        return XbrlManifest.model_validate(payload)
    except ValueError:
        return None
