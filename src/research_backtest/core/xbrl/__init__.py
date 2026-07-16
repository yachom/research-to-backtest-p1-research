"""XBRL 원본 파싱 계층 (README §9, §19.5, Milestone B2).

- :mod:`.models` — XbrlFact·XbrlContext·XbrlUnit·XbrlDimension (README §9.2~9.5)
- :mod:`.discovery` — extracted/에서 instance 문서 탐색 (README §8.1 파일명 비고정)
- :mod:`.parser` — namespace 동적 파싱 (README §9.6 Numeric 변환)
- :mod:`.store` — parquet 4종 저장·로드 (README §19.5, 결정성)

원본 수집(다운로드)은 :mod:`research_backtest.core.dart.xbrl_downloader` (Milestone B1).
"""

from research_backtest.core.xbrl.models import (
    ParsedXbrl,
    XbrlContext,
    XbrlDimension,
    XbrlFact,
    XbrlUnit,
)

__all__ = [
    "ParsedXbrl",
    "XbrlContext",
    "XbrlDimension",
    "XbrlFact",
    "XbrlUnit",
]
