# A1 구현 명세 — DART 기업·공시 식별 (README §31 Milestone 1)

- 근거 명세: README §6.1(고유번호), §6.2(공시검색), §19.1~19.2(파이프라인 P1-01/02), §27(오류 처리), §30(보안)
- 비범위: 재무제표 API(A2), XBRL 다운로드(B1), 정정공시 버전 그래프 완성(B4 — 여기서는 식별·표시까지만)
- 새 외부 의존성 **추가 금지**: httpx·pydantic·typer·rich·PyYAML로 충분하다. XML 파싱은 표준 라이브러리 `xml.etree.ElementTree`, ZIP은 `zipfile`.

## 0. 모듈 배치

```text
src/research_backtest/core/dart/__init__.py
src/research_backtest/core/dart/client.py             # DartClient: 인증·재시도·오류 매핑
src/research_backtest/core/dart/models.py             # DartFiling, ResolveResult 등
src/research_backtest/core/dart/corp_code.py          # 고유번호 파일 수집·캐시·기업 resolve
src/research_backtest/core/dart/disclosure_search.py  # 정기보고서 검색·분류
```

CLI는 `src/research_backtest/app/cli.py`의 `resolve-company` 스텁을 실구현으로 교체한다.

## 1. client.py — DartClient

```python
class DartClient:
    def __init__(self, api_key: str, *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None) -> None: ...
    def get_json(self, path: str, **params: str) -> dict[str, Any]: ...
    def get_bytes(self, path: str, **params: str) -> bytes: ...
    def close(self) -> None: ...   # context manager(__enter__/__exit__)도 지원
```

- base_url은 `core.constants.DART_BASE_URL`. `transport` 주입은 테스트(MockTransport)용.
- 모든 요청 params에 `crtfc_key` 자동 주입.
- `get_json`: 응답 JSON의 `status`가 `"000"`이 아니면 `DartApiError(status)`를 raise. (013/014 처리는 호출부 책임 — `err.is_no_data`로 구분.)
- `get_bytes`: 응답 선두가 ZIP magic(`PK\x03\x04`)이면 bytes 반환. 아니면 오류 XML/JSON이므로 본문에서 status를 추출해 `DartApiError`를 raise (README §19.4 "오류 응답을 ZIP으로 오인하지 않음" 원칙의 선반영).
- **재시도** (README §27.2~27.3): 대상 = `DartApiError.retryable`(020/800/900), HTTP 429/5xx, `httpx.TimeoutException`·`httpx.TransportError`. backoff 1→2→4→8초, 최대 4회. sleep 함수는 주입 가능하게 하라(테스트에서 즉시 반환). 재시도 소진 시:
  - DART 상태 코드 오류였으면 마지막 `DartApiError` 그대로 raise
  - 네트워크/HTTP 오류였으면 `DartTransportError`(신설, 아래 §5) raise
- **보안** (README §30.2): 어떤 예외 메시지·로그에도 `crtfc_key` 값이 포함되면 안 된다. httpx 예외 문자열에는 전체 URL(쿼리 포함)이 들어갈 수 있으므로, 감싸서 던질 때 `redact(text, secret)` 헬퍼로 키 값을 `***`로 치환하라. 로거(`r2b.dart`)에는 path·키 제외 params·status만 기록.

## 2. corp_code.py — 고유번호 파일과 기업 resolve

### 2.1 수집·캐시 (README §6.1, §8.3 멱등성 취지)

- `GET /api/corpCode.xml` → ZIP 바이너리. ZIP 내 XML 엔트리는 **파일명을 고정 가정하지 말고** 첫 `.xml` 엔트리를 사용(README §8.1 취지).
- 캐시 디렉토리: `{settings.data_dir}/cache/dart/corp_code/`
  - `response.zip` — 원본 그대로 보존
  - `corps.jsonl` — 파싱 결과 (한 줄 = `DartCorporation` JSON)
  - `meta.json` — `downloaded_at`(KST ISO8601), `count`, `sha256`(zip), `source: "OPEN_DART_CORP_CODE"`
- 갱신 규칙: `meta.json`의 `downloaded_at`이 `configs/dart.yaml`의 `corp_code_cache.refresh_days`(=7)보다 오래됐거나, `force=True`(CLI `--refresh-corp-codes`)일 때만 재다운로드. 그 외에는 캐시 사용.
- XML 항목 필드: `corp_code`(8자리), `corp_name`, `corp_eng_name`, `stock_code`(6자리 또는 공백), `modify_date`. `stock_code`는 strip 후 빈 문자열이면 `None`.
- 기존 `core/models.py`의 `DartCorporation`에 `corp_eng_name: str | None = None` 필드를 추가하라.

### 2.2 기업명 정규화 (README §6.1)

