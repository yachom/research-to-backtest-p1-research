# A4 구현 명세 — 핵심 계정 정규화·재무 시계열 (README §31 M6 축소, Wave 1)

- 근거: README §11(분기 역산), §12(계정 표준화), §13(표준 모델), §16.2~16.3(회계식 검증), §17.1(성장성), §22.1(available_from), docs/DATA_NOTES.md(A2 관찰 — **필독**)
- 입력: `data/raw/dart/financials/00164779/financial_api_raw.jsonl`(A2, 6,436행), `data/normalized/market/calendar/krx_trading_days.parquet`(A3)
- 비범위: XBRL 기반 정규화(B3), 정정공시 버전 그래프(B4), TTM, LLM Evidence(C1)

## 0. 파일 소유권 (D8 병렬 규칙 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/core/financials/**` (신규 패키지)
- `configs/account_registry.yaml` (스키마 개정 — §2)
- `tests/unit/financials/**` (신규 디렉토리, 자체 conftest 허용), `tests/integration/test_financials_build.py`, `tests/fixtures/financials/**`
- `tests/unit/test_configs.py` (registry 스키마 변경 반영— 이 파일의 registry 관련 테스트만 수정)
- **금지**: `app/cli.py`(CLI 연결은 메인 세션), `core/exceptions.py`(필요 예외 DataValidationError·LookaheadError 이미 존재), `tests/unit/conftest.py`, 타 패키지

## 1. 모듈 배치

```text
core/financials/__init__.py
core/financials/registry.py      # CanonicalAccount + account_registry.yaml 로더
core/financials/normalizer.py    # jsonl → 정규화 fact (계정 매칭·기간 해석)
core/financials/quarterly.py     # 단독분기 역산 (README §11)
core/financials/metrics.py       # YoY 등 지표 계산
core/financials/pipeline.py      # build_financial_datasets() 오케스트레이션 + parquet 저장
```

## 2. Registry 개정 (DATA_NOTES A2-①: SK하이닉스는 손익이 전부 CIS)

`configs/account_registry.yaml`의 `statement_type: <단일>`을 **`statement_types: [<복수>]`**로 개정한다:

- 손익 계정(revenue, operating_income, net_income): `[IS, CIS]`
- BS 계정: `[BS]`, CF 계정: `[CF]`
- `tests/unit/test_configs.py`의 registry 검증을 새 스키마에 맞게 수정 (statement_types가 비어있지 않고 전부 BS/IS/CIS/CF/SCE 중 하나)

```python
class CanonicalAccount(BaseModel):   # README §12.2 확장
    canonical_id: str
    korean_name: str
    english_name: str | None = None
    statement_types: list[str]           # 매칭 허용 sj_div
    balance_type: str | None = None
    period_type: Literal["instant", "duration"]
    accepted_concepts: list[str] = []    # "ifrs-full:Revenue" 형태(콜론)
    accepted_labels: list[str] = []
```

## 3. 계정 매칭 (README §12.1 우선순위, DATA_NOTES ②·③)

한 행(row)이 canonical 계정에 매칭되는 조건 — 순서대로:

1. `row.sj_div ∈ statement_types` (전제 — **sj_div 필터 필수**, DATA_NOTES ③)
2. concept 일치: API의 `account_id`는 `ifrs-full_Revenue`(언더스코어) 형태 → **`_`를 `:`로 정규화한 뒤** accepted_concepts와 비교. `-표준계정코드 미사용-`은 concept 불일치로 취급
3. label 일치: `account_nm`을 공백 제거 후 accepted_labels(동일 정규화)와 비교

추가 규칙:
- **SCE는 전면 제외**(처리 대상 sj_div = BS/IS/CIS/CF만).
- 동일 (연도·보고서·fs_div·sj_div) 안에서 한 canonical에 복수 행 매칭 시: `account_detail`이 `-`인 행 우선, 그래도 복수면 **선택하지 말고** 해당 (계정, 기간)을 UNRESOLVED로 기록(매핑 리포트에 출력, README §12.3 취지).
- 미매칭 행은 무시하되 개수를 리포트에 집계.

