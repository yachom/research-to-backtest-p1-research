"""DART 단일회사 전체 재무제표 API 수집 (README §6.4, §7.3, §13.2, §19.3, Milestone A2).

**Current View 한계 (README §15)** — 전체 재무제표 API는 접수번호(rcept_no)를
입력받지 않는다. ``(corp_code, bsns_year, reprt_code, fs_div)``로 조회하며,
정정공시가 있으면 항상 **현재 기준 최신(Current View) 수치**가 반환된다.
따라서 응답 각 행에 포함된 ``rcept_no``를 반드시 보존한다 — 어떤 공시
버전에서 온 수치인지의 유일한 근거다. 당시 투자자가 본 값(Point-in-Time
View)의 완전한 재현은 XBRL 원본 수집(Milestone B1)과 정정공시 버전
관리(B4)에서 완성한다.

저장 구조 — ``{data_dir}/raw/dart/financials/{corp_code}/`` (README §7.3, §8 취지):

- ``{bsns_year}_{reprt_code}_{fs_div}.json``: 응답 본문을 **수신한 텍스트
  그대로** 저장(재직렬화 금지 — sha256 재현성)
- ``{bsns_year}_{reprt_code}_{fs_div}.meta.json``: 수집 메타(params·status·
  fetched_at·sha256·row_count·rcept_nos·sj_div_counts·source)
- ``financial_api_raw.jsonl``: 병합본 (README §19.3 출력물)
- ``collection_report.json``: 최근 수집 실행 요약(실행마다 덮어씀)

캐시 규칙(README §8.3 멱등성, §19.3 "캐시 지원"): 쓰기 순서는 데이터 파일 →
meta.json이며 **meta가 커밋 마커**다 — meta 없이 데이터 파일만 있으면 캐시
미스로 간주하고 재수집한다. 013(조회 데이터 없음)도 meta로 기록해 negative
cache로 쓴다 — 미제출 보고서(예: 아직 접수 전인 당해년 사업보고서)를 매
실행마다 재조회하지 않는다.
"""

import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from research_backtest.core.constants import FsDiv, ReprtCode
from research_backtest.core.dart.client import DartClient
from research_backtest.core.exceptions import DartApiError, DataValidationError

logger = logging.getLogger("r2b.dart.financial_api")

KST = ZoneInfo("Asia/Seoul")

FINANCIAL_API_PATH = "fnlttSinglAcntAll.json"
JSONL_FILENAME = "financial_api_raw.jsonl"
REPORT_FILENAME = "collection_report.json"
COLLECTION_SOURCE = "DART_FULL_FINANCIAL_API"  # 데이터 출처 구분 (README §13.2)
MIN_SUPPORTED_YEAR = 2015  # 전체 재무제표 API 제공 범위 (README §6.4)
NO_DATA_STATUS = "013"

# 병합(jsonl) 정렬 기준 — reprt_code는 ReprtCode 선언 순서(연내 시간 순서, 명세 §8)
_REPRT_ORDER: dict[str, int] = {code.value: index for index, code in enumerate(ReprtCode)}
_FS_DIV_ORDER: dict[str, int] = {div.value: index for index, div in enumerate(FsDiv)}
_DATA_FILENAME_RE = re.compile(
    r"^(?P<bsns_year>\d{4})_(?P<reprt_code>\d{5})_(?P<fs_div>CFS|OFS)\.json$"
)

RequestResult = Literal["FETCHED", "CACHED", "NO_DATA", "NO_DATA_CACHED"]


def financials_out_dir(data_dir: Path, corp_code: str) -> Path:
    """수집 결과 디렉토리 — ``{data_dir}/raw/dart/financials/{corp_code}``."""
    return data_dir / "raw" / "dart" / "financials" / corp_code


class DartFullFinancialRequest(BaseModel):
    """전체 재무제표 API 요청 (README §6.4).

    rcept_no는 입력이 아니다 — 항상 Current View가 반환된다(모듈 docstring).
    """

    corp_code: str
    bsns_year: str
    reprt_code: ReprtCode
    fs_div: FsDiv

    def file_stem(self) -> str:
        """저장 파일명 스템 — 예: ``2024_11011_CFS`` (reprt_code는 코드 값, 명세 §8)."""
        return f"{self.bsns_year}_{self.reprt_code.value}_{self.fs_div.value}"

    def as_params(self) -> dict[str, str]:
        """API 쿼리 파라미터(meta.json의 params와 동일 형태)로 변환한다."""
        return {
            "corp_code": self.corp_code,
            "bsns_year": self.bsns_year,
            "reprt_code": self.reprt_code.value,
            "fs_div": self.fs_div.value,
        }