`normalize_corp_name(name: str) -> str`:
1. `unicodedata.normalize("NFKC", name)` (전각·㈜ 등 호환문자 정리 — ㈜는 NFKC로 `(주)`가 된다)
2. casefold
3. `주식회사`, `(주)` 제거
4. 공백 전부 제거
5. 다음 특수문자 제거: `. , · & - ' "`

원본 기업명은 `DartCorporation.corp_name`에 그대로 보존한다(정규화 값은 인덱스에만 사용).

### 2.3 resolve 규칙 (README §19.1)

```python
class ResolveResult(BaseModel):
    matched: DartCorporation | None
    candidates: list[DartCorporation]   # 다중 후보 시 (상장 우선 정렬, 최대 10)
    method: Literal["STOCK_CODE", "EXACT_NAME", "SUBSTRING", "NOT_FOUND", "AMBIGUOUS"]
```

`CorpCodeRegistry.resolve(query: str) -> ResolveResult` 우선순위:
1. `^\d{6}$` → `stock_code` 정확 일치 (`STOCK_CODE`)
2. 정규화 기업명 정확 일치 — `corp_name`과 `corp_eng_name` 모두 인덱싱. 일치가 여럿이면 **상장기업(stock_code 보유) 우선**; 상장 1개면 matched, 상장이 둘 이상이면 `AMBIGUOUS`(candidates만 채움)
3. 정규화 부분일치(contains) — 결과가 정확히 1개면 matched(`SUBSTRING`), 여럿이면 `AMBIGUOUS`, 없으면 `NOT_FOUND`

알려진 한계(명세로 기록, 구현 불요): 음차 별칭(예: "에스케이하이닉스" ↔ 등기명 표기 차이)은 정규화로 해결되지 않을 수 있다 → 후보 제시 + 종목코드 재시도 안내로 커버. alias 테이블(configs)은 후순위.

## 3. disclosure_search.py — 정기보고서 검색 (README §6.2, §19.2)

- `GET /api/list.json` params: `corp_code`, `bgn_de`, `end_de`(YYYYMMDD), `last_reprt_at="N"`(**원본 공시도 포함** — PIT 재현에 필요), `pblntf_ty="A"`(정기공시), `page_no`, `page_count="100"`. `total_page`까지 순회해 병합.
- status 013(조회 데이터 없음)은 예외가 아니라 **빈 리스트**로 처리 (`DartApiError.is_no_data` 활용).

### 3.1 모델

```python
class DartFiling(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str | None
    report_nm: str
    rcept_no: str
    flr_nm: str
    rcept_dt: date          # API의 YYYYMMDD를 date로
    rm: str | None

    # 파생 필드 (report_nm 파싱)
    report_type: PeriodicReportType | None   # ANNUAL/HALF/Q1/Q3, 정기보고서 아니면 None
    fiscal_period_end: date | None           # "(2024.12)" → 2024-12-31 (해당 월 말일)
    is_correction: bool                      # "[…정정…]" 프리픽스 여부
    correction_kind: str | None              # 예: "기재정정", "첨부정정"
```

`PeriodicReportType`은 `core/constants.py`에 `StrEnum`으로 추가하고 `ReprtCode`와의 매핑 dict도 함께 둔다 (ANNUAL↔11011, HALF↔11012, Q1↔11013, Q3↔11014).

### 3.2 분류 규칙

- `report_nm`에서 `[...]` 프리픽스들을 제거한 본문으로 판단: `사업보고서`→ANNUAL, `반기보고서`→HALF, `분기보고서`→Q1 또는 Q3.
- 분기 구분: `fiscal_period_end`의 월로 판단 — 3월→Q1, 9월→Q3. **12월 결산 가정**이며 이 한계를 docstring에 명시(비12월 결산 지원은 후순위, MVP 기업은 12월 결산).
- 프리픽스 파싱: `[기재정정]`, `[첨부정정]`, `[첨부추가]` 등 — `정정` 포함 시 `is_correction=True`, 프리픽스 문자열을 `correction_kind`에 보존.

### 3.3 조회 함수

```python
def find_periodic_filings(
    client: DartClient, corp_code: str, *, as_of_date: date, lookback_years: int = 5
) -> list[DartFiling]:
```
- `bgn_de = as_of_date - lookback_years년`, `end_de = as_of_date`.
- **PIT 방어 필터**: `rcept_dt <= as_of_date`가 아닌 항목은 제거(end_de가 보장하더라도 명시적으로 필터하고 테스트로 고정 — README §19.2 "분석 기준일 이후 공시 제외").
- 정렬: `rcept_dt` 내림차순.
- 헬퍼: `latest_filing(filings, report_type)` — 해당 유형의 최신(접수일 기준) 1건 또는 None.

## 4. CLI — `resolve-company` 실구현 (README §31 M1 완료 조건)

```bash
r2b resolve-company --company "SK하이닉스" [--as-of-date 2026-07-14] [--refresh-corp-codes]
```

