"""전체 재무제표 API 실호출 integration 테스트 (명세 A2 §6) — DART_API_KEY 없으면 skip.

out_dir은 항상 tmp_path 계열을 사용한다 — 실데이터 디렉토리(data/)를
오염시키지 않는다. 실행: 레포 루트에서 ``pytest -m integration``.
"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.constants import FsDiv, ReprtCode
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.financial_api import CollectionSummary, collect_financials

pytestmark = pytest.mark.integration

SK_HYNIX_CORP_CODE = "00164779"  # README §8.2 예시


@pytest.fixture(scope="module")
def dart_client() -> Iterator[DartClient]:
    settings = get_settings()
    if not settings.dart_api_key:
        pytest.skip("DART_API_KEY 미설정 — integration 테스트 생략")
    with DartClient(settings.dart_api_key) as client:
        yield client


@pytest.fixture(scope="module")
def out_dir_2024(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("financials_2024")


@pytest.fixture(scope="module")
def summary_2024(dart_client: DartClient, out_dir_2024: Path) -> CollectionSummary:
    """2024 사업연도 CFS 4개 보고서를 실수집한다 (연 4요청)."""
    return collect_financials(
        dart_client,
        SK_HYNIX_CORP_CODE,
        from_year=2024,
        to_year=2024,
        fs_divs=(FsDiv.CFS,),
        out_dir=out_dir_2024,
    )


def test_2024_annual_cfs_has_all_statement_types(
    summary_2024: CollectionSummary, out_dir_2024: Path
) -> None:
    annual = next(o for o in summary_2024.outcomes if o.reprt_code is ReprtCode.ANNUAL)
    assert annual.result == "FETCHED"
    assert annual.row_count > 100
    divs = set(annual.sj_div_counts)
    assert {"BS", "CF", "SCE"} <= divs
    assert divs & {"IS", "CIS"}
    assert annual.rcept_nos

    # 원문 텍스트 + sha256 meta로 raw 응답이 재현 가능하다 (README §31 M2)
    meta: Any = json.loads((out_dir_2024 / "2024_11011_CFS.meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "000"
    data_bytes = (out_dir_2024 / "2024_11011_CFS.json").read_bytes()
    assert meta["sha256"] == hashlib.sha256(data_bytes).hexdigest()


def test_second_run_hits_cache_without_refetch(
    dart_client: DartClient, out_dir_2024: Path, summary_2024: CollectionSummary
) -> None:
    meta_path = out_dir_2024 / "2024_11011_CFS.meta.json"
    mtime_before = meta_path.stat().st_mtime_ns
    second = collect_financials(
        dart_client,
        SK_HYNIX_CORP_CODE,
        from_year=2024,
        to_year=2024,
        fs_divs=(FsDiv.CFS,),
        out_dir=out_dir_2024,
    )
    assert all(o.result in {"CACHED", "NO_DATA_CACHED"} for o in second.outcomes)
    # 전송 계층 호출 수는 셀 수 없으므로 meta mtime 불변으로 재다운로드 없음을 검증
    assert meta_path.stat().st_mtime_ns == mtime_before


def test_unfiled_2026_annual_is_negative_cached(dart_client: DartClient, tmp_path: Path) -> None:
    out_dir = tmp_path / "financials_2026"
    first = collect_financials(
        dart_client,
        SK_HYNIX_CORP_CODE,
        from_year=2026,
        to_year=2026,
        fs_divs=(FsDiv.CFS,),
        out_dir=out_dir,
    )
    annual_first = next(o for o in first.outcomes if o.reprt_code is ReprtCode.ANNUAL)
    assert annual_first.result == "NO_DATA"  # 2026 사업보고서는 아직 미제출

    meta: Any = json.loads((out_dir / "2026_11011_CFS.meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "013"
    assert not (out_dir / "2026_11011_CFS.json").exists()

    second = collect_financials(
        dart_client,
        SK_HYNIX_CORP_CODE,
        from_year=2026,
        to_year=2026,
        fs_divs=(FsDiv.CFS,),
        out_dir=out_dir,
    )
    annual_second = next(o for o in second.outcomes if o.reprt_code is ReprtCode.ANNUAL)
    assert annual_second.result == "NO_DATA_CACHED"