class DartFinancialAccountRaw(BaseModel):
    """전체 재무제표 API 응답 1행 (README §6.4) — 검증·인벤토리용.

    저장은 응답 원문 텍스트 그대로이며 이 모델을 거치지 않는다(명세 A2 §2~3).
    ``extra="allow"``로 API 필드 추가(예: bfefrmtrm_*)에 깨지지 않게 한다.
    보고서 종류에 따라 응답에 없는 필드(예: 사업보고서의 frmtrm_q_*)가 있어
    선택 필드에는 기본값 None을 둔다. account_id는 표준계정을 쓰지 않은
    행에서 "-표준계정코드 미사용-" 표기가 온다(README §6.4).
    """

    model_config = ConfigDict(extra="allow")

    rcept_no: str
    reprt_code: str
    bsns_year: str
    corp_code: str

    sj_div: str
    sj_nm: str

    account_id: str
    account_nm: str
    account_detail: str | None = None

    thstrm_nm: str | None = None
    thstrm_amount: str | None = None
    thstrm_add_amount: str | None = None

    frmtrm_nm: str | None = None
    frmtrm_amount: str | None = None

    frmtrm_q_nm: str | None = None
    frmtrm_q_amount: str | None = None
    frmtrm_add_amount: str | None = None

    ord: str | None = None
    currency: str | None = None


class RequestOutcome(BaseModel):
    """요청 1건(연도·보고서·재무제표 구분)의 수집 결과 (명세 A2 §4)."""

    bsns_year: str
    reprt_code: ReprtCode
    fs_div: FsDiv
    result: RequestResult
    row_count: int
    # {"BS": 120, "IS": 40, ...} — BS·IS·CIS·CF·SCE 분리 집계 증빙 (README §31 M2)
    sj_div_counts: dict[str, int]
    rcept_nos: list[str]


class CollectionSummary(BaseModel):
    """수집 실행 1회의 요약 — collection_report.json에 저장 (명세 A2 §4)."""

    corp_code: str
    fetched_at: str  # KST ISO8601
    outcomes: list[RequestOutcome]


