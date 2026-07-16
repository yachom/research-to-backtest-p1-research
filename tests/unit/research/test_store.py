"""EvidencePackageStore 저장/로드·매니페스트 호환 테스트 (명세 W3a-E1 §3.5 DoD).

핵심: evidence_manifest.json이 core.hitl의 FileEvidenceStore.from_manifest로 실제
로드되어야 한다(H1 승인 게이트가 이 매니페스트로 근거 실존을 검증하기 때문).
"""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.validation import FileEvidenceStore
from research_backtest.research.evidence import (
    EvidencePackage,
    EvidencePackageStore,
    FinancialEvidence,
)
from research_backtest.research.evidence.store import MANIFEST_FILENAME, PACKAGE_FILENAME


def _package() -> EvidencePackage:
    return EvidencePackage(
        corp_code="00164779",
        as_of_date="2025-12-31",
        lookback_years=5,
        fs_scope="CFS",
        generated_at="2026-01-01T00:00:00+09:00",
        evidence=[
            FinancialEvidence(
                evidence_id="FIN_OP_MARGIN_2025Q3",
                category="PROFITABILITY",
                statement="영업이익률 개선.",
                current_value=Decimal("0.284"),
                comparison_value=Decimal("0.176"),
                change_rate=0.108,
                period="2025Q3",
                comparison_period="2024Q3",
                source_fact_ids=["FACT_operating_margin_CFS_2025Q3"],
                rcept_no="20251114001234",
                filing_date="2025-11-14",
                significance_score=0.91,
                fs_scope="CFS",
                available_from="2025-11-17",
            ),
            FinancialEvidence(
                evidence_id="FIN_OP_INCOME_TURN_FY2024",
                category="PROFITABILITY",
                statement="영업이익 흑자 전환.",
                current_value=Decimal(80),
                comparison_value=Decimal(-50),
                change_rate=2.6,
                period="FY2024",
                comparison_period="FY2023",
                source_fact_ids=["FACT_operating_income_CFS_2024QA"],
                rcept_no="20250319000684",
                filing_date="2025-03-19",
                significance_score=0.88,
                fs_scope="CFS",
                available_from="2025-03-20",
            ),
        ],
    )


def test_save_writes_both_files_and_roundtrips(tmp_path: Path) -> None:
    pkg = _package()
    store = EvidencePackageStore(tmp_path / "run1")
    package_path, manifest_path = store.save(pkg)
    assert package_path.name == PACKAGE_FILENAME and package_path.exists()
    assert manifest_path.name == MANIFEST_FILENAME and manifest_path.exists()
    loaded = store.load()
    assert loaded == pkg  # Decimal 포함 왕복 동일


def test_manifest_loads_via_core_hitl_file_evidence_store(tmp_path: Path) -> None:
    pkg = _package()
    store = EvidencePackageStore(tmp_path / "run1")
    _, manifest_path = store.save(pkg)
    file_store = FileEvidenceStore.from_manifest(manifest_path)
    assert file_store.has_evidence("FIN_OP_MARGIN_2025Q3")
    assert file_store.has_evidence("FIN_OP_INCOME_TURN_FY2024")
    assert not file_store.has_evidence("FIN_DOES_NOT_EXIST_2099Q9")


def test_manifest_shape_is_summary(tmp_path: Path) -> None:
    pkg = _package()
    store = EvidencePackageStore(tmp_path / "run1")
    _, manifest_path = store.save(pkg)
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert set(raw.keys()) == {"evidence"}
    first = raw["evidence"][0]
    assert set(first.keys()) == {"evidence_id", "category", "statement", "significance_score"}


def test_load_missing_package_raises(tmp_path: Path) -> None:
    with pytest.raises(DataValidationError):
        EvidencePackageStore(tmp_path / "empty").load()
