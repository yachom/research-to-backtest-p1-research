"""``outputs/{run_id}/`` 산출물 저장소 (H1 §6).

경로 규약: ``outputs/{run_id}/{파일명}`` — 파일명은 docs/OUTPUT_SCHEMA.md §0과
정확히 일치한다.

**AI 참고용 후보와 사용자 최종 가설은 파일부터 분리되어 있다** (원문 §7,
docs/AI_ROLE_BOUNDARY.md §3): AI가 생성하는 ``hypothesis_candidates.json``
(``list[HypothesisCandidate]``)과 사용자가 작성·승인하는
``human_investment_hypothesis.json``(``HumanInvestmentHypothesis``)은 서로
다른 모델·다른 파일이다. 이 둘을 하나의 파일이나 모델로 합치면 "AI가 최종
투자 가설을 자율적으로 확정하면 안 된다"는 강제 장치가 깨진다 — 이 저장소는
애초에 두 산출물을 위한 별도 save/load 쌍만 제공해 그 실수를 구조적으로
막는다.
"""

import json
from collections.abc import Mapping
from pathlib import Path

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import (
    AIUsageRecord,
    AnalystView,
    BacktestInterpretation,
    CandidateAnalysis,
    HumanInvestmentHypothesis,
    HypothesisCandidate,
    RunManifest,
    StrategyReview,
)
from research_backtest.core.hitl.states import RunState

AI_USAGE_LOG_FILENAME = "ai_usage_log.jsonl"
RUN_MANIFEST_FILENAME = "run_manifest.json"