def collect_financials(
    client: DartClient,
    corp_code: str,
    *,
    from_year: int,
    to_year: int,
    fs_divs: Sequence[FsDiv] = (FsDiv.CFS, FsDiv.OFS),
    out_dir: Path,
    force: bool = False,
    min_interval_seconds: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionSummary:
    """연도 x 4개 reprt_code x fs_divs 전체를 수집한다 (README §14, §19.3).

    - reprt_code 순회는 :class:`ReprtCode` 선언 순서(Q1→반기→Q3→사업)로
      연내 시간 순서와 일치시킨다(명세 §8).
    - 캐시 히트(meta 파싱 가능 ∧ status ∈ {000, 013} ∧ 000이면 데이터 파일
      존재)면 API를 호출하지 않는다. ``force=True``면 무시하고 재수집한다.
    - **실제 API 호출 사이에만** ``min_interval_seconds``를 대기한다(캐시
      히트는 대기 없음). 값은 configs/dart.yaml의 request.min_interval_seconds.
    - 013은 NO_DATA로 meta에 기록(negative cache)하고, 그 외
      :class:`DartApiError`는 전파해 전체 실행을 중단한다 — 부분 실패를
      은폐하지 않는다(README §27).
    - 실행 말미에 financial_api_raw.jsonl을 재생성하고
      collection_report.json을 덮어쓴다.

    ``from_year < 2015`` 또는 ``from_year > to_year``면 :class:`ValueError`.
    ``sleep``은 테스트 주입용.
    """
    if from_year < MIN_SUPPORTED_YEAR:
        raise ValueError(
            f"전체 재무제표 API는 {MIN_SUPPORTED_YEAR}년 이후 사업연도만 제공합니다"
            f" (README §6.4): from_year={from_year}"
        )
    if from_year > to_year:
        raise ValueError(f"from_year({from_year})가 to_year({to_year})보다 큽니다.")

    out_dir.mkdir(parents=True, exist_ok=True)
    outcomes: list[RequestOutcome] = []
    api_called = False
    for year in range(from_year, to_year + 1):
        for reprt_code in ReprtCode:
            for fs_div in fs_divs:
                request = DartFullFinancialRequest(
                    corp_code=corp_code,
                    bsns_year=str(year),
                    reprt_code=reprt_code,
                    fs_div=fs_div,
                )
                if not force:
                    cached = _cached_outcome(out_dir, request)
                    if cached is not None:
                        outcomes.append(cached)
                        continue
                if api_called:
                    sleep(min_interval_seconds)
                api_called = True
                outcomes.append(_fetch_and_store(client, request, out_dir))

    line_count = rebuild_financial_jsonl(out_dir)
    summary = CollectionSummary(
        corp_code=corp_code,
        fetched_at=datetime.now(KST).isoformat(),
        outcomes=outcomes,
    )
    (out_dir / REPORT_FILENAME).write_text(
        summary.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    logger.info(
        "전체 재무제표 수집 완료 corp_code=%s 요청=%d건 jsonl=%d라인",
        corp_code,
        len(outcomes),
        line_count,
    )
    return summary


def rebuild_financial_jsonl(out_dir: Path) -> int:
    """디스크의 원문 파일들로부터 financial_api_raw.jsonl을 결정적으로 재생성한다 (명세 §3.4).

    - 대상: meta(status 000 — 커밋 마커)가 있는 데이터 파일 전부. 이번 실행
      범위 밖 연도의 기존 파일도 포함해 항상 디렉토리 전체의 병합본이
      된다(멱등, README §8.3).
    - 순서: bsns_year 오름차순 → reprt_code(11013→11012→11014→11011) →
      fs_div(CFS→OFS) → 응답 내 행 순서.
    - 응답 행에는 fs_div가 없으므로 provenance로 감싸 보존한다:
      ``{"bsns_year": ..., "reprt_code": ..., "fs_div": ..., "row": {...}}``

    반환: 총 라인 수.
    """
    keyed: list[tuple[tuple[int, int, int], str, str, str, Path]] = []
    for path in sorted(out_dir.glob("*.json")):
        matched = _DATA_FILENAME_RE.match(path.name)
        if matched is None:
            continue
        bsns_year = matched.group("bsns_year")
        reprt_code = matched.group("reprt_code")
        fs_div = matched.group("fs_div")
        if reprt_code not in _REPRT_ORDER:
            continue
        meta = _load_meta(out_dir / f"{bsns_year}_{reprt_code}_{fs_div}.meta.json")
        if meta is None or str(meta.get("status", "")) != "000":
            continue  # meta가 커밋 마커 — 미커밋 파일은 병합하지 않는다 (명세 §3.2)
        key = (int(bsns_year), _REPRT_ORDER[reprt_code], _FS_DIV_ORDER[fs_div])
        keyed.append((key, bsns_year, reprt_code, fs_div, path))
    keyed.sort(key=lambda item: item[0])

    lines: list[str] = []
    for _, bsns_year, reprt_code, fs_div, path in keyed:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
        rows: Any = payload.get("list") if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            raise DataValidationError(f"병합 대상 응답에 list가 없습니다: {path.name}")
        lines.extend(
            json.dumps(
                {"bsns_year": bsns_year, "reprt_code": reprt_code, "fs_div": fs_div, "row": row},
                ensure_ascii=False,
            )
            for row in rows
        )
    (out_dir / JSONL_FILENAME).write_text(
        "\n".join(lines) + ("\n" if lines else ""), encoding="utf-8"
    )
    return len(lines)


# --- 내부 구현 ---------------------------------------------------------------


def _cached_outcome(out_dir: Path, request: DartFullFinancialRequest) -> RequestOutcome | None:
    """캐시 히트면 CACHED/NO_DATA_CACHED 결과를, 미스면 None을 반환한다 (명세 §3.3).

    캐시 판정에서 데이터 파일 내용은 다시 파싱하지 않는다 — 손상 의심 시
    사용자가 ``--force-download``로 재수집한다.
    """
    meta = _load_meta(out_dir / f"{request.file_stem()}.meta.json")
    if meta is None:
        return None
    status = str(meta.get("status", ""))
    if status == "000":
        if not (out_dir / f"{request.file_stem()}.json").exists():
            return None
        return _outcome(
            request,
            "CACHED",
            row_count=int(meta.get("row_count") or 0),
            # 구(舊) meta에 sj_div_counts가 없으면 빈 dict 허용 (명세 §4)
            sj_div_counts=meta.get("sj_div_counts") or {},
            rcept_nos=meta.get("rcept_nos") or [],
        )
    if status == NO_DATA_STATUS:
        return _outcome(request, "NO_DATA_CACHED")
    return None


def _fetch_and_store(
    client: DartClient, request: DartFullFinancialRequest, out_dir: Path
) -> RequestOutcome:
    """API 1건을 호출하고 원문·meta를 기록한다 (명세 §3.1~3.2).

    쓰기 순서: 데이터 파일 → meta.json (meta가 커밋 마커).
    013이면 meta만 기록(negative cache)하고, 그 외 DartApiError는 전파한다.
    """
    fetched_at = datetime.now(KST).isoformat()
    try:
        payload, text = client.get_json_text(FINANCIAL_API_PATH, **request.as_params())
    except DartApiError as err:
        if err.status_code != NO_DATA_STATUS:
            raise
        _write_meta(
            out_dir,
            request,
            {
                "params": request.as_params(),
                "status": NO_DATA_STATUS,
                "fetched_at": fetched_at,
                "row_count": 0,
                "source": COLLECTION_SOURCE,
            },
        )
        logger.info("조회 데이터 없음(013) — negative cache 기록: %s", request.file_stem())
        return _outcome(request, "NO_DATA")

    accounts = [DartFinancialAccountRaw.model_validate(row) for row in _extract_rows(payload)]
    sj_div_counts: dict[str, int] = {}
    for account in accounts:
        sj_div_counts[account.sj_div] = sj_div_counts.get(account.sj_div, 0) + 1
    rcept_nos = sorted({account.rcept_no for account in accounts})

    (out_dir / f"{request.file_stem()}.json").write_text(text, encoding="utf-8")
    _write_meta(
        out_dir,
        request,
        {
            "params": request.as_params(),
            "status": "000",
            "fetched_at": fetched_at,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "row_count": len(accounts),
            "rcept_nos": rcept_nos,
            "sj_div_counts": sj_div_counts,
            "source": COLLECTION_SOURCE,
        },
    )
    logger.debug("수집 저장 완료: %s (%d행)", request.file_stem(), len(accounts))
    return _outcome(
        request,
        "FETCHED",
        row_count=len(accounts),
        sj_div_counts=sj_div_counts,
        rcept_nos=rcept_nos,
    )


def _extract_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """정상(000) 응답에서 list 배열을 꺼낸다 — 구조가 다르면 DataValidationError."""
    raw: Any = payload.get("list")
    if not isinstance(raw, list):
        raise DataValidationError("전체 재무제표 응답에 list 배열이 없습니다.")
    rows: list[dict[str, Any]] = []
    for row in raw:
        if not isinstance(row, dict):
            raise DataValidationError("전체 재무제표 응답 행이 객체가 아닙니다.")
        rows.append(row)
    return rows


def _load_meta(meta_path: Path) -> dict[str, Any] | None:
    """meta.json을 읽는다 — 없거나 파싱 불가면 None(캐시 미스)."""
    if not meta_path.exists():
        return None
    try:
        loaded: Any = json.loads(meta_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _write_meta(out_dir: Path, request: DartFullFinancialRequest, meta: dict[str, Any]) -> None:
    (out_dir / f"{request.file_stem()}.meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _outcome(
    request: DartFullFinancialRequest,
    result: RequestResult,
    *,
    row_count: int = 0,
    sj_div_counts: dict[str, int] | None = None,
    rcept_nos: list[str] | None = None,
) -> RequestOutcome:
    return RequestOutcome(
        bsns_year=request.bsns_year,
        reprt_code=request.reprt_code,
        fs_div=request.fs_div,
        result=result,
        row_count=row_count,
        sj_div_counts=sj_div_counts or {},
        rcept_nos=rcept_nos or [],
    )
