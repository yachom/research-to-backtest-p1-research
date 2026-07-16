"""DartClient 단위 테스트 — 인증·재시도·오류 매핑 (README §27, §30, 명세 A1 §1)."""

from collections.abc import Callable

import httpx
import pytest

from research_backtest.core.dart.client import DartClient, redact
from research_backtest.core.exceptions import DartApiError, DartTransportError

ClientFactory = Callable[..., DartClient]


def test_redact_replaces_secret_with_stars() -> None:
    assert redact("key=SECRET123&x=1", "SECRET123") == "key=***&x=1"
    assert redact("no secret here", "") == "no secret here"


def test_get_json_injects_crtfc_key_and_returns_payload(make_dart_client: ClientFactory) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "000", "message": "정상", "list": []})

    with make_dart_client(handler) as client:
        payload = client.get_json("list.json", corp_code="00164779")

    assert payload["status"] == "000"
    assert len(seen) == 1
    assert seen[0].url.path == "/api/list.json"
    assert seen[0].url.params["crtfc_key"] == "unit-test-key"
    assert seen[0].url.params["corp_code"] == "00164779"


def test_no_data_status_raises_immediately_with_flag(make_dart_client: ClientFactory) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

    with make_dart_client(handler) as client, pytest.raises(DartApiError) as excinfo:
        client.get_json("list.json")

    assert excinfo.value.is_no_data
    assert calls == 1  # 013은 재시도 대상이 아니다 (README §27.2)


def test_retryable_status_is_retried_until_success(make_dart_client: ClientFactory) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json={"status": "020", "message": "요청 제한 초과"})
        return httpx.Response(200, json={"status": "000", "message": "정상"})

    with make_dart_client(handler, sleep=sleeps.append) as client:
        payload = client.get_json("list.json")

    assert payload["status"] == "000"
    assert calls == 2
    assert sleeps == [1.0]  # backoff 첫 단계 (README §27.3)


def test_fatal_status_fails_immediately_without_retry(make_dart_client: ClientFactory) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "010", "message": "등록되지 않은 키입니다."})

    with make_dart_client(handler) as client, pytest.raises(DartApiError) as excinfo:
        client.get_json("list.json")

    assert excinfo.value.status_code == "010"
    assert calls == 1


def test_retryable_status_exhaustion_raises_last_dart_api_error(
    make_dart_client: ClientFactory,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"status": "800", "message": "시스템 점검"})

    with make_dart_client(handler) as client, pytest.raises(DartApiError) as excinfo:
        client.get_json("list.json")

    assert excinfo.value.status_code == "800"
    assert calls == 5  # 최초 1회 + 재시도 4회 (README §27.3)


def test_http_5xx_is_retried_then_succeeds(make_dart_client: ClientFactory) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 2:
            return httpx.Response(500)
        return httpx.Response(200, json={"status": "000", "message": "정상"})

    with make_dart_client(handler, sleep=sleeps.append) as client:
        payload = client.get_json("list.json")

    assert payload["status"] == "000"
    assert calls == 3
    assert sleeps == [1.0, 2.0]


def test_http_429_is_retried(make_dart_client: ClientFactory) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429)
        return httpx.Response(200, json={"status": "000", "message": "정상"})

    with make_dart_client(handler) as client:
        assert client.get_json("list.json")["status"] == "000"

    assert calls == 2


def test_http_error_exhaustion_raises_transport_error(make_dart_client: ClientFactory) -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    with (
        make_dart_client(handler, sleep=sleeps.append) as client,
        pytest.raises(DartTransportError),
    ):
        client.get_json("list.json")

    assert calls == 5
    assert sleeps == [1.0, 2.0, 4.0, 8.0]


def test_network_timeout_is_retried_then_raises_transport_error(
    make_dart_client: ClientFactory,
) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("응답 시간 초과")

    with make_dart_client(handler) as client, pytest.raises(DartTransportError):
        client.get_json("list.json")

    assert calls == 5


def test_get_json_text_returns_payload_and_raw_body(make_dart_client: ClientFactory) -> None:
    body = '{"status": "000", "message": "정상", "list": [{"account_nm": "자산총계"}]}'

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode("utf-8"), headers={"Content-Type": "application/json"}
        )

    with make_dart_client(handler) as client:
        payload, text = client.get_json_text("fnlttSinglAcntAll.json")

    assert payload["status"] == "000"
    assert text == body  # 원문 텍스트 그대로 — 재직렬화 없음 (명세 A2 §3.1)


def test_get_json_text_raises_on_error_status_like_get_json(
    make_dart_client: ClientFactory,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "013", "message": "조회된 데이타가 없습니다."})

    with make_dart_client(handler) as client, pytest.raises(DartApiError) as excinfo:
        client.get_json_text("fnlttSinglAcntAll.json")

    assert excinfo.value.is_no_data


def test_get_bytes_returns_zip_payload(make_dart_client: ClientFactory) -> None:
    zip_bytes = b"PK\x03\x04" + b"\x00" * 16

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=zip_bytes)

    with make_dart_client(handler) as client:
        assert client.get_bytes("corpCode.xml") == zip_bytes


def test_get_bytes_maps_error_xml_to_dart_api_error(make_dart_client: ClientFactory) -> None:
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<result><status>013</status><message>조회된 데이타가 없습니다.</message></result>"
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=body.encode("utf-8"))

    with make_dart_client(handler) as client, pytest.raises(DartApiError) as excinfo:
        client.get_bytes("corpCode.xml")

    assert excinfo.value.status_code == "013"
    assert excinfo.value.is_no_data
    assert calls == 1


def test_exception_messages_never_contain_api_key(make_dart_client: ClientFactory) -> None:
    secret = "SECRET-API-KEY-123"

    def handler(request: httpx.Request) -> httpx.Response:
        # httpx 예외 문자열에 crtfc_key가 담긴 전체 URL이 포함되는 상황을 재현
        raise httpx.ConnectError(f"connection failed for {request.url}")

    with (
        make_dart_client(handler, api_key=secret) as client,
        pytest.raises(DartTransportError) as excinfo,
    ):
        client.get_bytes("corpCode.xml")

    message = str(excinfo.value)
    assert secret not in message  # README §30.2 — 키 값 노출 금지
    assert "***" in message
