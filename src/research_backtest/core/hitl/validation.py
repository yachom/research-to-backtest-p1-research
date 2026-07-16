"""AnalystView·HumanInvestmentHypothesis 외부 참조 검증 (H1 §5).

pydantic 모델 validator(models.py)가 구조적 제약(비어있지 않음, 최소 개수,
선택∩제외=∅ 등)을 담당하는 반면, 이 모듈은 **모델 밖의 자료**(Evidence Store,
Indicator Registry가 지원하는 변수 목록)와 대조해야만 판정할 수 있는 규칙을
담당한다.
"""

import json
from collections.abc import Collection
from pathlib import Path
from typing import Protocol

from pydantic import ValidationError as PydanticValidationError

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    AnalystView,
    HumanInvestmentHypothesis,
    HypothesisStatus,
    now_kst_iso,
)


class EvidenceStore(Protocol):
    """근거 ID 실존 여부만 답하는 최소 인터페이스."""

    def has_evidence(self, evidence_id: str) -> bool: ...


class FileEvidenceStore:
    """``outputs/{run_id}/evidence_manifest.json`` 기반 :class:`EvidenceStore` 구현체.

    manifest 형식: ``{"evidence": [{"evidence_id": "..."}, ...]}`` (evidence_id
    이외의 필드가 있어도 무시한다 — 실데이터 생성은 C1'의 책임이며 H1은 존재
    여부만 사용한다).
    """

    def __init__(self, evidence_ids: Collection[str]) -> None:
        self._evidence_ids = frozenset(evidence_ids)

    @classmethod
    def from_manifest(cls, path: Path) -> "FileEvidenceStore":
        if not path.exists():
            raise DataValidationError(
                f"Evidence manifest가 없습니다: {path}. "
                "데이터 수집과 AI 분석 후보 생성(generate-candidates) 단계를 "
                "먼저 실행해 evidence_manifest.json을 생성하세요."
            )
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            evidence_ids = [item["evidence_id"] for item in raw["evidence"]]
        except (KeyError, TypeError, json.JSONDecodeError) as err:
            raise DataValidationError(
                f"Evidence manifest 형식이 올바르지 않습니다: {path} "
                '(예상 형식: {"evidence": [{"evidence_id": "..."}, ...]})'
            ) from err
        return cls(evidence_ids)

    def has_evidence(self, evidence_id: str) -> bool:
        return evidence_id in self._evidence_ids


def validate_analyst_view(view: AnalystView, store: EvidenceStore) -> None:
    """OUTPUT_SCHEMA §2 규칙 전부(모델 자체 제약 + evidence 실존)를 검사한다.

    ``view``는 이미 pydantic으로 구성된 :class:`AnalystView`이므로 구조적
    제약은 보통 생성 시점에 강제되지만, ``model_construct`` 등으로 우회된
    인스턴스에도 대비해 재검증하고, evidence 실존 여부와 함께 위반을 모아
    하나의 :class:`DataValidationError`로 보고한다.
    """
    errors: list[str] = []
    try:
        AnalystView.model_validate(view.model_dump(mode="json"))
    except PydanticValidationError as err:
        errors.append(f"AnalystView 모델 제약 위반: {err}")

    missing = sorted(eid for eid in view.selected_evidence_ids if not store.has_evidence(eid))
    if missing:
        errors.append("선택한 근거 ID가 Evidence Store에 존재하지 않습니다: " + ", ".join(missing))

    if errors:
        raise DataValidationError(" / ".join(errors))


def validate_hypothesis(
    hypothesis: HumanInvestmentHypothesis,
    store: EvidenceStore,
    supported_variables: Collection[str],
) -> None:
    """가설의 외부 참조(Evidence 실존, AnalystView 연결, 변수 지원 여부)를 검사한다.

    ``supported_variables``는 호출부가 전달하는 A5 Indicator Registry의
    지원 변수 목록이다 — H1은 A5와 직접 통합하지 않는다(통합은 메인 세션).
    ``view_id``가 실제 :class:`AnalystView` 객체를 가리키는지 대조하는 일도
    호출부의 책임이다(이 함수는 비어있지 않은지만 확인한다).
    """
    errors: list[str] = []

    missing_evidence = sorted(eid for eid in hypothesis.evidence_ids if not store.has_evidence(eid))
    if missing_evidence:
        errors.append(
            "근거 ID가 Evidence Store에 존재하지 않습니다: " + ", ".join(missing_evidence)
        )

    if not hypothesis.view_id.strip():
        errors.append("가설은 AnalystView(view_id)와 연결되어야 합니다 — view_id가 비어 있습니다.")

    allowed_variables = set(supported_variables) | set(hypothesis.unsupported_variables)
    unsupported = sorted(v for v in hypothesis.selected_variables if v not in allowed_variables)
    if unsupported:
        errors.append(
            "Indicator Registry가 지원하지 않고 unsupported_variables에도 "
            "명시되지 않은 변수입니다: " + ", ".join(unsupported)
        )

    if errors:
        raise DataValidationError(" / ".join(errors))


def approve_hypothesis(
    hypothesis: HumanInvestmentHypothesis,
    *,
    approved_by: str,
    now: str | None = None,
) -> HumanInvestmentHypothesis:
    """검증 후 ``status=APPROVED``·``approved_by``·``approved_at``을 채운 사본을 반환한다.

    원본 ``hypothesis``는 변경하지 않는다. 새 payload를
    ``HumanInvestmentHypothesis.model_validate``로 다시 검증해 반환값이
    항상 전체 모델 제약(falsification_conditions ≥ 1, content_origin 허용값
    등)을 만족함을 보장한다.
    """
    if not approved_by.strip():
        raise DataValidationError("approve_hypothesis: approved_by는 비어 있을 수 없습니다.")

    timestamp = now if now is not None else now_kst_iso()
    payload = hypothesis.model_dump(mode="json")
    payload.update(
        status=HypothesisStatus.APPROVED.value,
        approved_by=approved_by,
        approved_at=timestamp,
        updated_at=timestamp,
    )
    return HumanInvestmentHypothesis.model_validate(payload)


__all__ = [
    "EvidenceStore",
    "FileEvidenceStore",
    "approve_hypothesis",
    "validate_analyst_view",
    "validate_hypothesis",
]
