"""DART 고유번호 파일 수집·캐시와 기업 resolve (README §6.1, §19.1).

캐시 레이아웃 — ``{data_dir}/cache/dart/corp_code/``:

- ``response.zip``: API 응답 원본 그대로 보존 (README §8.1 취지)
- ``corps.jsonl``: 파싱 결과 (한 줄 = DartCorporation JSON)
- ``meta.json``: downloaded_at(KST ISO8601)·count·sha256(zip)·source

갱신 규칙(README §8.3 멱등성 취지): meta.json의 downloaded_at이
``refresh_days``보다 오래됐거나 force=True일 때만 재다운로드한다.
"""

import hashlib
import io
import json
import logging
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research_backtest.core.dart.client import DartClient
from research_backtest.core.dart.models import ResolveResult
from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.models import DartCorporation

logger = logging.getLogger("r2b.dart.corp_code")

KST = ZoneInfo("Asia/Seoul")

CORP_CODE_API_PATH = "corpCode.xml"
ZIP_FILENAME = "response.zip"
CORPS_FILENAME = "corps.jsonl"
META_FILENAME = "meta.json"
CACHE_SOURCE = "OPEN_DART_CORP_CODE"
MAX_CANDIDATES = 10

_STOCK_CODE_RE = re.compile(r"\d{6}")
_LEGAL_FORM_TOKENS = ("주식회사", "(주)")  # ㈜는 NFKC 정규화로 "(주)"가 된다
_REMOVED_CHARS = ".,·&-'\""


def corp_code_cache_dir(data_dir: Path) -> Path:
    """고유번호 캐시 디렉토리 경로 — ``{data_dir}/cache/dart/corp_code/``."""
    return data_dir / "cache" / "dart" / "corp_code"


def normalize_corp_name(name: str) -> str:
    """기업명 정규화 (README §6.1) — 인덱스 전용, 원본은 corp_name에 보존.

    NFKC(전각·㈜ 등 호환문자 정리) → casefold → 법인격 표기("주식회사"/"(주)")
    제거 → 공백 전부 제거 → 특수문자(``. , · & - ' "``) 제거.
    """
    text = unicodedata.normalize("NFKC", name).casefold()
    for token in _LEGAL_FORM_TOKENS:
        text = text.replace(token, "")
    text = re.sub(r"\s+", "", text)
    return text.translate(str.maketrans("", "", _REMOVED_CHARS))


