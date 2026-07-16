"""전체 재무제표 API 수집 단위 테스트 (README §6.4, §19.3, 명세 A2 §6) — 네트워크 금지."""

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_backtest.core.constants import FsDiv, ReprtCode
from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.financial_api import (
    COLLECTION_SOURCE,
    JSONL_FILENAME,
    REPORT_FILENAME,
    CollectionSummary,
    collect_financials,
)

ClientFactory = Callable[..., DartClient]

CORP = "00164779"
# (bsns_year, reprt_code, fs_div) — mock 응답 라우팅 키
Key = tuple[str, str, str]

NO_DATA_BODY = '{"status":"013","message":"조회된 데이타가 없습니다."}'.encode()


@pytest.fixture
def sample_body(fixtures_dir: Path) -> bytes:
    """SK하이닉스 2024 사업보고서 CFS 축약 응답 — sj_div 5종(BS4·IS3·CIS2·CF2·SCE1)."""
    return (fixtures_dir / "fnltt_singl_acnt_all_sample.json").read_bytes()


def _make_handler(
    bodies: dict[Key, bytes],
) -> tuple[Callable[[httpx.Request], httpx.Response], list[Key]]:
    """키에 등록된 응답을 돌려주고(없으면 013), 호출 키를 기록하는 핸들러."""
    calls: list[Key] = []

    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert request.url.path == "/api/fnlttSinglAcntAll.json"
        assert params["corp_code"] == CORP
        key = (params["bsns_year"], params["reprt_code"], params["fs_div"])
        calls.append(key)
        body = bodies.get(key, NO_DATA_BODY)
        return httpx.Response(200, content=body, headers={"Content-Type": "application/json"})

    return handler, calls


def _make_body(bsns_year: str, reprt_code: str, rows: list[dict[str, str]]) -> bytes:
    """최소 필수 필드를 채운 응답 본문을 만든다 (rows에는 sj_div·account_id 지정)."""
    full_rows = [
        {
            "rcept_no": f"{bsns_year}0515{reprt_code}",
            "reprt_code": reprt_code,
            "bsns_year": bsns_year,
            "corp_code": CORP,
            "sj_nm": "재무제표",
            "account_nm": "계정",
            "account_detail": "-",
            **row,
        }
        for row in rows
    ]
    payload = {"status": "000", "message": "정상", "list": full_rows}
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


# --- fetch·저장 (명세 §3.1~3.2) ----------------------------------------------