동작: resolve → (매칭 시) 최근 2년(`lookback_years=2`) 정기보고서 검색 → rich 테이블 출력:
- 기업: `corp_code`, `stock_code`, `corp_name`, 상장 여부
- 최근 사업보고서: `rcept_no`, `rcept_dt`, `report_nm`
- 최근 분기·반기보고서: `rcept_no`, `rcept_dt`, `report_nm` (분기·반기 중 최신 1건)

종료 코드: `0` 성공 / `1` NOT_FOUND 또는 AMBIGUOUS(후보 테이블을 출력하고 "종목코드로 재시도" 안내) / `3` ConfigError(키 미설정 — 예외 트레이스가 아니라 친절한 한 줄 메시지). `--as-of-date` 기본값은 오늘(KST).

## 5. core 공용 코드 변경

- `core/exceptions.py`에 추가:
  ```python
  class DartTransportError(ResearchBacktestError):
      """네트워크·HTTP 계층 오류로 재시도가 소진된 경우 (키 값은 redact된 메시지)."""
  ```
- `core/config.py`에 `configs/dart.yaml` 로더 추가:
  ```python
  def load_dart_config(path: Path = Path("configs/dart.yaml")) -> DartConfig: ...
  ```
  `DartConfig`는 pydantic 모델(timeout_seconds, retry.max_attempts, retry.backoff_seconds, corp_code_cache.refresh_days). client·corp_code가 이 값을 사용한다(하드코딩 금지).

## 6. 테스트 명세

### unit (오프라인 — 네트워크 호출 금지, httpx.MockTransport 사용)

fixtures: `tests/fixtures/dart_api/`에 corpCode 샘플 XML(5~6개 기업: 상장 2, 비상장 동명이인 1 포함), `list.json` 샘플(사업/반기/분기 + `[기재정정]` 1건 + 2페이지 페이지네이션 케이스). ZIP fixture는 파일로 두지 말고 테스트 헬퍼에서 `zipfile`로 인메모리 생성.

| 대상 | 케이스 |
|---|---|
| normalize | `"(주)SK하이닉스"` ≡ `"SK하이닉스"`, `"㈜"` NFKC 처리, 공백·casefold |
| resolve | 6자리 종목코드 정확 일치 / 정규화명 일치 / 상장 우선 / 동명 다중 → AMBIGUOUS / 미존재 → NOT_FOUND |
| corp_code 캐시 | 첫 호출 다운로드 → 두 번째 호출 네트워크 미발생(transport 호출 횟수로 검증) / refresh_days 경과 시 재다운로드 / force 재다운로드 / meta.json sha256 기록 |
| client | status 000 정상 / 013 raise + `is_no_data` / 020 → 재시도 후 성공 / 010 → 즉시 실패(재시도 없음, 호출 1회 검증) / HTTP 500 → 재시도 / ZIP magic 아님 → DartApiError / **예외 문자열에 키 값 부재 assert** |
| disclosure | 유형 분류 / `[기재정정]` 플래그·kind / `fiscal_period_end` 파싱 / PIT 필터(rcept_dt > as_of 제외) / 페이지네이션 병합 / 013 → 빈 리스트 |
| CLI | 성공 경로(모든 계층 mock) exit 0 / AMBIGUOUS exit 1 / 키 미설정 exit 3 |

### integration (`@pytest.mark.integration` + 키 없으면 `pytest.skip`)

- 고유번호 파일 실다운로드 → `"SK하이닉스"` resolve → `corp_code == "00164779"`, `stock_code == "000660"` (README §8.2 예시와 일치). `"000660"` 쿼리도 동일 결과.
- 공시검색 실호출 → 최근 사업보고서 1건 이상 존재, `report_type == ANNUAL`.

## 7. 완료 조건 (DoD)

1. `make check`(ruff + ruff format + mypy strict + pytest) 전부 통과 — mypy strict를 깨지 않는다.
2. unit 테스트가 네트워크 없이 통과한다.
3. `.env`의 실키로 integration 테스트 통과.
4. `r2b resolve-company --company "SK하이닉스"` 실행 시 corp_code·stock_code·최근 사업보고서/분기보고서 접수번호가 출력된다 (README M1 완료 조건).
5. 캐시 동작: 같은 명령 재실행 시 고유번호 파일을 다시 받지 않는다.
6. 로그·예외·출력 어디에도 API 키가 노출되지 않는다.

## 8. 구현 노트

- 코드 스타일: 기존 파일과 동일 — 한국어 docstring + README § 참조, 식별자는 영어.
- 날짜는 전부 `datetime.date`로 다루고 API 경계에서만 YYYYMMDD 문자열 변환.
- `downloaded_at`은 `ZoneInfo("Asia/Seoul")` 기준.
- corps.jsonl 로딩(약 10만 행)은 모듈 수준 캐시 없이 Registry 인스턴스가 보유(CLI 1회 실행 수명이면 충분).
