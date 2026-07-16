"""jsonl → 정규화 관측치 (계정 매칭·금액 파싱) (명세 A4 §3~§4, README §12).

A2가 저장한 ``financial_api_raw.jsonl``의 각 라인은 provenance 래퍼
``{"bsns_year", "reprt_code", "fs_div", "row": {...}}``다(financial_api.py
``rebuild_financial_jsonl``). 이 모듈은 각 행을 :class:`CanonicalAccount`에
매칭하고 금액을 파싱해 보고서 단위 관측치(:class:`ReportObservation`)로
만든다. **기간 해석·단독분기 역산은 quarterly.py**가 이 관측치를 소비해
수행한다.

매칭 규칙(명세 §3):

1. ``sj_div ∈ statement_types`` (전제). **SCE는 전면 제외**한다.
2. concept 일치(``_``→``:`` 정규화) → 3. label 일치(공백 제거).
3. 동일 (연도·보고서·fs_div·sj_div) 안에서 한 canonical에 복수 행 매칭 시
   ``account_detail == "-"``인 행을 우선하고, 그래도 복수면 선택하지 않고
   UNRESOLVED로 기록한다(조용한 오답 금지, README §12.3).

금액 파싱(명세 §4, README §9.6): 빈 문자열/None → ``None``(**0과 구분**),
쉼표 제거, 음수 부호 유지, :class:`~decimal.Decimal`로 파싱 후 정수화.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from research_backtest.core.constants import StatementType
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.financials.registry import CanonicalAccount

# A4 처리 대상 sj_div — SCE 제외 (명세 §3, DATA_NOTES A2-③).
PROCESSED_SJ_DIVS: frozenset[str] = frozenset(
    {
        StatementType.BS.value,
        StatementType.IS.value,
        StatementType.CIS.value,
        StatementType.CF.value,
    }
)

RAW_JSONL_FILENAME = "financial_api_raw.jsonl"


def parse_amount(raw: str | None) -> int | None:
    """금액 문자열을 정수(KRW)로 파싱한다 — 빈 값은 None (명세 §4, README §9.6).

    빈 문자열/None → ``None``(0과 구분), 쉼표 제거, ``Decimal`` 파싱 후
    정수화한다. 파싱 불가 문자열은 :class:`DataValidationError`(조용한
    0 대체 금지).
    """
    if raw is None:
        return None
    text = raw.strip()
    if text == "":
        return None
    try:
        return int(Decimal(text.replace(",", "")))
    except (InvalidOperation, ValueError) as err:
        raise DataValidationError(f"금액을 파싱할 수 없습니다: {raw!r}") from err


@dataclass(frozen=True)
class ObservationKey:
    """관측치 조회 키 — (canonical, scope, 연도, 보고서코드)."""

    canonical_id: str
    fs_scope: str
    fiscal_year: int
    reprt_code: str


@dataclass
class ReportObservation:
    """한 보고서에서 매칭된 한 canonical 계정의 원시 관측치 (명세 §3~§4).

    ``thstrm_amount``·``thstrm_add_amount``의 의미는 계정 계열에 따라 다르다
    (quarterly.py docstring): 손익(IS/CIS)은 thstrm=3개월/add=누적, CF는
    thstrm=누적(YTD)이고 add 필드가 없다(A4 실측), BS는 thstrm=기말잔액.
    """

    canonical_id: str
    fs_scope: str
    fiscal_year: int
    reprt_code: str
    sj_div: str
    thstrm_amount: int | None
    thstrm_add_amount: int | None
    rcept_no: str
    source_account_id: str
    source_account_nm: str


@dataclass
class UnresolvedEntry:
    """복수 행 매칭으로 값을 확정하지 못한 (계정·보고서) (명세 §3, README §12.3)."""

    canonical_id: str
    fs_scope: str
    fiscal_year: int
    reprt_code: str
    sj_div: str
    candidate_account_nms: list[str]


@dataclass
class NormalizationResult:
    """정규화 산출물 — 관측치 + 매칭 통계 (build_report 입력, 명세 §7-⑤)."""

    observations: dict[ObservationKey, ReportObservation] = field(default_factory=dict)
    matched_row_counts: dict[str, int] = field(default_factory=dict)
    unmatched_row_count: int = 0
    sce_skipped_count: int = 0
    processed_row_count: int = 0
    unresolved: list[UnresolvedEntry] = field(default_factory=list)

    def get(
        self, canonical_id: str, fs_scope: str, fiscal_year: int, reprt_code: str
    ) -> ReportObservation | None:
        return self.observations.get(
            ObservationKey(canonical_id, fs_scope, fiscal_year, reprt_code)
        )

    def scopes(self) -> list[str]:
        return sorted({key.fs_scope for key in self.observations})

    def years(self, fs_scope: str) -> list[int]:
        return sorted({key.fiscal_year for key in self.observations if key.fs_scope == fs_scope})


def load_raw_rows(path: Path) -> list[dict[str, Any]]:
    """financial_api_raw.jsonl을 provenance 래퍼 dict 리스트로 로드한다.

    빈 파일은 :class:`DataValidationError`. 각 라인은
    ``{"bsns_year", "reprt_code", "fs_div", "row"}`` 구조여야 한다.
    """
    if not path.exists():
        raise DataValidationError(
            f"정규화 입력 jsonl이 없습니다: {path} "
            "(r2b collect-financials로 전체 재무제표를 먼저 수집)"
        )
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parsed: Any = json.loads(line)
        if not isinstance(parsed, dict) or "row" not in parsed:
            raise DataValidationError(f"jsonl 라인 구조가 잘못되었습니다: {line[:80]!r}")
        rows.append(parsed)
    if not rows:
        raise DataValidationError(f"정규화 입력 jsonl이 비어 있습니다: {path}")
    return rows


def normalize_financials(
    raw_rows: Iterable[dict[str, Any]],
    registry: dict[str, CanonicalAccount],
    *,
    scopes: Iterable[str],
) -> NormalizationResult:
    """jsonl 원시 행들을 canonical 관측치로 정규화한다 (명세 §3~§4).

    ``scopes``(CFS/OFS)에 해당하는 행만 처리한다. SCE 행은 건너뛰고 개수만
    집계한다. 한 (연도·보고서·fs_div·sj_div)에서 canonical 매칭이 애매하면
    관측치를 만들지 않고 UNRESOLVED로 기록한다.
    """
    scope_set = set(scopes)
    result = NormalizationResult()
    result.matched_row_counts = {canonical_id: 0 for canonical_id in registry}

    # (canonical, scope, year, reprt, sj_div) → 매칭된 행들 (account_detail 우선순위 해소용)
    grouped: dict[tuple[str, str, int, str, str], list[dict[str, Any]]] = {}

    for wrapper in raw_rows:
        fs_scope = str(wrapper.get("fs_div", ""))
        if fs_scope not in scope_set:
            continue
        row = wrapper["row"]
        if not isinstance(row, dict):
            raise DataValidationError("jsonl 래퍼의 row가 객체가 아닙니다.")
        sj_div = str(row.get("sj_div", ""))
        if sj_div == StatementType.SCE.value:
            result.sce_skipped_count += 1
            continue
        if sj_div not in PROCESSED_SJ_DIVS:
            continue
        result.processed_row_count += 1

        fiscal_year = int(str(wrapper["bsns_year"]))
        reprt_code = str(wrapper["reprt_code"])
        account_id = str(row.get("account_id", ""))
        account_nm = str(row.get("account_nm", ""))

        matched_any = False
        for canonical_id, account in registry.items():
            if account.matches(sj_div, account_id, account_nm):
                matched_any = True
                result.matched_row_counts[canonical_id] += 1
                group_key = (canonical_id, fs_scope, fiscal_year, reprt_code, sj_div)
                grouped.setdefault(group_key, []).append(row)
        if not matched_any:
            result.unmatched_row_count += 1

    _resolve_observations(grouped, registry, result)
    return result


# --- 내부 구현 ---------------------------------------------------------------


def _resolve_observations(
    grouped: dict[tuple[str, str, int, str, str], list[dict[str, Any]]],
    registry: dict[str, CanonicalAccount],
    result: NormalizationResult,
) -> None:
    """그룹별 매칭 행을 관측치로 확정한다 — 복수 매칭은 account_detail 우선/UNRESOLVED.

    canonical의 statement_types 순서대로 sj_div를 훑어 **가장 먼저 확정되는**
    sj_div의 값을 관측치로 채택한다(README §12.1: IS가 있으면 IS 우선, 없으면
    CIS — SK하이닉스는 CIS만 존재). 동일 sj_div 내 애매성은 UNRESOLVED.
    """
    # (canonical, scope, year, reprt) → {sj_div: 확정 행 | None(UNRESOLVED)}
    resolved_by_sj: dict[tuple[str, str, int, str], dict[str, dict[str, Any] | None]] = {}
    unresolved_seen: set[tuple[str, str, int, str, str]] = set()

    for (canonical_id, fs_scope, year, reprt_code, sj_div), matches in grouped.items():
        chosen = _pick_row(matches)
        obs_key = (canonical_id, fs_scope, year, reprt_code)
        resolved_by_sj.setdefault(obs_key, {})[sj_div] = chosen
        if (
            chosen is None
            and (canonical_id, fs_scope, year, reprt_code, sj_div) not in unresolved_seen
        ):
            unresolved_seen.add((canonical_id, fs_scope, year, reprt_code, sj_div))
            result.unresolved.append(
                UnresolvedEntry(
                    canonical_id=canonical_id,
                    fs_scope=fs_scope,
                    fiscal_year=year,
                    reprt_code=reprt_code,
                    sj_div=sj_div,
                    candidate_account_nms=[str(m.get("account_nm", "")) for m in matches],
                )
            )

    for (canonical_id, fs_scope, year, reprt_code), by_sj in resolved_by_sj.items():
        account = registry[canonical_id]
        chosen_row: dict[str, Any] | None = None
        chosen_sj: str | None = None
        for sj_div in account.statement_types:  # statement_types 순서 = 우선순위
            if sj_div in by_sj and by_sj[sj_div] is not None:
                chosen_row = by_sj[sj_div]
                chosen_sj = sj_div
                break
        if chosen_row is None or chosen_sj is None:
            continue  # 모든 sj_div가 UNRESOLVED — 관측치 없음
        result.observations[ObservationKey(canonical_id, fs_scope, year, reprt_code)] = (
            ReportObservation(
                canonical_id=canonical_id,
                fs_scope=fs_scope,
                fiscal_year=year,
                reprt_code=reprt_code,
                sj_div=chosen_sj,
                thstrm_amount=parse_amount(chosen_row.get("thstrm_amount")),
                thstrm_add_amount=parse_amount(chosen_row.get("thstrm_add_amount")),
                rcept_no=str(chosen_row["rcept_no"]),
                source_account_id=str(chosen_row.get("account_id", "")),
                source_account_nm=str(chosen_row.get("account_nm", "")),
            )
        )


def _pick_row(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    """복수 매칭에서 값을 확정한다 — account_detail=='-' 우선, 그래도 복수면 None(UNRESOLVED)."""
    if len(matches) == 1:
        return matches[0]
    dash_rows = [m for m in matches if str(m.get("account_detail", "")).strip() == "-"]
    if len(dash_rows) == 1:
        return dash_rows[0]
    return None