class RunStore:
    """``outputs/{run_id}/`` 산출물의 save/load + ``ai_usage_log.jsonl`` append."""

    def __init__(self, outputs_dir: Path, run_id: str) -> None:
        self.outputs_dir = outputs_dir
        self.run_id = run_id

    @property
    def run_dir(self) -> Path:
        return self.outputs_dir / self.run_id

    def _path(self, filename: str) -> Path:
        return self.run_dir / filename

    def _write_json(self, filename: str, payload: object) -> Path:
        path = self._path(filename)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _read_json(self, filename: str, *, next_step_hint: str) -> object:
        path = self._path(filename)
        if not path.exists():
            raise DataValidationError(f"{filename}이(가) 없습니다 ({path}). {next_step_hint}")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as err:
            raise DataValidationError(f"{filename}이(가) 올바른 JSON이 아닙니다 ({path}).") from err

    # -- run_manifest (불변 실행 메타 — docs/specs/CLI-integration.md §5.0) -------

    def save_run_manifest(self, manifest: RunManifest) -> Path:
        return self._write_json(RUN_MANIFEST_FILENAME, manifest.model_dump(mode="json"))

    def load_run_manifest(self) -> RunManifest:
        data = self._read_json(
            RUN_MANIFEST_FILENAME,
            next_step_hint="create-run으로 실행을 먼저 등록하세요.",
        )
        return RunManifest.model_validate(data)

    # -- run_state ---------------------------------------------------------

    def save_run_state(self, state: RunState) -> Path:
        return self._write_json("run_state.json", state.model_dump(mode="json"))

    def load_run_state(self) -> RunState:
        data = self._read_json(
            "run_state.json",
            next_step_hint="states.create_run_state()로 실행을 먼저 등록하세요.",
        )
        return RunState.model_validate(data)

    # -- candidate_analysis (AI) --------------------------------------------

    def save_candidate_analysis(self, analysis: CandidateAnalysis) -> Path:
        return self._write_json("candidate_analysis.json", analysis.model_dump(mode="json"))

    def load_candidate_analysis(self) -> CandidateAnalysis:
        data = self._read_json(
            "candidate_analysis.json",
            next_step_hint="AI 분석 후보 생성 단계(generate-candidates)를 먼저 실행하세요.",
        )
        return CandidateAnalysis.model_validate(data)

    # -- analyst_view (사용자) ------------------------------------------------

    def save_analyst_view(self, view: AnalystView) -> Path:
        return self._write_json("analyst_view.json", view.model_dump(mode="json"))

    def load_analyst_view(self) -> AnalystView:
        data = self._read_json(
            "analyst_view.json",
            next_step_hint="분석 관점 작성 단계(create-analyst-view)를 먼저 실행하세요.",
        )
        return AnalystView.model_validate(data)

    # -- hypothesis_candidates (AI 참고용 — human_hypothesis와 별도 파일) -------------

    def save_hypothesis_candidates(self, candidates: list[HypothesisCandidate]) -> Path:
        return self._write_json(
            "hypothesis_candidates.json",
            [candidate.model_dump(mode="json") for candidate in candidates],
        )

    def load_hypothesis_candidates(self) -> list[HypothesisCandidate]:
        data = self._read_json(
            "hypothesis_candidates.json",
            next_step_hint="AI 가설 후보 생성 단계를 먼저 실행하세요.",
        )
        if not isinstance(data, list):
            raise DataValidationError("hypothesis_candidates.json의 최상위 타입은 list여야 합니다.")
        return [HypothesisCandidate.model_validate(item) for item in data]

    # -- human_investment_hypothesis (사용자 최종 가설 — AI 후보와 별도 파일) ----------

    def save_human_hypothesis(self, hypothesis: HumanInvestmentHypothesis) -> Path:
        return self._write_json(
            "human_investment_hypothesis.json", hypothesis.model_dump(mode="json")
        )

    def load_human_hypothesis(self) -> HumanInvestmentHypothesis:
        data = self._read_json(
            "human_investment_hypothesis.json",
            next_step_hint="투자 가설 작성 단계(create-hypothesis)를 먼저 실행하세요.",
        )
        return HumanInvestmentHypothesis.model_validate(data)

    # -- strategy_draft (AI 초안 — dict, StrategySpec 결합은 통합 단계) ----------------

    def save_strategy_draft(self, draft: Mapping[str, object]) -> Path:
        return self._write_json("strategy_draft.json", dict(draft))

    def load_strategy_draft(self) -> dict[str, object]:
        data = self._read_json(
            "strategy_draft.json",
            next_step_hint="전략 초안 생성 단계(generate-strategy-draft)를 먼저 실행하세요.",
        )
        if not isinstance(data, dict):
            raise DataValidationError("strategy_draft.json의 최상위 타입은 dict여야 합니다.")
        return data

    # -- strategy_review (사용자 승인 + 수정 이력) -------------------------------

    def save_strategy_review(self, review: StrategyReview) -> Path:
        return self._write_json("strategy_review.json", review.model_dump(mode="json"))

    def load_strategy_review(self) -> StrategyReview:
        data = self._read_json(
            "strategy_review.json",
            next_step_hint="전략 검토·승인 단계(approve-strategy)를 먼저 실행하세요.",
        )
        return StrategyReview.model_validate(data)

    # -- backtest_interpretation (사용자 최종 해석) -------------------------------

    def save_backtest_interpretation(self, interpretation: BacktestInterpretation) -> Path:
        return self._write_json(
            "backtest_interpretation.json", interpretation.model_dump(mode="json")
        )

    def load_backtest_interpretation(self) -> BacktestInterpretation:
        data = self._read_json(
            "backtest_interpretation.json",
            next_step_hint="결과 해석 제출 단계(submit-interpretation)를 먼저 실행하세요.",
        )
        return BacktestInterpretation.model_validate(data)

    # -- ai_usage_log.jsonl (append-only, 과제 2 증빙) ----------------------------

    def append_ai_usage(self, record: AIUsageRecord) -> Path:
        """AIUsageRecord 1건을 ``ai_usage_log.jsonl``에 한 줄로 append한다.

        JSONL 포맷을 유지하기 위해(레코드당 정확히 한 줄) 이 메서드만은
        ``indent`` 없이 압축된 한 줄 JSON을 쓴다 — 다른 산출물의
        ``indent=2`` pretty-print와는 의도적으로 다르다.
        """
        path = self._path(AI_USAGE_LOG_FILENAME)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record.model_dump(mode="json"), ensure_ascii=False)
        with path.open("a", encoding="utf-8") as fp:
            fp.write(line + "\n")
        return path

    def load_ai_usage_log(self) -> list[AIUsageRecord]:
        """``ai_usage_log.jsonl``의 전체 레코드를 읽는다. 파일이 없으면 빈 목록."""
        path = self._path(AI_USAGE_LOG_FILENAME)
        if not path.exists():
            return []
        records: list[AIUsageRecord] = []
        with path.open(encoding="utf-8") as fp:
            for line in fp:
                stripped = line.strip()
                if stripped:
                    records.append(AIUsageRecord.model_validate_json(stripped))
        return records


__all__ = ["AI_USAGE_LOG_FILENAME", "RUN_MANIFEST_FILENAME", "RunStore"]
