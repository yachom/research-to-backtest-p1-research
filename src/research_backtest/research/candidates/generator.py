"""후보 생성기 — Evidence → CandidateAnalysis·HypothesisCandidate (명세 W3b §2.1).

LLM은 텍스트 정리만 담당하고(docs/AI_ROLE_BOUNDARY.md §1) Python이 만든
Evidence를 재계산하지 않는다. 이 모듈은 세 가지를 보장한다:

1. **결정적 상위 선택** — significance 내림차순으로 상위 evidence만 프롬프트에
   실어(:func:`select_evidence_for_prompt`) 토큰·환각을 억제한다.
2. **evidence_id 봉쇄** — LLM이 내놓은 모든 evidence_id가 프롬프트에 제공한
   부분집합인지 재시도 루프에서 검증한다(1804 §4-2·3의 기계적 강제). 위반
   id는 오류 메시지가 되어 다음 시도의 피드백이 된다.
3. **저작 필드 코드 주입** — :class:`HypothesisCandidate`의 ``generated_by``·
   ``prompt_version``은 LLM 출력에서 받지 않고(있어도 버리고) 코드가
   ``metadata.model``·``"v1"``으로 주입한다(명세 §2.1).

프롬프트는 ``research/prompts/{candidate_analysis,hypothesis_candidate}_v1.txt``
버전 파일이며(과제 2 증빙, docs/HUMAN_IN_THE_LOOP.md §5), 1804 §4 제약 7개를
문면 그대로 담는다.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    CandidateAnalysis,
    Finding,
    HypothesisCandidate,
)
from research_backtest.core.llm.client import LlmCallMetadata, LlmTextClient
from research_backtest.core.llm.json_call import complete_validated
from research_backtest.core.llm.prompts import load_prompt
from research_backtest.quant.strategy.registry import (
    FINANCIAL_INDICATORS,
    FLOW_INDICATORS,
    PRICE_INDICATORS,
)
from research_backtest.research.evidence.models import EvidencePackage, FinancialEvidence

#: 프롬프트 버전 파일 디렉토리 — 이 파일 기준 ``research/prompts/`` (명세 §1, 1804 §12).
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

#: 프롬프트에 싣는 evidence 상한 (명세 W3b §2.1의 60). 지연·출력 길이는 evidence
#: 수 축소가 아니라 **프롬프트의 항목 수 상한**(범주별 최대 3개, 한 문장 서술)으로
#: 제어한다 — 입력을 15건으로 줄이면 CASH_FLOW·STABILITY처럼 유의도 하위 카테고리가
#: 통째로 빠져 risk/conflicting 후보의 근거가 사라진다. 초기 구현이 timeout 120초
#: 제약 아래 15로 낮췄던 실측 이력은 configs/llm.yaml의 360초 상향(2026-07-15,
#: 메인 세션)으로 해소됐다.
DEFAULT_MAX_EVIDENCE = 60

#: 코드가 주입하는 프롬프트 버전(모든 v1 파일 공통). LLM 출력에서 받지 않는다.
PROMPT_VERSION = "v1"

CANDIDATE_ANALYSIS_PROMPT_NAME = "candidate_analysis"
HYPOTHESIS_CANDIDATE_PROMPT_NAME = "hypothesis_candidate"

#: HypothesisCandidate.generated_by가 필수라 검증을 통과시키기 위한 임시값 —
#: complete_validated 반환 후 metadata.model로 덮어쓴다(명세 §2.1).
_PENDING_GENERATED_BY = "__pending__"

_SYSTEM_PROMPT = (
    "너는 한국 주식 리서치를 돕는 보조 도구다. 제공된 재무 Evidence만 근거로 삼아 "
    "사용자가 검토·선택할 후보를 정리한다. 최종 투자 의견을 확정하지 않는다. "
    "반드시 설명·마크다운 코드펜스 없이 유효한 JSON만 출력한다."
)


def select_evidence_for_prompt(
    package: EvidencePackage, *, max_evidence: int = DEFAULT_MAX_EVIDENCE
) -> list[FinancialEvidence]:
    """significance 내림차순, 동률은 evidence_id 오름차순으로 상위 ``max_evidence``건 (명세 §2.1).

    빌더가 이미 같은 키로 정렬해두지만(builder ``evidence.sort``), 이 함수는
    입력 순서에 의존하지 않도록 다시 결정적으로 정렬해 상위를 자른다.
    """
    ordered = sorted(package.evidence, key=lambda e: (-e.significance_score, e.evidence_id))
    return ordered[:max_evidence]


def generate_candidate_analysis(
    package: EvidencePackage,
    *,
    client: LlmTextClient,
    prompts_dir: Path,
    max_attempts: int,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
) -> tuple[CandidateAnalysis, LlmCallMetadata]:
    """Evidence 패키지 → :class:`CandidateAnalysis` (사실·관계 후보·상충 근거, 1804 §4).

    validator는 ``model_validate`` 뒤 모든 evidence_id(Finding 6종 리스트·
    RelationshipCandidate의 evidence/counter)가 프롬프트에 제공한 evidence의
    부분집합인지 검사하고, 위반 id를 오류 메시지로 만들어 재시도 피드백이
    되게 한다(명세 §2.1).
    """
    evidence = select_evidence_for_prompt(package, max_evidence=max_evidence)
    allowed_ids = {e.evidence_id for e in evidence}

    template = load_prompt(prompts_dir, CANDIDATE_ANALYSIS_PROMPT_NAME, 1)
    user_prompt = template.render(
        corp_code=package.corp_code,
        as_of_date=package.as_of_date,
        evidence_json=_serialize_evidence(evidence),
    )

    def validator(payload: object) -> CandidateAnalysis:
        analysis = CandidateAnalysis.model_validate(payload)
        _reject_unknown_ids(_analysis_evidence_ids(analysis), allowed_ids)
        return analysis

    return complete_validated(
        client,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        validator=validator,
        max_attempts=max_attempts,
    )


def generate_hypothesis_candidates(
    package: EvidencePackage,
    analysis: CandidateAnalysis,
    *,
    client: LlmTextClient,
    prompts_dir: Path,
    max_attempts: int,
    max_evidence: int = DEFAULT_MAX_EVIDENCE,
) -> tuple[list[HypothesisCandidate], LlmCallMetadata]:
    """분석 후보 → 참고용 :class:`HypothesisCandidate` 목록 1~5개 (1804 §7).

    LLM 출력에서 ``generated_by``·``prompt_version``은 버리고(있어도 무시),
    반환 직전에 코드가 ``metadata.model``·``"v1"``으로 주입한다(명세 §2.1).
    evidence_id/counter_evidence_id 실존 검증은 분석과 동일하다.
    ``measurable_variables``는 검증하지 않되(참고용 후보) 프롬프트에 A5 지원
    지표 목록을 제공해 유도한다.
    """
    evidence = select_evidence_for_prompt(package, max_evidence=max_evidence)
    allowed_ids = {e.evidence_id for e in evidence}

    template = load_prompt(prompts_dir, HYPOTHESIS_CANDIDATE_PROMPT_NAME, 1)
    user_prompt = template.render(
        corp_code=package.corp_code,
        as_of_date=package.as_of_date,
        evidence_json=_serialize_evidence(evidence),
        analysis_json=analysis.model_dump_json(indent=2),
        indicator_list=_render_indicator_list(),
    )

    def validator(payload: object) -> list[HypothesisCandidate]:
        candidates = _validate_candidate_list(payload)
        referenced: set[str] = set()
        for candidate in candidates:
            referenced.update(candidate.evidence_ids)
            referenced.update(candidate.counter_evidence_ids)
        _reject_unknown_ids(referenced, allowed_ids)
        return candidates

    candidates, metadata = complete_validated(
        client,
        system_prompt=_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        validator=validator,
        max_attempts=max_attempts,
    )
    injected = [
        candidate.model_copy(
            update={"generated_by": metadata.model, "prompt_version": PROMPT_VERSION}
        )
        for candidate in candidates
    ]
    return injected, metadata


# --- validator 보조 (결정적·순수) -------------------------------------------


def _validate_candidate_list(payload: object) -> list[HypothesisCandidate]:
    """최상위 list(1~5개)를 HypothesisCandidate로 검증한다 (저작 필드는 임시 주입).

    ``generated_by``·``prompt_version``은 LLM이 넣었든 안 넣었든 버리고 임시값을
    주입한다 — 최종값은 호출자가 metadata로 덮어쓴다(명세 §2.1).
    """
    if not isinstance(payload, list):
        raise DataValidationError("가설 후보의 최상위 타입은 JSON 배열(list)이어야 합니다.")
    if not 1 <= len(payload) <= 5:
        raise DataValidationError(
            f"가설 후보는 1~5개여야 합니다(사용자 선택용 복수 후보): {len(payload)}개."
        )
    candidates: list[HypothesisCandidate] = []
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise DataValidationError(f"{index}번째 가설 후보가 JSON 객체가 아닙니다.")
        data = dict(item)
        data.pop("generated_by", None)
        data.pop("prompt_version", None)
        data["generated_by"] = _PENDING_GENERATED_BY
        data["prompt_version"] = PROMPT_VERSION
        candidates.append(HypothesisCandidate.model_validate(data))
    return candidates


def _analysis_evidence_ids(analysis: CandidateAnalysis) -> set[str]:
    """CandidateAnalysis가 참조하는 모든 evidence_id를 모은다 (Finding 6종 + 관계 후보)."""
    finding_lists: tuple[list[Finding], ...] = (
        analysis.financial_findings,
        analysis.business_findings,
        analysis.industry_findings,
        analysis.catalyst_candidates,
        analysis.risk_candidates,
        analysis.conflicting_evidence,
    )
    referenced: set[str] = set()
    for findings in finding_lists:
        for finding in findings:
            referenced.update(finding.evidence_ids)
    for relationship in analysis.relationship_candidates:
        referenced.update(relationship.evidence_ids)
        referenced.update(relationship.counter_evidence_ids)
    return referenced


def _reject_unknown_ids(referenced: set[str], allowed_ids: set[str]) -> None:
    """제공한 evidence 밖의 id가 있으면 위반 목록을 오류로 던진다 (재시도 피드백, 명세 §2.1)."""
    unknown = referenced - allowed_ids
    if unknown:
        raise DataValidationError(
            "제공된 evidence 목록에 없는 evidence_id를 사용했습니다: "
            f"{sorted(unknown)}. 반드시 제공된 evidence의 evidence_id만 인용하라."
        )


# --- 프롬프트 입력 직렬화 (결정적·순수) --------------------------------------


def _serialize_evidence(evidence: list[FinancialEvidence]) -> str:
    """Evidence 리스트를 프롬프트용 JSON 문자열로 직렬화한다 (Decimal→str, 명세 §2.1)."""
    return json.dumps(
        [item.model_dump(mode="json") for item in evidence],
        ensure_ascii=False,
        indent=2,
    )


def _render_indicator_list() -> str:
    """A5 Indicator Registry의 지원 지표 3분류를 프롬프트용 문자열로 렌더한다 (명세 §2.1).

    ``RelationshipCandidate``/``HypothesisCandidate``의 ``measurable_variables``
    후보를 유도하기 위한 화이트리스트다. 값은 프롬프트 플레이스홀더로 주입되며
    (템플릿 리터럴이 아니라) str.format 재해석 대상이 아니다.
    """
    lines = [
        f"- 재무 지표(FINANCIAL): {', '.join(sorted(FINANCIAL_INDICATORS))}",
        f"- 가격 지표(PRICE): {', '.join(sorted(PRICE_INDICATORS))}",
        f"- 수급 지표(FLOW): {', '.join(sorted(FLOW_INDICATORS))}",
    ]
    return "\n".join(lines)


__all__ = [
    "CANDIDATE_ANALYSIS_PROMPT_NAME",
    "HYPOTHESIS_CANDIDATE_PROMPT_NAME",
    "PROMPTS_DIR",
    "PROMPT_VERSION",
    "generate_candidate_analysis",
    "generate_hypothesis_candidates",
    "select_evidence_for_prompt",
]
