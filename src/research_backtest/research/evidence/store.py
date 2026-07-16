"""Evidence 패키지 영속화 — evidence_package.json + evidence_manifest.json (명세 W3a-E1 §3.5).

두 파일을 쓴다:

- ``evidence_package.json`` — :class:`EvidencePackage` 전문(직렬화). :meth:`load`가 되읽는다.
- ``evidence_manifest.json`` — 사용자 브라우징·게이트 검증용 요약. 형식은
  :class:`core.hitl.validation.FileEvidenceStore` ``from_manifest``가 읽는 형식과
  **호환**된다: ``{"evidence": [{"evidence_id": ..., ...}]}``. from_manifest는
  ``evidence_id``만 사용하고 나머지 필드(category·statement·significance_score)는
  무시하므로, 요약 정보를 함께 실어 브라우징에 활용한다.

run_dir 결합·CLI 연결(outputs/{run_id}/)은 Wave 3b(C1'-gen)가 담당한다 — 이 계층은
경로 하나를 받아 저장·로드만 한다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from research_backtest.core.exceptions import DataValidationError
from research_backtest.research.evidence.models import EvidencePackage

PACKAGE_FILENAME = "evidence_package.json"
MANIFEST_FILENAME = "evidence_manifest.json"


class EvidencePackageStore:
    """``run_dir`` 하나에 Evidence 패키지·매니페스트를 저장/로드한다."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    @property
    def package_path(self) -> Path:
        return self.run_dir / PACKAGE_FILENAME

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / MANIFEST_FILENAME

    def save(self, package: EvidencePackage) -> tuple[Path, Path]:
        """evidence_package.json + evidence_manifest.json을 저장한다.

        원자적 개념(둘 다 쓰거나 예외): 두 파일의 바이트를 **먼저 전부 직렬화**한
        뒤(여기서 실패하면 아무 것도 쓰지 않음) 각각 임시 파일에 쓰고 원자적
        rename(:func:`os.replace`)한다.
        """
        package_bytes = (package.model_dump_json(indent=2) + "\n").encode("utf-8")
        manifest = {
            "evidence": [
                {
                    "evidence_id": e.evidence_id,
                    "category": e.category,
                    "statement": e.statement,
                    "significance_score": e.significance_score,
                }
                for e in package.evidence
            ]
        }
        manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

        self.run_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.package_path, package_bytes)
        _atomic_write(self.manifest_path, manifest_bytes)
        return self.package_path, self.manifest_path

    def load(self) -> EvidencePackage:
        """evidence_package.json을 :class:`EvidencePackage`로 되읽는다."""
        if not self.package_path.exists():
            raise DataValidationError(
                f"Evidence 패키지가 없습니다: {self.package_path}. "
                "generate-candidates(C1') 또는 Evidence 빌드를 먼저 실행하세요."
            )
        raw = self.package_path.read_text(encoding="utf-8")
        return EvidencePackage.model_validate_json(raw)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


__all__ = ["MANIFEST_FILENAME", "PACKAGE_FILENAME", "EvidencePackageStore"]