## 4. 기간 해석·단독분기 (README §10.2, §11 — 12월 결산 가정)

전체 재무제표 API 의미론(README §843줄 인용): 분·반기 손익의 `thstrm_amount`=3개월, `thstrm_add_amount`=누적.

| 분기 | 소스 | 규칙 |
|---|---|---|
| Q1 | 11013 | IS·CIS: `thstrm_amount`(3개월). BS: `thstrm_amount`(기말) |
| Q2 | 11012 | IS·CIS: `thstrm_amount`(3개월) 우선, 없으면 `thstrm_add_amount`(반기누적) − Q1 → `DERIVED_QUARTER` |
| Q3 | 11014 | Q2와 동일 패턴 |
| Q4 | 11011 | IS·CIS: 연간 − 3Q누적(= 11014의 add, 없으면 Q1+Q2+Q3 단독 합) → 항상 `DERIVED_QUARTER` (README §11.3) |
| 연간 | 11011 | `thstrm_amount` |

- **CF는 연간만 필수.** 분기 CF의 thstrm/add 의미는 실데이터로 검증해 docstring과 보고에 기록하고, 확실할 때만 분기 CF를 생성(불확실하면 결측 처리 — 조용한 오답 금지).
- 금액 파싱(README §9.6): 빈 문자열/None → None(**0과 구분**), 쉼표 제거, 음수 부호, `Decimal`로 파싱 후 저장은 pandas **Int64**(nullable, KRW 정수). 비율 지표만 float64.
- README §11.2 역산 전제(동일 계정·scope·단위) 위반 시 해당 값 결측 + 리포트.

## 5. available_from (README §22.1)

- `rcept_dt = date(rcept_no[:8])` — 접수번호 앞 8자리가 접수일(YYYYMMDD). 각 (연도·보고서) 데이터의 rcept_no에서 유도.
- `available_from = KrxTradingCalendar.next_trading_day(rcept_dt)` — A3 캘린더 파일 사용. coverage 밖이면 예외 전파(조용한 대체 금지).
- 파생 분기값(Q4 등)의 available_from = **연간 보고서**의 available_from (가장 늦게 공개된 입력 기준 — 파생값은 모든 입력이 공개된 뒤에만 알 수 있다). 일반화: available_from = max(입력들의 available_from).

## 6. 지표 (README §17.1, §21.1 — A6 계약)

분기 단독 기준 YoY (전년 동기 대비): `revenue_yoy`, `operating_income_yoy`, `net_income_yoy`. 추가: `operating_margin`(단독분기 OP/매출).

- **YoY 부호 규약 (중요)**: `yoy = (cur − prev) / abs(prev)` — 전년 동기가 음수(적자)일 때 개선이 양수로 나오게 한다. SK하이닉스 2023 분기 영업이익이 음수라 2024 신호에 직결된다. `prev`가 None·0이면 None. docstring + DATA_NOTES에 기록.
- metric_id 명명은 README §21.1과 **완전 동일** (A5 DSL이 같은 이름을 컬럼으로 참조).

## 7. 출력 (parquet, `data/normalized/financials/{corp_code}/` — A6와의 계약)

