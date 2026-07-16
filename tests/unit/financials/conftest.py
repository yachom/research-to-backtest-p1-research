"""A4 재무 정규화 unit 테스트 공용 픽스처 — 오프라인(합성 jsonl + 가짜 캘린더).

실제 API 행 스키마를 모사한 소형 jsonl(2개 연도 4개 보고서 CFS)을 합성한다.
YoY(전년 동기)를 만들기 위해 2개 연도를 담고, 2022 손익을 음수로 두어 음수
base YoY 규약을 실데이터 없이 검증한다.
"""

import json
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path

import pytest

from research_backtest.core.financials.normalizer import RAW_JSONL_FILENAME
from research_backtest.core.market.calendar import KrxTradingCalendar

# reprt_code (core.constants.ReprtCode와 동일)
Q1, HALF, Q3, ANNUAL = "11013", "11012", "11014", "11011"

# (year, reprt) → rcept_no 접두 8자리(접수일) — 전부 평일
RCEPT_DT: dict[tuple[int, str], str] = {
    (2022, Q1): "20220516",  # 월
    (2022, HALF): "20220816",  # 화
    (2022, Q3): "20221114",  # 월
    (2022, ANNUAL): "20230320",  # 월 (FY2022 사업보고서는 다음해 제출)
    (2023, Q1): "20230515",
    (2023, HALF): "20230814",
    (2023, Q3): "20231114",
    (2023, ANNUAL): "20240320",
}


def _rcept_no(year: int, reprt: str) -> str:
    return RCEPT_DT[(year, reprt)] + "000001"


def _income_rows(
    account_id: str, account_nm: str, singles: dict[int, int]
) -> dict[str, dict[str, str]]:
    """손익 계정의 4개 보고서 행 값 — thstrm=3개월, add=누적(연간 add는 빈 문자열)."""
    q1, q2, q3, q4 = singles[1], singles[2], singles[3], singles[4]
    cum = {Q1: q1, HALF: q1 + q2, Q3: q1 + q2 + q3}
    thstrm = {Q1: q1, HALF: q2, Q3: q3, ANNUAL: q1 + q2 + q3 + q4}
    out: dict[str, dict[str, str]] = {}
    for reprt in (Q1, HALF, Q3, ANNUAL):
        out[reprt] = {
            "account_id": account_id,
            "account_nm": account_nm,
            "account_detail": "-",
            "sj_div": "CIS",
            "thstrm_amount": str(thstrm[reprt]),
            "thstrm_add_amount": "" if reprt == ANNUAL else str(cum[reprt]),
        }
    return out


def _instant_rows(
    account_id: str, account_nm: str, balances: dict[str, int]
) -> dict[str, dict[str, str]]:
    """BS 계정 — thstrm=기말잔액, add 없음(키 자체 없음)."""
    return {
        reprt: {
            "account_id": account_id,
            "account_nm": account_nm,
            "account_detail": "-",
            "sj_div": "BS",
            "thstrm_amount": str(balances[reprt]),
        }
        for reprt in (Q1, HALF, Q3, ANNUAL)
    }


def _cf_rows(account_id: str, account_nm: str, cum: dict[str, int]) -> dict[str, dict[str, str]]:
    """CF 계정 — thstrm=누적(YTD), add 필드 없음 (A4 실측)."""
    return {
        reprt: {
            "account_id": account_id,
            "account_nm": account_nm,
            "account_detail": "-",
            "sj_div": "CF",
            "thstrm_amount": str(cum[reprt]),
        }
        for reprt in (Q1, HALF, Q3, ANNUAL)
    }


def _balances(base: int, step: int) -> dict[str, int]:
    return {Q1: base, HALF: base + step, Q3: base + 2 * step, ANNUAL: base + 3 * step}


