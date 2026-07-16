# B3 구현 명세 — API-XBRL 정합성 검증 (README §31 M5 잔여·§16.4·§19.7, Wave 2)

- 근거: README §16.3(허용 오차)·§16.4(교차검증·ReconciliationResult·상태), §19.7(P1-07), §10(Context 선택 — **단, docs/DATA_NOTES.md B1+B2 실측 ②로 개정된 규칙을 따른다**), §34(품질 관리 서사)
- 입력(전부 실물 존재): A4 `data/normalized/financials/{corp}/normalized_facts.parquet`(REPORTED 행 — period_start/end·rcept_no 포함), B1 `data/raw/dart/xbrl/{corp}/{rcept_no}/extracted/`, B2 `core/xbrl.parse_extracted/store_parsed_xbrl`(normalized parquet 4종), `configs/account_registry.yaml`
- 비범위: 매핑 자체의 수정(발견 사항은 보고), 정정공시 버전 선택(B4), 주요계정 API 3원 대조(후순위 — §16.4의 major_account_value 필드는 None 허용)

## 0. 파일 소유권 (D8 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/core/reconciliation/**` (신규 패키지)
- `tests/unit/reconciliation/**`(자체 conftest 허용), `tests/fixtures/reconciliation/**`, `tests/integration/test_reconciliation.py`
- **금지**: `core/xbrl/**`·`core/financials/**`(소비만), `configs/account_registry.yaml`(소비만 — 개정 필요 발견 시 보고로), `app/cli.py`, 공유 테스트 파일

## 1. 모듈 배치

```text
core/reconciliation/__init__.py
core/reconciliation/xbrl_select.py   # XBRL fact 선택: concept·period·scope 매칭
core/reconciliation/compare.py       # 값 비교·상태 분류 (ReconciliationResult)
core/reconciliation/pipeline.py      # 배치: 전 파싱 보장 → 대조 → 리포트 저장
```

## 2. 사전 단계 — 전 XBRL 파싱 보장

`data/raw/dart/xbrl/{corp}/` 아래 22개 rcept_no 중 normalized parquet이 없는 것은
B2의 `parse_extracted → store_parsed_xbrl`로 생성한다(멱등 — 이미 있으면 스킵).
파싱 실패 rcept_no는 중단하지 말고 리포트에 기록 후 계속.

## 3. xbrl_select.py — fact 선택 규칙

대상 계정(README §16.4 — registry의 canonical 7종): total_assets, total_liabilities,
total_equity, revenue, operating_income, net_income, operating_cash_flow.

**concept 매칭** — B2 facts는 `concept_namespace`(uri)와 `concept_local_name`을 보존한다.
registry `accepted_concepts`("prefix:Local")를 (namespace 계열, local)로 해석해 매칭:
- `ifrs-full:X` → namespace uri에 `xbrl.ifrs.org`(또는 `ifrs`) 포함 ∧ local == X
- `dart:X` → namespace uri에 `dart` 포함 ∧ local == X
- prefix 문자열 자체에 의존하지 말 것(문서마다 다를 수 있음 — B2 fixture_altprefix가 이 케이스).

**period 매칭** — A4 normalized_facts의 REPORTED 행이 가진 (period_type, period_start,
period_end)와 XBRL context를 대조: BS는 instant == period_end, IS·CIS·CF는
duration (start, end) 정확 일치. (A4 값은 원본 semantics를 보존하므로 XBRL의
누적/3개월 context 중 정확히 하나와 만난다 — 불일치 시 아래 상태 분류.)

**scope 매칭** — DATA_NOTES B1+B2 실측 ②의 개정 규칙:
context의 dimension이 **정확히 1개**이고 axis local ==
`ConsolidatedAndSeparateFinancialStatementsAxis`이며 member local이
CFS→`ConsolidatedMember` / OFS→`SeparateMember`인 context만 채택.
(README §10.1의 "dimension 없는 기본 context" 규칙은 실데이터에 없음 — docstring에 근거 기록.)
실제 member local명은 구현 중 실데이터로 확인해 상수화하고 보고에 기록하라.

선택 결과: 정확히 1개 fact → 값 비교로. 0개/2개 이상 → 상태 분류(§4).

## 4. compare.py — 비교·상태 분류

