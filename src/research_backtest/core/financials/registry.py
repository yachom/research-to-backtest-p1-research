"""표준계정 Registry — CanonicalAccount + account_registry.yaml 로더 (명세 A4 §2, README §12.2).

계정 매칭(명세 §3)의 계약을 정의한다:

- ``statement_types``: 매칭을 허용하는 sj_div 집합. 손익 계정은 ``[IS, CIS]``
  (DATA_NOTES A2-①: SK하이닉스는 손익이 전부 CIS에 담긴다), BS는 ``[BS]``,
  CF는 ``[CF]``. **SCE는 어떤 계정도 허용하지 않는다** — A4 처리 범위 밖.
- ``accepted_concepts``: ``"prefix:Name"``(콜론) 형태의 XBRL 표준계정 ID.
  API 응답의 ``account_id``는 ``"prefix_Name"``(언더스코어)이므로
  :func:`normalize_concept`로 ``_``→``:`` 정규화한 뒤 비교한다.
- ``accepted_labels``: ``account_nm``을 :func:`normalize_label`(공백 제거)로
  정규화해 비교한다.
"""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ValidationError, field_validator

from research_backtest.core.constants import StatementType
from research_backtest.core.exceptions import ConfigError

DEFAULT_REGISTRY_PATH = Path("configs/account_registry.yaml")

# API가 표준계정을 쓰지 않은 행에 붙이는 표기 — concept 매칭에서 제외한다
# (명세 §3-②, DATA_NOTES A2-②: 6,436행 중 857행/13.3%).
NON_STANDARD_ACCOUNT_ID = "-표준계정코드 미사용-"

_VALID_SJ_DIVS = frozenset(member.value for member in StatementType)


def normalize_concept(concept: str) -> str:
    """XBRL concept 정규화 — ``_``를 ``:``로 치환한다 (명세 §3-②).

    API ``account_id``(``ifrs-full_Revenue``)와 registry ``accepted_concepts``
    (``ifrs-full:Revenue``)를 같은 형태로 만들어 비교한다. IFRS/DART concept
    이름부에는 언더스코어가 없어 전역 치환이 안전하며, 양쪽에 동일 규칙을
    적용하므로 비교가 일관된다.
    """
    return concept.replace("_", ":")


def normalize_label(label: str) -> str:
    """계정명(label) 정규화 — 모든 공백을 제거한다 (명세 §3-③).

    ``"영업활동 현금흐름"``과 ``"영업활동현금흐름"``을 동일 취급한다.
    ``str.split()``은 전각 공백(U+3000)을 포함한 유니코드 공백을 모두
    분리하므로 KRX 라벨의 다양한 공백 표기를 흡수한다.
    """
    return "".join(label.split())


class CanonicalAccount(BaseModel):
    """표준계정 1개의 매칭 규칙 (README §12.2 확장, 명세 A4 §2).

    ``statement_types``는 비어 있지 않고 전부 BS/IS/CIS/CF/SCE 중 하나여야
    한다(로더 검증). ``accepted_concepts``는 콜론 형태로 보관한다.
    """

    canonical_id: str
    korean_name: str
    english_name: str | None = None
    statement_types: list[str]
    balance_type: str | None = None
    period_type: Literal["instant", "duration"]
    accepted_concepts: list[str] = []
    accepted_labels: list[str] = []

    @field_validator("statement_types")
    @classmethod
    def _validate_statement_types(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("statement_types가 비어 있습니다 (명세 §2)")
        invalid = [sj for sj in value if sj not in _VALID_SJ_DIVS]
        if invalid:
            raise ValueError(
                f"허용되지 않은 sj_div {invalid} — {sorted(_VALID_SJ_DIVS)} 중 하나여야 합니다"
            )
        return value

    def matches(self, sj_div: str, account_id: str, account_nm: str) -> bool:
        """한 행이 이 계정에 매칭되는지 — 명세 §3의 순서(sj_div → concept → label).

        1. ``sj_div``가 ``statement_types``에 없으면 즉시 불일치(전제).
        2. concept 일치: ``account_id``를 정규화해 ``accepted_concepts``와 비교.
           ``NON_STANDARD_ACCOUNT_ID``는 concept 불일치로 취급한다.
        3. label 일치: ``account_nm``을 정규화해 ``accepted_labels``와 비교.
        """
        if sj_div not in self.statement_types:
            return False
        if account_id != NON_STANDARD_ACCOUNT_ID:
            normalized = normalize_concept(account_id)
            if any(normalized == normalize_concept(c) for c in self.accepted_concepts):
                return True
        normalized_label = normalize_label(account_nm)
        return any(normalized_label == normalize_label(label) for label in self.accepted_labels)

    def is_cumulative_flow(self) -> bool:
        """CF 계정인지 — thstrm_amount가 누적(YTD)으로 보고되는 계열 (명세 §4, A4 실측).

        CF는 ``thstrm_add_amount`` 필드가 없고 ``thstrm_amount``가 누적이라
        단독분기 역산 방식이 손익(IS/CIS)과 다르다(quarterly 모듈 참조).
        """
        return self.period_type == "duration" and StatementType.CF.value in self.statement_types

    def is_period_flow(self) -> bool:
        """손익(IS/CIS) 계정인지 — thstrm=3개월, thstrm_add=누적 계열 (명세 §4)."""
        return self.period_type == "duration" and not self.is_cumulative_flow()


def load_registry(path: Path = DEFAULT_REGISTRY_PATH) -> dict[str, CanonicalAccount]:
    """account_registry.yaml을 읽어 canonical_id → :class:`CanonicalAccount`로 로드한다.

    파일이 없거나 스키마 위반이면 :class:`ConfigError`. canonical_id는 YAML의
    최상위 키를 사용한다.
    """
    if not path.exists():
        raise ConfigError(f"계정 registry 파일이 없습니다: {path} (레포 루트에서 실행했는지 확인)")
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not raw:
        raise ConfigError(f"계정 registry 형식이 잘못되었습니다(비어 있거나 매핑이 아님): {path}")

    registry: dict[str, CanonicalAccount] = {}
    for canonical_id, entry in raw.items():
        if not isinstance(entry, dict):
            raise ConfigError(f"registry 항목이 매핑이 아닙니다: {canonical_id}")
        try:
            registry[canonical_id] = CanonicalAccount(canonical_id=canonical_id, **entry)
        except ValidationError as err:
            raise ConfigError(f"registry 항목 검증 실패({canonical_id}): {err}") from err
    return registry
