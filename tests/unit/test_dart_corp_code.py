"""corp_code 단위 테스트 — 정규화·resolve·캐시 (README §6.1, §19.1, 명세 A1 §2)."""

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest

from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.corp_code import (
    CorpCodeRegistry,
    corp_code_cache_dir,
    load_corp_code_registry,
    normalize_corp_name,
    parse_corp_code_zip,
)

ClientFactory = Callable[..., DartClient]
KST = ZoneInfo("Asia/Seoul")


# --- 기업명 정규화 (README §6.1) -------------------------------------------


def test_normalize_strips_legal_form_prefix() -> None:
    assert normalize_corp_name("(주)SK하이닉스") == normalize_corp_name("SK하이닉스")
    assert normalize_corp_name("주식회사 카카오") == normalize_corp_name("카카오")


def test_normalize_handles_compatibility_chars_via_nfkc() -> None:
    # ㈜는 NFKC로 "(주)"가 된 뒤 제거된다. 전각 영문도 반각으로 정리된다.
    assert normalize_corp_name("㈜SK하이닉스") == normalize_corp_name("SK하이닉스")
    assert normalize_corp_name("ＳＫ하이닉스") == normalize_corp_name("SK하이닉스")


def test_normalize_casefolds_and_removes_whitespace() -> None:
    assert normalize_corp_name("SK  HYNIX") == normalize_corp_name("sk hynix")
    assert normalize_corp_name(" 삼성 전자 ") == normalize_corp_name("삼성전자")


def test_normalize_removes_special_chars() -> None:
    assert normalize_corp_name("에스.케이-하이닉스&") == "에스케이하이닉스"
    assert normalize_corp_name("S&P글로벌, 주식회사") == "sp글로벌"
    assert normalize_corp_name("현대·기아") == normalize_corp_name("현대기아")


# --- 고유번호 ZIP 파싱 ------------------------------------------------------


def test_parse_corp_code_zip_reads_first_xml_entry(corp_code_zip: bytes) -> None:
    corporations = parse_corp_code_zip(corp_code_zip)
    assert len(corporations) == 6
    by_code = {corp.corp_code: corp for corp in corporations}
    sk = by_code["00164779"]
    assert sk.corp_name == "SK하이닉스"
    assert sk.corp_eng_name == "SK hynix Inc."
    assert sk.stock_code == "000660"
    # 비상장은 stock_code가 공백 → None, 빈 영문명 → None
    unlisted = by_code["00990001"]
    assert unlisted.stock_code is None
    assert unlisted.corp_eng_name is None


# --- resolve 규칙 (README §19.1, 명세 §2.3) ---------------------------------


@pytest.fixture
def registry(corp_code_zip: bytes) -> CorpCodeRegistry:
    return CorpCodeRegistry(parse_corp_code_zip(corp_code_zip))


