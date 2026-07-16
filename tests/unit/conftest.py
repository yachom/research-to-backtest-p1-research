"""DART unit 테스트 공용 픽스처 — 네트워크 접근 금지(httpx.MockTransport만 사용)."""

import io
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from research_backtest.core.dart.client import DartClient

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "dart_api"

MockHandler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def corp_code_zip() -> bytes:
    """corpCode 샘플 XML을 인메모리 ZIP으로 포장한다 (ZIP fixture는 파일로 두지 않음)."""
    xml_text = (FIXTURE_DIR / "corp_code_sample.xml").read_text(encoding="utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("CORPCODE.xml", xml_text)
    return buffer.getvalue()


@pytest.fixture
def make_dart_client() -> Callable[..., DartClient]:
    """MockTransport 기반 DartClient 팩토리 — sleep은 즉시 반환(기본)."""

    def _make(handler: MockHandler, *, api_key: str = "unit-test-key", **kwargs: Any) -> DartClient:
        kwargs.setdefault("sleep", lambda _seconds: None)
        return DartClient(api_key, transport=httpx.MockTransport(handler), **kwargs)

    return _make
