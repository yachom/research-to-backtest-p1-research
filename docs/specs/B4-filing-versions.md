# B4 구현 명세 — 정정공시 버전 관리·Point-in-Time View (README §15, Wave 2)

- 근거: README §15.1~15.3(원칙·FilingVersion·분석 모드), §4(PIT 원칙), A1 `DartFiling`(is_correction·correction_kind 이미 파싱됨), docs/DATA_NOTES.md B1+B2 실측 ⑤(**실데이터 정정 쌍**: 2020.12 사업보고서 원본 20210322000782 + [기재정정] 20210330000776)
- 비범위: 정정 전·후 수치 diff(후순위), XBRL 버전별 재파싱(B3·후속에서 소비), CLI(메인 세션)

## 0. 파일 소유권 (D8 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/core/dart/filing_versions.py` (신규 — core/dart의 기존 파일 수정 금지)
- `tests/unit/test_filing_versions.py`, `tests/integration/test_filing_versions_live.py`
- **금지**: `core/dart/{client,corp_code,disclosure_search,financial_api,xbrl_downloader}.py`, `core/dates.py`, 공유 테스트 파일·conftest

## 1. 모델 (README §15.2 그대로 + 파생)

```python
class FilingVersion(BaseModel):
    rcept_no: str
    original_rcept_no: str | None      # 이 버전이 정정하는 계열의 최초 원본 (원본이면 None)
    filing_date: str                   # rcept_dt ISO (원문 §15.2 필드명 유지)
    report_name: str
    revision_type: str | None          # correction_kind (예: "기재정정") — 원본이면 None
    is_latest_version: bool
    supersedes_rcept_no: str | None    # 직전 버전 rcept_no (원본이면 None)

class FilingVersionGroup(BaseModel):
    report_type: PeriodicReportType
    fiscal_period_end: date
    versions: list[FilingVersion]      # filing_date(→rcept_no) 오름차순
```

## 2. 버전 그래프 구축

```python
def build_version_groups(filings: Sequence[DartFiling]) -> list[FilingVersionGroup]:
```

- 그룹 키 = `(report_type, fiscal_period_end)` — A1이 이미 파싱한 값 사용. report_type
  또는 fiscal_period_end가 None인 filing은 그룹화 불가로 제외하고 개수 반환(로그).
- 그룹 내 정렬: `(rcept_dt, rcept_no)` 오름차순. 첫 항목이 원본(단, `is_correction=True`인
  항목만 있는 그룹이면 원본 미수집 케이스 — original_rcept_no=None으로 두되 리포트에 표시).
- 체인 연결: i번째의 `supersedes_rcept_no` = (i-1)번째 rcept_no,
  `original_rcept_no` = 첫 원본의 rcept_no, 마지막만 `is_latest_version=True`.
- 동일 그룹에 원본이 2개(is_correction=False 중복)면 DataValidationError가 아니라
  **REQUIRES_REVIEW 로그 + rcept_dt 순 처리**(방어적 — 실데이터 미관측 케이스).

## 3. Point-in-Time 선택 (README §15.3)

```python
def visible_version(
    group: FilingVersionGroup, *, as_of_date: date, calendar: TradingCalendar,
) -> FilingVersion | None:
    """분석 기준일에 이용 가능했던 최신 버전 (README §15.3 Point-in-Time View).

    이용 가능 판정은 available_from(접수일 다음 거래일, README §4.3) <= as_of_date.
    백테스트는 반드시 이 함수를 쓴다 — Current View(마지막 버전 무조건)는
    current_version()으로 별도 제공한다."""

def current_version(group: FilingVersionGroup) -> FilingVersion: ...
```

- available_from 계산은 `core.dates.available_from(filing_date, calendar)` 재사용.
- as_of 이전에 아무 버전도 없으면 None.

## 4. 저장·로드

- `data/normalized/dart/{corp_code}/filing_versions.json` — FilingVersionGroup 목록,
  `ensure_ascii=False, indent=2`. `save_version_groups`/`load_version_groups` 쌍.
- 빌드 함수: `build_and_save(corp_code, *, client, data_dir, as_of_date, lookback_years=6)` —
  A1 `find_periodic_filings` 호출(원본 포함 검색은 이미 `last_reprt_at="N"`) 후 그래프 저장.

## 5. 테스트

### unit (합성 DartFiling 목록으로)

| 케이스 |
|---|
| 원본 1 + 정정 2 체인: supersedes·original·is_latest 정확 |
| 정정 없음 그룹: 원본이 latest |
| 원본 미수집(정정만 존재): original None + 표시 |
| visible_version: as_of가 원본 available_from 전/원본과 정정 사이/정정 후 — 각각 None/원본/정정 (주말 낀 available_from 포함, WeekdayCalendar 사용) |
| current_version ≠ visible_version 사례 고정 |
| report_type None filing 제외 처리 |

### integration (DART_API_KEY·DATA_DIR — 없으면 skip)

- 실호출로 00164779 그래프 빌드 → **2020.12 사업보고서 그룹에서 원본 20210322000782와
  기재정정 20210330000776이 체인으로 연결**되고 정정이 latest임을 확인.
- `visible_version(as_of=2021-03-25)` == 원본, `(as_of=2021-04-05)` == 기재정정 —
  실데이터로 §15.3 재현(접수일 다음 거래일 규칙 적용: 3/22(월) 접수→3/23 이용가능, 3/30(화) 접수→3/31 이용가능).
- 저장 파일 생성·로드 왕복.

## 6. DoD

1. ruff·mypy strict·pytest 전부 통과 (워크트리 전체 스위트)
2. 실데이터 정정 쌍이 그래프·PIT 선택으로 재현됨 (integration)
3. README §15.3의 두 모드(Current/PIT)가 별도 함수로 제공되고 docstring에 백테스트=PIT 명시
4. 후속(B3 버전 인지 대조·A4 정정 반영) 인계 사항을 보고에 기록