README §16.4 `ReconciliationResult` 스키마 그대로 (major_account_value는 None).
값은 XBRL `raw_value`를 `Decimal`로, API 값은 A4 Int64를 Decimal로 — float 경유 금지.

| 조건 | status |
|---|---|
| 후보 fact 정확히 1 ∧ 차이 == 0 | `MATCH` |
| 〃 ∧ 차이 ≤ 허용 오차(abs 1e6 KRW **또는** rel 0.1%, `<=` 판정 — DATA_NOTES A4-④ 경계 사례) | `ROUNDING_DIFFERENCE` |
| 〃 ∧ 허용 오차 초과 | `REQUIRES_REVIEW` (absolute/relative_difference 기록) |
| concept 매칭 fact 자체가 0개 | `MISSING_IN_XBRL` |
| concept은 있으나 period 일치 context 없음 | `CONTEXT_MISMATCH` |
| period는 맞으나 scope 조건(연결/별도 축 단독) 불충족 | `SCOPE_MISMATCH` |
| 후보 2개 이상(중복) | `REQUIRES_REVIEW` (reason에 후보 수) |
| API 쪽 값 없음(A4 미커버) | `MISSING_IN_API` |

(`ACCOUNT_MAPPING_MISMATCH`는 이번 자동 분류에서 직접 산출하지 않되, REQUIRES_REVIEW의
reason 후보 문자열로 예약 — README §16.4 상태 목록 유지.)

## 5. pipeline.py — 배치·리포트

```python
def reconcile_all(
    corp_code: str, *, data_dir: Path, scopes=(FsDiv.CFS, FsDiv.OFS),
) -> ReconciliationReport:
```

- 범위: 수집된 **전 정기보고서**(연간 5 + 분·반기 — rcept_no 22건) × scopes × 7계정.
  A4 normalized_facts에 있는 (계정, 기간, scope) REPORTED 행이 기준 목록이다.
- 저장(README §19.7): `data/analytics/reconciliation/{corp_code}/reconciliation_report.json`
  (요약: 상태별 카운트, 계정×연도 매트릭스) + `reconciliation_failures.csv`
  (MATCH·ROUNDING 외 전 행). 실행마다 덮어씀.
- 로그에 상태별 집계 출력.

## 6. 테스트

### unit (`tests/unit/reconciliation/`, 오프라인 — 소형 합성 parquet fixture를 conftest에서 생성)

| 대상 | 케이스 |
|---|---|
| xbrl_select | ifrs-full/dart namespace 계열 매칭(상이 prefix 포함) / instant·duration period 매칭 / 연결·별도 축 단독 context만 채택(추가 dimension 붙은 context 배제, dimension 0개 context 배제) / 후보 0·1·2개 각 경로 |
| compare | MATCH(차이 0) / ROUNDING(±1e6 경계 `<=`) / REQUIRES_REVIEW(초과, 차이 기록) / Decimal 정밀 비교(큰 KRW 값 float 오염 없음) |
| pipeline | 상태 집계·리포트 파일 생성 / 파싱 실패 rcept 건너뛰고 기록 |

### integration (`tests/integration/test_reconciliation.py`, DATA_DIR — 실데이터 없으면 skip)

- 미파싱 rcept 자동 파싱 후 전량 대조 실행.
- **기대(README §19.7 "일정 오차 이상이면 실패 처리")**: 연간 5개년 × CFS·OFS × 7계정에서
  `MATCH + ROUNDING_DIFFERENCE` 비율 100% — 미달 시 테스트 실패가 아니라
  **REQUIRES_REVIEW 목록을 보고에 포함하고 xfail 사유 명시** (원인이 정정공시 Current View
  차이일 수 있음 — B4 소관). 분·반기는 커버리지·상태 분포를 보고만.

## 7. DoD

1. ruff·mypy strict·pytest 전부 통과 (워크트리 전체 스위트)
2. 실데이터 대조 실행 완료 — 상태별 집계표를 보고에 포함 (연간·분기 분리)
3. README §34의 서사("API 결과와 XBRL 원본 수치를 교차검증")를 실증하는 리포트 파일 생성
4. 발견 사항(REQUIRES_REVIEW 원인 추정, member local명 실측, registry 개정 필요 여부)을 DATA_NOTES 후보로 보고
