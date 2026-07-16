"""실데이터 Evidence 빌드 integration 테스트 (명세 W3a-E1 §3.7 DoD).

실행: ``DATA_DIR=/…/data pytest -m integration tests/integration/test_evidence_build.py``
(API 호출 없음 — A4 산출 parquet만 읽는다. DATA_DIR만 있으면 된다).

DoD: SK하이닉스 as_of=2025-12-31 evidence ≥ 20건·전건 PIT 준수, as_of=2023-06-30
재실행 시 미래 공시 evidence 0건.
"""

from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from research_backtest.core.config import get_settings
from research_backtest.core.financials.pipeline import METRICS_FILENAME, financials_out_dir
from research_backtest.core.hitl.validation import FileEvidenceStore
from research_backtest.research.evidence import EvidencePackageStore, build_financial_evidence

pytestmark = pytest.mark.integration

SK_HYNIX = "00164779"
_FIXED = datetime(2026, 7, 15, 12, 0, 0)


@pytest.fixture(scope="module")
def data_dir() -> Path:
    dd = get_settings().data_dir
    if not (financials_out_dir(dd, SK_HYNIX) / METRICS_FILENAME).exists():
        pytest.skip(f"실데이터 없음(financial_metrics.parquet) — DATA_DIR 확인: {dd}")
    return dd


def test_as_of_2025_at_least_20_evidence_all_pit_safe(data_dir: Path) -> None:
    as_of = date(2025, 12, 31)
    pkg = build_financial_evidence(SK_HYNIX, as_of=as_of, data_dir=data_dir, now=_FIXED)
    assert len(pkg.evidence) >= 20
    # 전 건 PIT 준수: available_from <= as_of
    assert all(e.available_from <= as_of.isoformat() for e in pkg.evidence)
    # 카테고리 분포(보고용) — 여러 카테고리가 채워져야 한다
    dist = Counter(e.category for e in pkg.evidence)
    assert len(dist) >= 4, dist
    # README §18.2 형태의 영업이익률 evidence가 존재
    assert any(e.evidence_id.startswith("FIN_OP_MARGIN_") for e in pkg.evidence)
    # 흑자 전환 서사(2023 적자 → 2024 흑자)가 포착된다
    assert any("TURN" in e.evidence_id for e in pkg.evidence)


def test_as_of_2023_midpoint_no_future_disclosure(data_dir: Path) -> None:
    as_of = date(2023, 6, 30)
    pkg = build_financial_evidence(SK_HYNIX, as_of=as_of, data_dir=data_dir, now=_FIXED)
    assert len(pkg.evidence) > 0  # 2021~2023 데이터는 존재
    future = [e.evidence_id for e in pkg.evidence if e.available_from > as_of.isoformat()]
    assert future == [], f"미래 공시 evidence 유출: {future}"
    # 재무값 근거일도 전부 기준일 이하
    assert all(e.filing_date <= as_of.isoformat() for e in pkg.evidence)


def test_store_roundtrip_and_manifest_gate_compatible(data_dir: Path, tmp_path: Path) -> None:
    pkg = build_financial_evidence(
        SK_HYNIX, as_of=date(2025, 12, 31), data_dir=data_dir, now=_FIXED
    )
    store = EvidencePackageStore(tmp_path / "run")
    _, manifest_path = store.save(pkg)
    assert store.load() == pkg
    file_store = FileEvidenceStore.from_manifest(manifest_path)
    assert all(file_store.has_evidence(e.evidence_id) for e in pkg.evidence)


def test_deterministic_on_real_data(data_dir: Path) -> None:
    a = build_financial_evidence(SK_HYNIX, as_of=date(2025, 12, 31), data_dir=data_dir, now=_FIXED)
    b = build_financial_evidence(SK_HYNIX, as_of=date(2025, 12, 31), data_dir=data_dir, now=_FIXED)
    assert a.model_dump_json() == b.model_dump_json()