def parse_corp_code_zip(raw: bytes) -> list[DartCorporation]:
    """고유번호 ZIP을 파싱해 기업 목록을 만든다 (README §6.1).

    ZIP 내부 파일명은 고정 가정하지 않고 첫 ``.xml`` 엔트리를 사용한다
    (README §8.1 취지). stock_code는 strip 후 빈 문자열이면 None.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith(".xml")]
            if not xml_names:
                raise DataValidationError("고유번호 ZIP에 XML 엔트리가 없습니다.")
            xml_bytes = zf.read(xml_names[0])
    except zipfile.BadZipFile as err:
        raise DataValidationError("고유번호 응답이 유효한 ZIP이 아닙니다.") from err

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as err:
        raise DataValidationError("고유번호 XML 파싱에 실패했습니다.") from err

    corporations: list[DartCorporation] = []
    for item in root.iter("list"):
        corp_code = (item.findtext("corp_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        if not corp_code or not corp_name:
            continue
        corporations.append(
            DartCorporation(
                corp_code=corp_code,
                corp_name=corp_name,
                corp_eng_name=(item.findtext("corp_eng_name") or "").strip() or None,
                stock_code=(item.findtext("stock_code") or "").strip() or None,
                modify_date=(item.findtext("modify_date") or "").strip(),
            )
        )
    if not corporations:
        raise DataValidationError("고유번호 XML에서 기업 항목을 찾지 못했습니다.")
    return corporations


class CorpCodeRegistry:
    """고유번호 목록을 메모리에 보유하고 기업을 식별한다 (README §19.1).

    corps.jsonl(약 10만 행)은 모듈 수준 캐시 없이 인스턴스가 보유한다 —
    CLI 1회 실행 수명이면 충분하다(명세 §8 구현 노트).
    """

    def __init__(self, corporations: Sequence[DartCorporation]) -> None:
        # (기업, 정규화된 이름들) — 부분일치 검색용
        self._entries: list[tuple[DartCorporation, tuple[str, ...]]] = []
        self._by_stock_code: dict[str, DartCorporation] = {}
        self._by_normalized_name: dict[str, list[DartCorporation]] = {}
        for corp in corporations:
            names = {
                normalized
                for raw_name in (corp.corp_name, corp.corp_eng_name)
                if raw_name
                if (normalized := normalize_corp_name(raw_name))
            }
            self._entries.append((corp, tuple(names)))
            if corp.stock_code:
                self._by_stock_code.setdefault(corp.stock_code, corp)
            for key in names:
                self._by_normalized_name.setdefault(key, []).append(corp)

    def __len__(self) -> int:
        return len(self._entries)

    def resolve(self, query: str) -> ResolveResult:
        """기업명·종목코드를 corp_code로 식별한다 (README §19.1, 명세 §2.3).

        우선순위:
        1. 6자리 숫자 → stock_code 정확 일치 (STOCK_CODE)
        2. 정규화 기업명 정확 일치(corp_name·corp_eng_name 모두) — 다중 일치 시
           상장기업 우선, 상장이 둘 이상이면 AMBIGUOUS
        3. 정규화 부분일치 — 1개면 SUBSTRING, 여럿이면 AMBIGUOUS, 없으면 NOT_FOUND

        알려진 한계: 음차 별칭(예: "에스케이하이닉스" ↔ 등기명 표기 차이)은
        정규화로 해결되지 않을 수 있다 → 후보 제시 + 종목코드 재시도로 커버.
        """
        stripped = query.strip()
        if _STOCK_CODE_RE.fullmatch(stripped):
            corp = self._by_stock_code.get(stripped)
            if corp is not None:
                return ResolveResult(matched=corp, candidates=[], method="STOCK_CODE")
            return ResolveResult(matched=None, candidates=[], method="NOT_FOUND")

        normalized = normalize_corp_name(stripped)
        if not normalized:
            return ResolveResult(matched=None, candidates=[], method="NOT_FOUND")

        exact = self._by_normalized_name.get(normalized, [])
        if exact:
            if len(exact) == 1:
                return ResolveResult(matched=exact[0], candidates=[], method="EXACT_NAME")
            listed = [corp for corp in exact if corp.stock_code]
            if len(listed) == 1:
                return ResolveResult(matched=listed[0], candidates=[], method="EXACT_NAME")
            return ResolveResult(
                matched=None, candidates=_sort_candidates(exact), method="AMBIGUOUS"
            )

        substring = [
            corp for corp, names in self._entries if any(normalized in name for name in names)
        ]
        if len(substring) == 1:
            return ResolveResult(matched=substring[0], candidates=[], method="SUBSTRING")
        if substring:
            return ResolveResult(
                matched=None, candidates=_sort_candidates(substring), method="AMBIGUOUS"
            )
        return ResolveResult(matched=None, candidates=[], method="NOT_FOUND")


def _sort_candidates(corporations: Iterable[DartCorporation]) -> list[DartCorporation]:
    """후보 정렬: 상장기업(stock_code 보유) 우선, 이름·코드 순, 최대 10개."""
    ordered = sorted(corporations, key=lambda c: (c.stock_code is None, c.corp_name, c.corp_code))
    return ordered[:MAX_CANDIDATES]


def load_corp_code_registry(
    client: DartClient,
    cache_dir: Path,
    *,
    refresh_days: int,
    force: bool = False,
    now: datetime | None = None,
) -> CorpCodeRegistry:
    """캐시가 신선하면 캐시에서, 아니면 API에서 고유번호 파일을 적재한다 (README §6.1).

    ``now``는 테스트 주입용(기본: 현재 KST). ``force``는 CLI
    ``--refresh-corp-codes``에 대응한다.
    """
    current = now if now is not None else datetime.now(KST)
    if not force and _is_cache_fresh(cache_dir, refresh_days=refresh_days, now=current):
        logger.debug("고유번호 캐시 사용: %s", cache_dir)
        return CorpCodeRegistry(_read_cached_corporations(cache_dir))
    logger.info("고유번호 파일 다운로드 (force=%s)", force)
    raw = client.get_bytes(CORP_CODE_API_PATH)
    corporations = parse_corp_code_zip(raw)
    _write_cache(cache_dir, raw, corporations, downloaded_at=current)
    return CorpCodeRegistry(corporations)


def _is_cache_fresh(cache_dir: Path, *, refresh_days: int, now: datetime) -> bool:
    """meta.json의 downloaded_at 기준으로 캐시 신선도를 판정한다."""
    meta_path = cache_dir / META_FILENAME
    if not meta_path.exists() or not (cache_dir / CORPS_FILENAME).exists():
        return False
    try:
        meta: Any = json.loads(meta_path.read_text(encoding="utf-8"))
        downloaded_at = datetime.fromisoformat(str(meta["downloaded_at"]))
    except (ValueError, KeyError, TypeError):
        return False
    if downloaded_at.tzinfo is None:
        return False
    return now - downloaded_at <= timedelta(days=refresh_days)


def _read_cached_corporations(cache_dir: Path) -> list[DartCorporation]:
    corporations: list[DartCorporation] = []
    with (cache_dir / CORPS_FILENAME).open(encoding="utf-8") as fp:
        for line in fp:
            if line.strip():
                corporations.append(DartCorporation.model_validate_json(line))
    return corporations


def _write_cache(
    cache_dir: Path,
    raw: bytes,
    corporations: Sequence[DartCorporation],
    *,
    downloaded_at: datetime,
) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / ZIP_FILENAME).write_bytes(raw)
    lines = "\n".join(corp.model_dump_json() for corp in corporations)
    (cache_dir / CORPS_FILENAME).write_text(lines + "\n", encoding="utf-8")
    meta = {
        "downloaded_at": downloaded_at.isoformat(),
        "count": len(corporations),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source": CACHE_SOURCE,
    }
    (cache_dir / META_FILENAME).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