def test_fetch_stores_raw_text_and_meta(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    handler, calls = _make_handler({("2024", "11011", "CFS"): sample_body})
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        summary = collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )

    # 연 4개 보고서 x CFS = 4요청, 순서는 ReprtCode 선언 순서(Q1→반기→Q3→사업)
    assert [key[1] for key in calls] == ["11013", "11012", "11014", "11011"]
    assert [outcome.result for outcome in summary.outcomes] == [
        "NO_DATA",
        "NO_DATA",
        "NO_DATA",
        "FETCHED",
    ]

    # 원문 텍스트 그대로 저장 — 파일 내용 == 응답 본문 (재직렬화 금지)
    assert (out_dir / "2024_11011_CFS.json").read_bytes() == sample_body

    meta: Any = json.loads((out_dir / "2024_11011_CFS.meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "000"
    assert meta["sha256"] == hashlib.sha256(sample_body).hexdigest()
    assert meta["row_count"] == 12
    assert meta["rcept_nos"] == ["20250320001234"]
    assert meta["sj_div_counts"] == {"BS": 4, "IS": 3, "CIS": 2, "CF": 2, "SCE": 1}
    assert meta["source"] == COLLECTION_SOURCE
    assert meta["params"] == {
        "corp_code": CORP,
        "bsns_year": "2024",
        "reprt_code": "11011",
        "fs_div": "CFS",
    }

    fetched = summary.outcomes[-1]
    assert fetched.row_count == 12
    assert fetched.sj_div_counts == {"BS": 4, "IS": 3, "CIS": 2, "CF": 2, "SCE": 1}
    assert fetched.rcept_nos == ["20250320001234"]

    # collection_report.json은 CollectionSummary와 동형이다
    report = CollectionSummary.model_validate_json(
        (out_dir / REPORT_FILENAME).read_text(encoding="utf-8")
    )
    assert report.corp_code == CORP
    assert len(report.outcomes) == 4


# --- 캐시 (명세 §3.3) --------------------------------------------------------


def test_second_run_hits_cache_without_api_calls(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    handler, calls = _make_handler({("2024", "11011", "CFS"): sample_body})
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )
        assert len(calls) == 4
        second = collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )

    assert len(calls) == 4  # 두 번째 실행은 API 호출 0회
    assert [outcome.result for outcome in second.outcomes] == [
        "NO_DATA_CACHED",
        "NO_DATA_CACHED",
        "NO_DATA_CACHED",
        "CACHED",
    ]
    cached = second.outcomes[-1]
    # CACHED는 meta에서 읽는다 — 데이터 파일 재파싱 없음
    assert cached.row_count == 12
    assert cached.sj_div_counts == {"BS": 4, "IS": 3, "CIS": 2, "CF": 2, "SCE": 1}
    assert cached.rcept_nos == ["20250320001234"]


def test_force_redownloads_everything(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    handler, calls = _make_handler({("2024", "11011", "CFS"): sample_body})
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )
        third = collect_financials(
            client,
            CORP,
            from_year=2024,
            to_year=2024,
            fs_divs=(FsDiv.CFS,),
            out_dir=out_dir,
            force=True,
        )

    assert len(calls) == 8  # force는 negative cache 포함 전부 재수집
    assert [outcome.result for outcome in third.outcomes] == [
        "NO_DATA",
        "NO_DATA",
        "NO_DATA",
        "FETCHED",
    ]


def test_data_file_without_meta_is_refetched(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    handler, calls = _make_handler({("2024", "11011", "CFS"): sample_body})
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )
        # meta가 커밋 마커 — meta 없이 데이터 파일만 있으면 캐시 미스 (명세 §3.2)
        (out_dir / "2024_11011_CFS.meta.json").unlink()
        second = collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )

    assert len(calls) == 5  # 사업보고서 1건만 재수집
    assert second.outcomes[-1].result == "FETCHED"
    assert (out_dir / "2024_11011_CFS.meta.json").exists()


# --- negative cache (명세 §3.2~3.3) ------------------------------------------


def test_no_data_writes_negative_cache_meta_only(
    make_dart_client: ClientFactory, tmp_path: Path
) -> None:
    handler, calls = _make_handler({})  # 전부 013
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        first = collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )
        second = collect_financials(
            client, CORP, from_year=2024, to_year=2024, fs_divs=(FsDiv.CFS,), out_dir=out_dir
        )

    assert [outcome.result for outcome in first.outcomes] == ["NO_DATA"] * 4
    assert [outcome.result for outcome in second.outcomes] == ["NO_DATA_CACHED"] * 4
    assert len(calls) == 4  # 두 번째 실행은 호출 0회

    meta: Any = json.loads((out_dir / "2024_11011_CFS.meta.json").read_text(encoding="utf-8"))
    assert meta["status"] == "013"
    assert meta["row_count"] == 0
    assert "sha256" not in meta
    assert "rcept_nos" not in meta
    assert not (out_dir / "2024_11011_CFS.json").exists()  # 데이터 파일 없음
    # 수집 결과가 없으므로 병합본은 빈 파일(0라인)
    assert (out_dir / JSONL_FILENAME).read_text(encoding="utf-8") == ""


# --- financial_api_raw.jsonl 병합 (명세 §3.4) --------------------------------