def test_resolve_by_stock_code(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("000660")
    assert result.method == "STOCK_CODE"
    assert result.matched is not None
    assert result.matched.corp_code == "00164779"


def test_resolve_unknown_stock_code_is_not_found(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("123456")
    assert result.method == "NOT_FOUND"
    assert result.matched is None


def test_resolve_exact_name_after_normalization(registry: CorpCodeRegistry) -> None:
    for query in ("SK하이닉스", "(주)sk하이닉스", "㈜SK하이닉스", "SK 하이닉스"):
        result = registry.resolve(query)
        assert result.method == "EXACT_NAME", query
        assert result.matched is not None
        assert result.matched.corp_code == "00164779"


def test_resolve_matches_english_name(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("SK hynix Inc.")
    assert result.method == "EXACT_NAME"
    assert result.matched is not None
    assert result.matched.corp_code == "00164779"


def test_resolve_prefers_listed_corp_on_duplicate_names(registry: CorpCodeRegistry) -> None:
    # "삼성전자"는 상장(00126380)·비상장(00990001) 동명 2건 → 상장 우선
    result = registry.resolve("삼성전자")
    assert result.method == "EXACT_NAME"
    assert result.matched is not None
    assert result.matched.corp_code == "00126380"


def test_resolve_ambiguous_when_multiple_unlisted_share_name(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("쌍둥이상사")
    assert result.method == "AMBIGUOUS"
    assert result.matched is None
    assert {corp.corp_code for corp in result.candidates} == {"00990002", "00990003"}


def test_resolve_substring_single_match(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("세미콘")
    assert result.method == "SUBSTRING"
    assert result.matched is not None
    assert result.matched.corp_code == "00990004"


def test_resolve_substring_multiple_matches_is_ambiguous(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("하이닉스")
    assert result.method == "AMBIGUOUS"
    assert result.matched is None
    assert {corp.corp_code for corp in result.candidates} == {"00164779", "00990004"}
    # 후보 정렬은 상장 우선
    assert result.candidates[0].corp_code == "00164779"


def test_resolve_not_found(registry: CorpCodeRegistry) -> None:
    result = registry.resolve("없는회사이름졸라이상한")
    assert result.method == "NOT_FOUND"
    assert result.matched is None
    assert result.candidates == []


# --- 캐시 동작 (README §6.1 캐시 갱신, §8.3 멱등성 취지) ---------------------


def _counting_zip_handler(
    zip_bytes: bytes,
) -> tuple[Callable[[httpx.Request], httpx.Response], list[int]]:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(200, content=zip_bytes)

    return handler, calls


def test_cache_prevents_second_download(
    make_dart_client: ClientFactory, corp_code_zip: bytes, tmp_path: Path
) -> None:
    handler, calls = _counting_zip_handler(corp_code_zip)
    cache_dir = corp_code_cache_dir(tmp_path / "data")

    with make_dart_client(handler) as client:
        first = load_corp_code_registry(client, cache_dir, refresh_days=7)
        second = load_corp_code_registry(client, cache_dir, refresh_days=7)

    assert len(calls) == 1  # 두 번째 호출은 네트워크 미발생
    assert len(first) == len(second) == 6
    resolved = second.resolve("000660")
    assert resolved.matched is not None
    assert resolved.matched.corp_code == "00164779"


def test_cache_writes_zip_jsonl_and_meta(
    make_dart_client: ClientFactory, corp_code_zip: bytes, tmp_path: Path
) -> None:
    handler, _calls = _counting_zip_handler(corp_code_zip)
    cache_dir = corp_code_cache_dir(tmp_path / "data")

    with make_dart_client(handler) as client:
        load_corp_code_registry(client, cache_dir, refresh_days=7)

    assert (cache_dir / "response.zip").read_bytes() == corp_code_zip
    jsonl_lines = (cache_dir / "corps.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(jsonl_lines) == 6
    meta: Any = json.loads((cache_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["sha256"] == hashlib.sha256(corp_code_zip).hexdigest()
    assert meta["count"] == 6
    assert meta["source"] == "OPEN_DART_CORP_CODE"
    downloaded_at = datetime.fromisoformat(meta["downloaded_at"])
    assert downloaded_at.tzinfo is not None  # KST aware ISO8601


def test_cache_redownloads_after_refresh_days(
    make_dart_client: ClientFactory, corp_code_zip: bytes, tmp_path: Path
) -> None:
    handler, calls = _counting_zip_handler(corp_code_zip)
    cache_dir = corp_code_cache_dir(tmp_path / "data")
    t0 = datetime(2026, 7, 1, 9, 0, tzinfo=KST)

    with make_dart_client(handler) as client:
        load_corp_code_registry(client, cache_dir, refresh_days=7, now=t0)
        # 7일 이내 → 캐시 사용
        load_corp_code_registry(client, cache_dir, refresh_days=7, now=t0 + timedelta(days=6))
        assert len(calls) == 1
        # 7일 초과 → 재다운로드
        load_corp_code_registry(
            client, cache_dir, refresh_days=7, now=t0 + timedelta(days=7, hours=1)
        )
        assert len(calls) == 2


def test_force_refresh_redownloads(
    make_dart_client: ClientFactory, corp_code_zip: bytes, tmp_path: Path
) -> None:
    handler, calls = _counting_zip_handler(corp_code_zip)
    cache_dir = corp_code_cache_dir(tmp_path / "data")

    with make_dart_client(handler) as client:
        load_corp_code_registry(client, cache_dir, refresh_days=7)
        load_corp_code_registry(client, cache_dir, refresh_days=7, force=True)

    assert len(calls) == 2