1. `normalized_facts.parquet` (long): canonical_id, fs_scope, sj_div, fiscal_year, fiscal_quarter(Int64, 연간=NA), period_start, period_end, value(Int64), value_type(`REPORTED`/`DERIVED_QUARTER`), rcept_no, rcept_dt, available_from, source_account_id, source_account_nm
2. `quarterly_financials.parquet` (wide): fs_scope, fiscal_year, fiscal_quarter, period_start, period_end, rcept_no, rcept_dt, available_from + canonical 계정 컬럼들(Int64, IS·CIS·CF는 단독분기, BS는 기말잔액)
3. `annual_financials.parquet` (wide): 동일 구조, fiscal_quarter 없음
4. `financial_metrics.parquet`: metric_id, fs_scope, fiscal_year, fiscal_quarter, period_end, value(float64), rcept_no, rcept_dt, **available_from**, inputs_derived(bool) — **A6는 이 파일을 available_from으로 as-of join한다**
5. `build_report.json`: 매칭 통계(계정별 매칭 행수·미매칭 수·UNRESOLVED 목록), 검증 결과(§8), 생성 파일 요약

기본 scope=CFS(README §7). OFS도 동일 파이프라인으로 생성하되 파일 내 fs_scope 컬럼으로 구분(두 scope 모두 저장 — §7.3).

## 8. 검증 (pipeline 내장 — 실패 시 build_report에 기록하고 심각 위반은 DataValidationError)

- 회계식(§16.2·§16.3): 자산총계 ≈ 부채총계+자본총계 — 각 (연도·분기·scope), 허용 오차 abs 1e6 KRW ∨ rel 0.1%
- 교차 소스 일관성: `반기 add == Q1 + Q2 단독`, `3Q add == 반기 add + Q3 단독` (매출·영업이익, 동일 오차)
- 커버리지: CFS 기준 연간 5개년(2021~2025) × 필수 계정(revenue, operating_income, net_income, total_assets, total_liabilities, total_equity) non-null 100%; 최근 8개 분기 단독 손익 non-null
- available_from > period_end (공시가 회계기간 종료 후) — 전 행

## 9. 테스트

### unit (`tests/unit/financials/`, 오프라인 — 합성 fixture jsonl + 가짜 캘린더)

fixture는 실제 API 행 스키마를 모사한 소형 jsonl(1개 연도 4개 보고서 CFS)로: CIS 손익 / label-only 매칭(비표준 account_id) / Q2 직접·역산 두 경로 / Q4 역산 / BS instant / 복수 매칭 UNRESOLVED / 빈 금액 → None / 음수 YoY 규약.

| 대상 | 케이스 |
|---|---|
| registry | 새 스키마 로드, statement_types 검증, 콜론·언더스코어 concept 정규화 |
| normalizer | CIS 매칭 / label 매칭 / sj_div 필터(SCE 무시, ProfitLoss가 SCE에 있어도 미매칭) / UNRESOLVED / 빈 값 vs 0 구분 |
| quarterly | Q2 두 경로, Q4 역산 + value_type=DERIVED_QUARTER, 입력 결측 시 결측 전파 |
| metrics | YoY 정상 / **음수 base** / prev=None·0 → None / operating_margin |
| available_from | rcept_no→rcept_dt, 금요일→월요일(가짜 캘린더), 파생값 = max(입력 available_from) |
| pipeline | 산출 4개 parquet 스키마(컬럼·dtype) 계약 고정, 회계식 위반 fixture → DataValidationError |

### integration (`tests/integration/test_financials_build.py`, 실데이터 — `data/raw/dart/financials/00164779/financial_api_raw.jsonl` 없으면 skip)

- 실데이터 전체 빌드 성공 + §8 검증 전부 통과
- 최근 8개 분기 operating_income_yoy 존재, available_from이 캘린더 거래일

## 10. DoD

1. ruff·mypy strict·pytest 전부 통과 (본인 워크트리에서 전체 스위트)
2. 실데이터 빌드: 5개년 연간 + 8개 분기 단독 + metrics 생성, build_report의 UNRESOLVED·미커버 계정 0건 (불가능하면 사유를 보고에 명시)
3. §7 출력 스키마가 명세와 정확히 일치 (A6가 이 계약으로 병렬 개발된다)
4. DATA_NOTES에 넣을 관찰(분기 CF 의미론 실측 등)을 최종 보고에 포함
