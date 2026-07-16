"""XBRL 원본 수집 단위 테스트 (README §8, §19.4, 명세 §1) — 네트워크 금지."""

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_backtest.core.constants import (
    PERIODIC_REPORT_TO_REPRT_CODE,
    PeriodicReportType,
    ReprtCode,
)
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.models import DartFiling
from research_backtest.core.dart.xbrl_downloader import (
    CHECKSUM_FILENAME,
    EXTRACTED_DIRNAME,
    MANIFEST_FILENAME,
    XBRL_SOURCE,
    ZIP_FILENAME,
    XbrlManifest,
    download_xbrl_filing,
    download_xbrl_filings,
    xbrl_filing_dir,
)
from research_backtest.core.exceptions import DataValidationError

ClientFactory = Callable[..., DartClient]
FilingFactory = Callable[..., DartFiling]
ZipFactory = Callable[[Mapping[str, bytes]], bytes]

CORP = "00164779"
XBRL_PATH = "/api/fnlttXbrl.xml"


def _reprt(filing: DartFiling) -> ReprtCode:
    """filing.report_type → reprt_code (테스트 전제: 정기보고서)."""
    assert filing.report_type is not None
    return PERIODIC_REPORT_TO_REPRT_CODE[filing.report_type]


def _make_handler(
    zip_by_rcept: Mapping[str, bytes],
    *,
    error_by_rcept: Mapping[str, str] | None = None,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[tuple[str, str]]]:
    """rcept_no로 ZIP·오류 XML을 라우팅하고 호출을 기록하는 핸들러."""
    errors = dict(error_by_rcept or {})
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == XBRL_PATH
        params = request.url.params
        rcept = params["rcept_no"]
        calls.append((rcept, params["reprt_code"]))
        if rcept in errors:
            body = (
                f'<?xml version="1.0" encoding="utf-8"?><result>'
                f"<status>{errors[rcept]}</status><message>없음</message></result>"
            ).encode()
            return httpx.Response(200, content=body, headers={"Content-Type": "application/xml"})
        if rcept in zip_by_rcept:
            return httpx.Response(
                200,
                content=zip_by_rcept[rcept],
                headers={"Content-Type": "application/zip"},
            )
        body = b'<?xml version="1.0"?><result><status>013</status></result>'
        return httpx.Response(200, content=body, headers={"Content-Type": "application/xml"})

    return handler, calls


# --- 저장 레이아웃·manifest·checksum (명세 §1.1) ------------------------------