def build_synthetic_rows(*, broken_identity: bool = False) -> list[dict[str, object]]:
    """2개 연도(2022·2023) CFS 합성 행 — provenance 래퍼 리스트 (jsonl 라인).

    회계식(자산=부채+자본)·교차 소스 일관성이 성립하도록 구성한다. 2022 손익은
    음수라 2023 YoY가 음수 base 규약을 밟는다. ``broken_identity=True``면 2023
    연간 자본을 크게 틀어 회계식 위반을 유도한다(pipeline DataValidationError).

    label-only 매칭 실증: trade_receivables는 비표준 account_id
    ``ifrs-full_CurrentTradeReceivables``(registry concept 미일치)에 label
    '매출채권'으로만 매칭된다. SCE의 ifrs-full_ProfitLoss 행 1개는 sj_div
    필터로 무시되어야 한다.
    """
    income = {
        "revenue": {
            "id": "ifrs-full_Revenue",
            "nm": "매출액",
            2022: {1: 100, 2: 110, 3: 120, 4: 130},
            2023: {1: 200, 2: 220, 3: 240, 4: 260},
        },
        "operating_income": {
            "id": "dart_OperatingIncomeLoss",
            "nm": "영업이익(손실)",
            2022: {1: -10, 2: -5, 3: -8, 4: 3},  # 음수 base
            2023: {1: 20, 2: 25, 3: 30, 4: 35},
        },
        "net_income": {
            "id": "ifrs-full_ProfitLoss",
            "nm": "분기순이익(손실)",
            2022: {1: -8, 2: -4, 3: -6, 4: 2},
            2023: {1: 15, 2: 18, 3: 20, 4: 25},
        },
    }
    liabilities = {2022: _balances(600, 5), 2023: _balances(650, 5)}
    equity = {2022: _balances(400, 5), 2023: _balances(455, 5)}
    assets = {
        year: {reprt: liabilities[year][reprt] + equity[year][reprt] for reprt in liabilities[year]}
        for year in (2022, 2023)
    }
    if broken_identity:
        equity[2023][ANNUAL] += 10_000_000  # 자산 ≠ 부채+자본 (허용오차 초과)
    cf = {
        2022: {Q1: 50, HALF: 110, Q3: 180, ANNUAL: 260},
        2023: {Q1: 90, HALF: 190, Q3: 300, ANNUAL: 420},
    }
    receivables = {2022: _balances(70, 2), 2023: _balances(80, 2)}

    rows: list[dict[str, object]] = []
    for year in (2022, 2023):
        per_report: dict[str, list[dict[str, str]]] = {r: [] for r in (Q1, HALF, Q3, ANNUAL)}
        for spec in income.values():
            built = _income_rows(str(spec["id"]), str(spec["nm"]), spec[year])  # type: ignore[arg-type]
            for reprt, row in built.items():
                per_report[reprt].append(row)
        for values, aid, anm in [
            (assets[year], "ifrs-full_Assets", "자산총계"),
            (liabilities[year], "ifrs-full_Liabilities", "부채총계"),
            (equity[year], "ifrs-full_Equity", "자본총계"),
            (receivables[year], "ifrs-full_CurrentTradeReceivables", "매출채권"),
        ]:
            for reprt, row in _instant_rows(aid, anm, values).items():
                per_report[reprt].append(row)
        for reprt, row in _cf_rows(
            "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동현금흐름", cf[year]
        ).items():
            per_report[reprt].append(row)
        # sj_div 필터 검증용: SCE에 ProfitLoss 1행(무시되어야 함)
        per_report[ANNUAL].append(
            {
                "account_id": "ifrs-full_ProfitLoss",
                "account_nm": "당기순이익(손실)",
                "account_detail": "연결재무제표 [member]",
                "sj_div": "SCE",
                "thstrm_amount": "999999",
            }
        )
        for reprt in (Q1, HALF, Q3, ANNUAL):
            for row in per_report[reprt]:
                full = {
                    "rcept_no": _rcept_no(year, reprt),
                    "reprt_code": reprt,
                    "bsns_year": str(year),
                    "corp_code": "00000000",
                    "sj_nm": row["sj_div"],
                    "currency": "KRW",
                    **row,
                }
                rows.append(
                    {"bsns_year": str(year), "reprt_code": reprt, "fs_div": "CFS", "row": full}
                )
    return rows


@pytest.fixture
def fake_calendar() -> KrxTradingCalendar:
    """평일만 거래일인 가짜 KRX 캘린더(2020~2027) — 금요일→월요일 스킵 검증용."""
    start, end = date(2020, 1, 1), date(2027, 12, 31)
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return KrxTradingCalendar(days)


@pytest.fixture
def write_synthetic_dataset(
    tmp_path: Path,
) -> Callable[..., Path]:
    """합성 jsonl을 tmp data_dir의 실제 입력 경로에 써주는 팩토리 — data_dir 반환."""

    def _write(*, corp_code: str = "00000000", broken_identity: bool = False) -> Path:
        data_dir = tmp_path / "data"
        raw_dir = data_dir / "raw" / "dart" / "financials" / corp_code
        raw_dir.mkdir(parents=True, exist_ok=True)
        rows = build_synthetic_rows(broken_identity=broken_identity)
        (raw_dir / RAW_JSONL_FILENAME).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8"
        )
        return data_dir

    return _write