def test_jsonl_merge_is_deterministic_with_provenance(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    bodies = {
        # 연도·보고서·scope 순서를 뒤섞어 등록해도 병합 순서는 결정적이어야 한다
        ("2024", "11011", "OFS"): _make_body(
            "2024", "11011", [{"sj_div": "BS", "account_id": "ifrs-full_Assets"}]
        ),
        ("2024", "11011", "CFS"): sample_body,
        ("2024", "11013", "CFS"): _make_body(
            "2024", "11013", [{"sj_div": "IS", "account_id": "ifrs-full_Revenue"}]
        ),
        ("2023", "11012", "OFS"): _make_body(
            "2023",
            "11012",
            [
                {"sj_div": "BS", "account_id": "ifrs-full_Assets"},
                {"sj_div": "CF", "account_id": "-표준계정코드 미사용-"},
            ],
        ),
    }
    handler, _calls = _make_handler(bodies)
    out_dir = tmp_path / "fin"

    with make_dart_client(handler) as client:
        summary = collect_financials(client, CORP, from_year=2023, to_year=2024, out_dir=out_dir)

    fetched_rows = sum(o.row_count for o in summary.outcomes if o.result == "FETCHED")
    lines = (out_dir / JSONL_FILENAME).read_text(encoding="utf-8").splitlines()
    assert len(lines) == fetched_rows == 2 + 1 + 12 + 1

    records: list[Any] = [json.loads(line) for line in lines]
    assert all(set(record) == {"bsns_year", "reprt_code", "fs_div", "row"} for record in records)
    # 정렬: bsns_year 오름차순 → reprt_code(11013→11012→11014→11011) → fs_div(CFS→OFS)
    provenance = [(r["bsns_year"], r["reprt_code"], r["fs_div"]) for r in records]
    assert provenance == (
        [("2023", "11012", "OFS")] * 2
        + [("2024", "11013", "CFS")]
        + [("2024", "11011", "CFS")] * 12
        + [("2024", "11011", "OFS")]
    )
    # 응답 내 행 순서 보존 + 행 원문 필드 유지
    assert records[3]["row"]["account_id"] == "ifrs-full_CurrentAssets"
    assert records[3]["row"]["sj_div"] == "BS"

    # 재실행(전부 캐시)해도 병합본은 바이트 단위로 동일하다(멱등)
    jsonl_before = (out_dir / JSONL_FILENAME).read_bytes()
    with make_dart_client(handler) as client:
        collect_financials(client, CORP, from_year=2023, to_year=2024, out_dir=out_dir)
    assert (out_dir / JSONL_FILENAME).read_bytes() == jsonl_before


# --- 경계 (README §6.4 — 2015년 이후 제공) -----------------------------------


def _forbidden_handler(request: httpx.Request) -> httpx.Response:
    raise AssertionError("경계 검증 실패 시 API가 호출되면 안 된다")


def test_from_year_before_2015_raises_value_error(
    make_dart_client: ClientFactory, tmp_path: Path
) -> None:
    with (
        make_dart_client(_forbidden_handler) as client,
        pytest.raises(ValueError, match="2015"),
    ):
        collect_financials(client, CORP, from_year=2014, to_year=2024, out_dir=tmp_path)


def test_from_year_after_to_year_raises_value_error(
    make_dart_client: ClientFactory, tmp_path: Path
) -> None:
    with (
        make_dart_client(_forbidden_handler) as client,
        pytest.raises(ValueError, match="to_year"),
    ):
        collect_financials(client, CORP, from_year=2024, to_year=2023, out_dir=tmp_path)


# --- 호출 간격 (명세 §4 — 실제 API 호출 사이에만 대기) -------------------------


def test_sleep_called_between_real_api_calls_only(
    make_dart_client: ClientFactory, sample_body: bytes, tmp_path: Path
) -> None:
    bodies = {("2024", code.value, "CFS"): sample_body for code in ReprtCode}
    handler, calls = _make_handler(bodies)
    out_dir = tmp_path / "fin"
    sleeps: list[float] = []

    with make_dart_client(handler) as client:
        collect_financials(
            client,
            CORP,
            from_year=2024,
            to_year=2024,
            fs_divs=(FsDiv.CFS,),
            out_dir=out_dir,
            min_interval_seconds=0.25,
            sleep=sleeps.append,
        )

    assert len(calls) == 4
    assert sleeps == [0.25, 0.25, 0.25]  # 첫 호출 전에는 대기하지 않는다

    cached_sleeps: list[float] = []
    with make_dart_client(handler) as client:
        collect_financials(
            client,
            CORP,
            from_year=2024,
            to_year=2024,
            fs_divs=(FsDiv.CFS,),
            out_dir=out_dir,
            min_interval_seconds=0.25,
            sleep=cached_sleeps.append,
        )

    assert len(calls) == 4  # 전부 캐시 히트
    assert cached_sleeps == []  # 캐시 히트는 대기 없음
