"""DART OpenAPI HTTP 클라이언트 — 인증·재시도·오류 매핑 (README §6, §27, §30).

모든 요청 params에 ``crtfc_key``를 자동 주입하고 README §27.2~27.3의 재시도
정책을 구현한다. 예외 메시지·로그 어디에도 인증키 값이 노출되지 않도록
:func:`redact`로 치환한다(README §30.2).
"""

import json
import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from types import TracebackType
from typing import Any, Self, TypeVar

import httpx

from research_backtest.core.constants import DART_BASE_URL
from research_backtest.core.exceptions import DartApiError, DartTransportError

logger = logging.getLogger("r2b.dart")

# ZIP 파일 시그니처 — 오류 응답(XML/JSON)을 ZIP으로 오인하지 않기 위한 검사 (README §19.4)
ZIP_MAGIC = b"PK\x03\x04"

_T = TypeVar("_T")


def redact(text: str, secret: str) -> str:
    """문자열에 포함된 비밀 값을 ``***``로 치환한다 (README §30.2).

    httpx 예외 문자열에는 쿼리(crtfc_key 포함)가 담긴 전체 URL이 들어갈 수
    있으므로, 예외를 감싸 던지기 전에 반드시 이 헬퍼를 거친다.
    """
    if not secret:
        return text
    return text.replace(secret, "***")


def _extract_error_payload(content: bytes) -> tuple[str, str | None]:
    """비-ZIP 오류 본문(XML/JSON)에서 (status, message)를 추출한다 (README §27.1).

    추출에 실패하면 900(정의되지 않은 오류)으로 간주한다.
    """
    text = content.decode("utf-8", errors="replace").strip()
    if text.startswith("{"):
        try:
            payload: Any = json.loads(text)
        except ValueError:
            return "900", None
        if isinstance(payload, dict):
            status = str(payload.get("status") or "900")
            message = payload.get("message")
            return status, str(message) if message is not None else None
        return "900", None
    try:
        # bytes로 파싱해야 XML 인코딩 선언을 처리할 수 있다.
        root = ET.fromstring(content)
    except ET.ParseError:
        return "900", None
    status = (root.findtext(".//status") or "").strip() or "900"
    message = (root.findtext(".//message") or "").strip() or None
    return status, message


