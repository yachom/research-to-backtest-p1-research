"""XBRL unit 테스트 픽스처 — 네트워크 금지, ZIP은 인메모리 생성 (명세 §3).

상위 ``tests/unit/conftest.py``의 ``make_dart_client``(MockTransport 팩토리)를
cascade로 재사용한다. 여기서는 XBRL 전용 fixture(instance 바이트·ZIP 빌더·
DartFiling 빌더)만 추가한다.
"""

import io
import zipfile
from collections.abc import Callable, Mapping
from datetime import date
from pathlib import Path

import pytest

from research_backtest.core.constants import PeriodicReportType
from research_backtest.core.dart.models import DartFiling

XBRL_FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "xbrl"

SK_HYNIX_CORP_CODE = "00164779"
SK_HYNIX_STOCK_CODE = "000660"


@pytest.fixture
def xbrl_fixtures_dir() -> Path:
    return XBRL_FIXTURE_DIR


@pytest.fixture
def standard_instance_bytes() -> bytes:
    """fixture ① — 표준+확장 네임스페이스, 전 파서 분기 포함."""
    return (XBRL_FIXTURE_DIR / "fixture_standard.xbrl").read_bytes()


@pytest.fixture
def altprefix_instance_bytes() -> bytes:
    """fixture ② — 같은 uri를 다른 prefix로 선언."""
    return (XBRL_FIXTURE_DIR / "fixture_altprefix.xbrl").read_bytes()


@pytest.fixture
def make_xbrl_zip() -> Callable[[Mapping[str, bytes]], bytes]:
    """{엔트리 이름: 바이트} → 인메모리 ZIP 바이트."""

    def _make(entries: Mapping[str, bytes]) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries.items():
                zf.writestr(name, data)
        return buffer.getvalue()

    return _make


@pytest.fixture
def sample_xbrl_zip(
    make_xbrl_zip: Callable[[Mapping[str, bytes]], bytes],
    standard_instance_bytes: bytes,
) -> bytes:
    """실제 제출과 유사한 ZIP — instance 1개 + 스키마 + 링크베이스(비-instance).

    파일명은 고정 가정하지 않는 실측 규칙(``entity{corp}_{date}.*``)을 흉내낸다.
    """
    schema = b'<?xml version="1.0"?><xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"/>'
    linkbase = (
        b'<?xml version="1.0"?><link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>'
    )
    return make_xbrl_zip(
        {
            "entity00164779_2024-12-31.xbrl": standard_instance_bytes,
            "entity00164779_2024-12-31.xsd": schema,
            "entity00164779_2024-12-31_pre.xml": linkbase,
            "entity00164779_2024-12-31_lab-ko.xml": linkbase,
        }
    )


@pytest.fixture
def make_filing() -> Callable[..., DartFiling]:
    """테스트용 DartFiling 팩토리 (기본: SK하이닉스 사업보고서)."""

    def _make(
        *,
        rcept_no: str = "20250319000665",
        report_type: PeriodicReportType | None = PeriodicReportType.ANNUAL,
        report_nm: str = "사업보고서 (2024.12)",
        corp_code: str = SK_HYNIX_CORP_CODE,
        stock_code: str | None = SK_HYNIX_STOCK_CODE,
        rcept_dt: date = date(2025, 3, 19),
    ) -> DartFiling:
        return DartFiling(
            corp_code=corp_code,
            corp_name="에스케이하이닉스",
            stock_code=stock_code,
            report_nm=report_nm,
            rcept_no=rcept_no,
            flr_nm="에스케이하이닉스",
            rcept_dt=rcept_dt,
            rm=None,
            report_type=report_type,
            fiscal_period_end=date(2024, 12, 31),
            is_correction=False,
            correction_kind=None,
        )

    return _make
