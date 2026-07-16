"""API-XBRL 정합성 검증 (Milestone B3, README §16.3~§16.4·§19.7).

전체 재무제표 API 수집분(A4 정규화)과 XBRL 원본 파싱(B2)을 교차검증해 대표 7계정의
값을 대조하고 상태를 분류한다(README §34 품질 관리 서사의 실증).

- :mod:`.xbrl_select` — concept·period·scope 매칭으로 XBRL fact 선택
- :mod:`.compare` — Decimal 정밀 비교·상태 분류(:class:`ReconciliationResult`)
- :mod:`.pipeline` — 전 파싱 보장 → 전량 대조 → 리포트 저장(:func:`reconcile_all`)

registry·A4·B2 산출은 소비만 한다(명세 §0).
"""

from research_backtest.core.reconciliation.compare import (
    ReconciliationResult,
    ReconciliationStatus,
    classify,
)
from research_backtest.core.reconciliation.pipeline import (
    ReconciliationReport,
    reconcile_all,
    reconciliation_out_dir,
)
from research_backtest.core.reconciliation.xbrl_select import (
    FactSelection,
    SelectionStage,
    XbrlIndex,
    select_fact,
)

__all__ = [
    "FactSelection",
    "ReconciliationReport",
    "ReconciliationResult",
    "ReconciliationStatus",
    "SelectionStage",
    "XbrlIndex",
    "classify",
    "reconcile_all",
    "reconciliation_out_dir",
    "select_fact",
]