class DartClient:
    """OpenDART HTTP 클라이언트 (README §6).

    - base_url: :data:`core.constants.DART_BASE_URL`
    - 재시도 대상(README §27.2): DART 상태 020/800/900, HTTP 429/5xx,
      네트워크 오류(``httpx.TransportError`` — TimeoutException 포함)
    - backoff(README §27.3): i번째 재시도 전 ``backoff_seconds[i]``초 대기,
      최초 시도 이후 최대 ``max_attempts``회 재시도
    - 재시도 소진 시: 마지막 실패가 DART 상태 코드였으면 그 ``DartApiError``를,
      네트워크·HTTP 오류였으면 ``DartTransportError``를 던진다.

    ``transport``·``sleep`` 주입은 테스트용(MockTransport, 즉시 반환 sleep).
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        max_attempts: int = 4,
        backoff_seconds: Sequence[float] = (1.0, 2.0, 4.0, 8.0),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._max_attempts = max_attempts
        self._backoff_seconds = tuple(backoff_seconds)
        self._sleep = sleep
        self._client = httpx.Client(base_url=DART_BASE_URL, timeout=timeout, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """내부 httpx 클라이언트를 닫는다."""
        self._client.close()

    def get_json(self, path: str, **params: str) -> dict[str, Any]:
        """JSON API를 호출한다. ``status``가 000이 아니면 :class:`DartApiError`.

        013/014(조회 데이터 없음 계열)의 처리는 호출부 책임이다 —
        ``err.is_no_data``로 구분한다(README §27.1).
        """
        return self.get_json_text(path, **params)[0]

    def get_json_text(self, path: str, **params: str) -> tuple[dict[str, Any], str]:
        """get_json과 동일한 status 처리 + 응답 원문 텍스트를 함께 반환한다.

        원문 보존(README §8.1 취지)이 필요한 수집기(전체 재무제표 API,
        명세 A2 §3.1)가 재직렬화 없이 응답을 그대로 저장할 수 있게 한다.
        """
        return self._request(path, dict(params), self._parse_json_text)

    def get_bytes(self, path: str, **params: str) -> bytes:
        """바이너리(ZIP) API를 호출한다.

        응답 선두가 ZIP magic이 아니면 오류 본문(XML/JSON)에서 status를 추출해
        :class:`DartApiError`를 던진다 — 오류 응답을 ZIP으로 오인하지 않는다
        (README §19.4 원칙의 선반영).
        """
        return self._request(path, dict(params), self._parse_zip_bytes)

    # --- 내부 구현 ---------------------------------------------------------

    def _request(
        self,
        path: str,
        params: dict[str, str],
        parse: Callable[[httpx.Response], _T],
    ) -> _T:
        """재시도 정책(README §27.2~27.3)을 적용해 GET 요청을 수행한다."""
        last_api_error: DartApiError | None = None
        transport_detail = "요청 미수행"
        total_attempts = self._max_attempts + 1
        for attempt in range(1, total_attempts + 1):
            if attempt > 1:
                self._sleep(self._backoff(attempt - 2))
            try:
                response = self._client.get(path, params={"crtfc_key": self._api_key, **params})
            except httpx.TransportError as exc:  # TimeoutException 포함
                last_api_error = None
                transport_detail = redact(f"{type(exc).__name__}: {exc}", self._api_key)
                logger.warning(
                    "DART 네트워크 오류 path=%s 시도=%d/%d", path, attempt, total_attempts
                )
                continue
            if response.status_code == 429 or response.status_code >= 500:
                last_api_error = None
                transport_detail = f"HTTP {response.status_code}"
                logger.warning(
                    "DART HTTP 오류 %d path=%s 시도=%d/%d",
                    response.status_code,
                    path,
                    attempt,
                    total_attempts,
                )
                continue
            if response.status_code >= 400:
                # 4xx(429 제외)는 재시도해도 무의미 — 즉시 실패
                raise DartTransportError(
                    f"DART HTTP 오류(재시도 불가): {response.status_code}, path={path}"
                )
            try:
                result = parse(response)
            except DartApiError as err:
                if not err.retryable:
                    raise
                last_api_error = err
                logger.warning(
                    "DART 상태 오류 [%s] path=%s 시도=%d/%d",
                    err.status_code,
                    path,
                    attempt,
                    total_attempts,
                )
                continue
            logger.debug("DART 응답 정상 path=%s params=%s", path, params)
            return result
        if last_api_error is not None:
            raise last_api_error
        raise DartTransportError(
            f"DART 요청 재시도 소진(총 {total_attempts}회 시도): "
            f"path={path}, 마지막 오류={transport_detail}"
        )

    def _backoff(self, retry_index: int) -> float:
        if not self._backoff_seconds:
            return 0.0
        return self._backoff_seconds[min(retry_index, len(self._backoff_seconds) - 1)]

    def _parse_json_text(self, response: httpx.Response) -> tuple[dict[str, Any], str]:
        return self._parse_json(response), response.text

    def _parse_json(self, response: httpx.Response) -> dict[str, Any]:
        try:
            payload: Any = response.json()
        except ValueError as err:
            raise DartApiError("900", "JSON 응답 파싱 실패") from err
        if not isinstance(payload, dict):
            raise DartApiError("900", "JSON 응답이 객체가 아님")
        status = str(payload.get("status", ""))
        if status != "000":
            message = payload.get("message")
            raise DartApiError(
                status, redact(str(message), self._api_key) if message is not None else None
            )
        return payload

    def _parse_zip_bytes(self, response: httpx.Response) -> bytes:
        content = response.content
        if content[: len(ZIP_MAGIC)] == ZIP_MAGIC:
            return content
        status, message = _extract_error_payload(content)
        raise DartApiError(status, redact(message, self._api_key) if message is not None else None)