def test_download_stores_layout_and_manifest(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, calls = _make_handler({"20250319000665": sample_xbrl_zip})

    with make_dart_client(handler) as client:
        outcome = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    assert outcome.result == "FETCHED"
    assert calls == [("20250319000665", "11011")]

    fdir = xbrl_filing_dir(tmp_path, CORP, "20250319000665")
    # 레이아웃: response.zip / manifest.json / extracted/ / checksum.sha256
    assert (fdir / ZIP_FILENAME).read_bytes() == sample_xbrl_zip
    assert (fdir / EXTRACTED_DIRNAME).is_dir()
    extracted_files = {p.name for p in (fdir / EXTRACTED_DIRNAME).iterdir()}
    assert "entity00164779_2024-12-31.xbrl" in extracted_files  # 파일명 비고정 그대로 보존

    # checksum 파일 == manifest.sha256 == sha256(response.zip)
    expected_sha = hashlib.sha256(sample_xbrl_zip).hexdigest()
    assert (fdir / CHECKSUM_FILENAME).read_text(encoding="utf-8").strip() == expected_sha
    assert outcome.sha256 == expected_sha

    manifest: Any = json.loads((fdir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert manifest["corp_code"] == CORP
    assert manifest["stock_code"] == "000660"
    assert manifest["rcept_no"] == "20250319000665"
    assert manifest["reprt_code"] == "11011"
    assert manifest["report_name"] == "사업보고서 (2024.12)"
    assert manifest["filing_date"] == "2025-03-19"  # rcept_no[:8]
    assert manifest["source"] == XBRL_SOURCE
    assert manifest["http_status"] == 200
    assert manifest["content_type"] == "application/zip"
    assert manifest["sha256"] == expected_sha
    assert manifest["status"] is None  # 성공은 negative-cache status 없음
    assert manifest["downloaded_at"].endswith("+09:00")  # KST


# --- 멱등성·force (README §8.3) ----------------------------------------------


def test_second_run_is_cached_without_api_call(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, calls = _make_handler({"20250319000665": sample_xbrl_zip})

    with make_dart_client(handler) as client:
        first = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)
        assert first.result == "FETCHED"
        assert len(calls) == 1
        second = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    assert second.result == "CACHED"
    assert len(calls) == 1  # 두 번째는 API 호출 0회
    assert second.sha256 == first.sha256


def test_force_redownloads(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, calls = _make_handler({"20250319000665": sample_xbrl_zip})

    with make_dart_client(handler) as client:
        download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)
        forced = download_xbrl_filing(
            client, filing, reprt_code=_reprt(filing), data_dir=tmp_path, force=True
        )

    assert forced.result == "FETCHED"
    assert len(calls) == 2  # force는 캐시 무시 후 재다운로드


def test_cache_miss_when_checksum_tampered(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, calls = _make_handler({"20250319000665": sample_xbrl_zip})

    with make_dart_client(handler) as client:
        download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)
        # response.zip 손상(무결성 불일치) → 캐시 미스로 재다운로드
        fdir = xbrl_filing_dir(tmp_path, CORP, "20250319000665")
        (fdir / ZIP_FILENAME).write_bytes(b"PK\x03\x04corrupted-bytes")
        outcome = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    assert outcome.result == "FETCHED"
    assert len(calls) == 2


# --- 손상 ZIP·zip-slip (README §19.4, 명세 §1.1) -----------------------------


def test_corrupt_zip_raises_and_leaves_no_artifacts(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    # PK magic은 있으나(클라이언트의 ZIP 판정 통과) 내용이 손상된 바이트
    corrupt = b"PK\x03\x04" + b"\x00" * 40
    handler, _calls = _make_handler({"20250319000665": corrupt})

    with make_dart_client(handler) as client, pytest.raises(DataValidationError):
        download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    # 부분 산출물 없음 — 무결성 검증을 쓰기 전에 하므로 디렉토리 자체가 없다
    assert not xbrl_filing_dir(tmp_path, CORP, "20250319000665").exists()


def test_zip_slip_entry_rejected(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    make_xbrl_zip: ZipFactory,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    # 유효한 ZIP이지만 엔트리 이름이 상위 디렉토리로 탈출을 시도
    evil_zip = make_xbrl_zip({"../evil.txt": b"pwned", "instance.xbrl": b"<a/>"})
    handler, _calls = _make_handler({"20250319000665": evil_zip})

    with make_dart_client(handler) as client, pytest.raises(DataValidationError, match="zip-slip"):
        download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    # 탈출 대상 파일이 생성되지 않았고, 부분 산출물 디렉토리도 제거됨
    assert not (tmp_path / "raw" / "dart" / "xbrl" / CORP / "evil.txt").exists()
    assert not (tmp_path / "evil.txt").exists()
    assert not xbrl_filing_dir(tmp_path, CORP, "20250319000665").exists()


# --- 013/014 negative cache (명세 §1.1) --------------------------------------


def test_no_data_writes_negative_cache_manifest_only(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, calls = _make_handler({}, error_by_rcept={"20250319000665": "013"})

    with make_dart_client(handler) as client:
        first = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)
        second = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    assert first.result == "NO_DATA"
    assert first.reason == "013"
    assert second.result == "NO_DATA_CACHED"
    assert len(calls) == 1  # 두 번째는 재조회 없음

    fdir = xbrl_filing_dir(tmp_path, CORP, "20250319000665")
    manifest = XbrlManifest.model_validate_json((fdir / MANIFEST_FILENAME).read_text("utf-8"))
    assert manifest.status == "013"
    assert manifest.sha256 is None
    assert manifest.http_status is None
    assert not (fdir / ZIP_FILENAME).exists()  # zip 없음
    assert not (fdir / EXTRACTED_DIRNAME).exists()


def test_file_not_found_014_negative_cache(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    tmp_path: Path,
) -> None:
    filing = make_filing(rcept_no="20250319000665")
    handler, _calls = _make_handler({}, error_by_rcept={"20250319000665": "014"})

    with make_dart_client(handler) as client:
        outcome = download_xbrl_filing(client, filing, reprt_code=_reprt(filing), data_dir=tmp_path)

    assert outcome.result == "NO_DATA"
    assert outcome.reason == "014"
    fdir = xbrl_filing_dir(tmp_path, CORP, "20250319000665")
    manifest = XbrlManifest.model_validate_json((fdir / MANIFEST_FILENAME).read_text("utf-8"))
    assert manifest.status == "014"


# --- 배치 (명세 §1.2) --------------------------------------------------------


def test_batch_maps_reprt_code_and_skips_unknown_type(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    annual = make_filing(rcept_no="20250319000665", report_type=PeriodicReportType.ANNUAL)
    half = make_filing(
        rcept_no="20240814001887",
        report_type=PeriodicReportType.HALF,
        report_nm="반기보고서 (2024.06)",
    )
    non_periodic = make_filing(
        rcept_no="20240101000001", report_type=None, report_nm="주요사항보고서"
    )
    handler, calls = _make_handler(
        {"20250319000665": sample_xbrl_zip, "20240814001887": sample_xbrl_zip}
    )

    with make_dart_client(handler) as client:
        outcomes = download_xbrl_filings(
            client, [annual, half, non_periodic], data_dir=tmp_path, sleep=lambda _s: None
        )

    by_rcept = {o.rcept_no: o for o in outcomes}
    assert by_rcept["20250319000665"].result == "FETCHED"
    assert by_rcept["20250319000665"].reprt_code == "11011"
    assert by_rcept["20240814001887"].result == "FETCHED"
    assert by_rcept["20240814001887"].reprt_code == "11012"  # 반기
    assert by_rcept["20240101000001"].result == "SKIPPED"
    assert by_rcept["20240101000001"].reprt_code is None
    # SKIPPED는 API 호출하지 않는다
    assert {c[0] for c in calls} == {"20250319000665", "20240814001887"}


def test_batch_sleeps_between_real_calls_only(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    filings = [
        make_filing(rcept_no="20250319000665"),
        make_filing(rcept_no="20240319000684", report_nm="사업보고서 (2023.12)"),
        make_filing(rcept_no="20230321001209", report_nm="사업보고서 (2022.12)"),
    ]
    handler, _calls = _make_handler({f.rcept_no: sample_xbrl_zip for f in filings})
    sleeps: list[float] = []

    with make_dart_client(handler) as client:
        download_xbrl_filings(
            client, filings, data_dir=tmp_path, min_interval_seconds=0.2, sleep=sleeps.append
        )
        # 두 번째 실행은 전부 캐시 → sleep 없음
        cached_sleeps: list[float] = []
        download_xbrl_filings(
            client, filings, data_dir=tmp_path, min_interval_seconds=0.2, sleep=cached_sleeps.append
        )

    assert sleeps == [0.2, 0.2]  # 첫 호출 전에는 대기하지 않는다 (3건 → 2회 대기)
    assert cached_sleeps == []


def test_batch_records_failure_and_continues(
    make_dart_client: ClientFactory,
    make_filing: FilingFactory,
    sample_xbrl_zip: bytes,
    tmp_path: Path,
) -> None:
    good = make_filing(rcept_no="20250319000665")
    bad = make_filing(rcept_no="20240319000684", report_nm="사업보고서 (2023.12)")
    another_good = make_filing(rcept_no="20230321001209", report_nm="사업보고서 (2022.12)")
    # bad는 PK magic만 있고 손상된 ZIP → DataValidationError → FAILED로 기록 후 계속
    handler, _calls = _make_handler(
        {
            "20250319000665": sample_xbrl_zip,
            "20240319000684": b"PK\x03\x04" + b"\x00" * 20,
            "20230321001209": sample_xbrl_zip,
        }
    )

    with make_dart_client(handler) as client:
        outcomes = download_xbrl_filings(
            client, [good, bad, another_good], data_dir=tmp_path, sleep=lambda _s: None
        )

    results = {o.rcept_no: o.result for o in outcomes}
    assert results["20250319000665"] == "FETCHED"
    assert results["20240319000684"] == "FAILED"  # 실패는 건별 기록, 배치는 계속
    assert results["20230321001209"] == "FETCHED"  # 실패 뒤 건도 정상 수집
    failed = next(o for o in outcomes if o.result == "FAILED")
    assert failed.reason is not None
    # 실패 건은 부분 산출물 없음
    assert not xbrl_filing_dir(tmp_path, CORP, "20240319000684").exists()
