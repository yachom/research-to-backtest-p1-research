# A2 구현 명세 — DART 전체 재무제표 API 수집 (README §31 Milestone 2)

- 근거 명세: README §6.4(전체 재무제표 API), §7.3(CFS·OFS 모두 저장), §13.2(출처 구분), §14(수집 순서·기간), §19.3(P1-03), §27(오류 처리)
- 비범위: XBRL ZIP(B1), 계정 정규화·시계열(A4), 정정공시 버전 그래프(B4)
- 새 외부 의존성 **추가 금지**. A1과 동일한 스타일·품질 게이트(mypy strict, ruff) 유지.

## 0. 모듈 배치

```text
src/research_backtest/core/dart/financial_api.py   # 신규 — 수집기 전체
```

CLI는 `app/cli.py`의 `collect-financials` 스텁을 실구현으로 교체. `resolve-company`와 중복되는 "기업 식별 실패 시 후보 출력 후 exit" 로직은 cli.py 내 공용 헬퍼 `_resolve_or_exit(...)`로 추출해 두 명령이 공유한다.

## 1. 설계상 중요한 사실 (docstring으로 명문화할 것)

**전체 재무제표 API는 접수번호(rcept_no)를 입력받지 않는다** — `(corp_code, bsns_year, reprt_code, fs_div)`로 조회하며, 정정공시가 있으면 **현재 기준 최신(Current View) 수치**가 반환된다(README §15). 따라서:

- 응답의 `rcept_no`(각 행에 포함)를 반드시 보존한다 — 어떤 공시 버전에서 온 수치인지의 유일한 근거.
- 당시 투자자가 본 값(Point-in-Time View)의 완전한 재현은 XBRL 원본(B1)·버전 관리(B4)에서 완성한다. A2는 이 한계를 모듈 docstring에 명시한다.

## 2. API 사양 (README §6.4)

- `GET /api/fnlttSinglAcntAll.json`
- 요청 모델 — README §6.4 그대로:

```python
class DartFullFinancialRequest(BaseModel):
    corp_code: str
    bsns_year: str                       # "2024"
    reprt_code: ReprtCode                # 11011/11012/11013/11014 (core.constants)
    fs_div: FsDiv                        # CFS/OFS (core.constants)
```

- 응답 행 모델 `DartFinancialAccountRaw` — README §6.4의 필드 목록 그대로 (rcept_no, reprt_code, bsns_year, corp_code, sj_div, sj_nm, account_id, account_nm, account_detail, thstrm_nm, thstrm_amount, thstrm_add_amount, frmtrm_nm, frmtrm_amount, frmtrm_q_nm, frmtrm_q_amount, frmtrm_add_amount, ord, currency). `model_config = ConfigDict(extra="allow")` — API 필드 추가에 깨지지 않게. **이 모델은 검증·인벤토리용이며, 저장은 응답 원문 그대로다(§3).**
- 2015년 경계(README §6.4): `from_year < 2015`는 수집 함수에서 `ValueError`, CLI에서 친절한 메시지 + `typer.BadParameter`.

## 3. 저장 구조 — raw 계층 (README §7.3, §8 취지)

```text
data/raw/dart/financials/{corp_code}/
├── {bsns_year}_{reprt_code}_{fs_div}.json        # API 응답 본문 "원문 그대로"(text)
├── {bsns_year}_{reprt_code}_{fs_div}.meta.json   # 수집 메타 (아래)
├── financial_api_raw.jsonl                        # 병합본 — README §19.3 출력물
└── collection_report.json                         # 최근 수집 실행 요약
```

### 3.1 원문 보존

- 응답 본문을 **수신한 텍스트 그대로** 저장한다(재직렬화 금지 — sha256 재현성).
- 이를 위해 `DartClient`에 메서드 1개 추가:
  ```python
  def get_json_text(self, path: str, **params: str) -> tuple[dict[str, Any], str]:
      """get_json과 동일한 status 처리 + 응답 원문 텍스트를 함께 반환한다."""
  ```
  기존 `get_json`은 `get_json_text`에 위임하도록 리팩터링(동작 불변, 기존 테스트 통과 유지).

### 3.2 meta.json

```json
{
  "params": {"corp_code": "...", "bsns_year": "2024", "reprt_code": "11011", "fs_div": "CFS"},
  "status": "000",
  "fetched_at": "<KST ISO8601>",
  "sha256": "<응답 원문 텍스트의 sha256>",
  "row_count": 312,
  "rcept_nos": ["20250318001234"],
  "source": "DART_FULL_FINANCIAL_API"
}
```

- `source`는 README §13.2의 출처 구분 값.
- **013(조회 데이터 없음)도 meta로 기록한다(negative cache)** — `status: "013"`, `row_count: 0`, sha256·rcept_nos 생략, 데이터 파일 없음. 미제출 보고서(예: 아직 접수 전인 당해년 사업보고서)를 매 실행마다 재조회하지 않기 위함.
- 쓰기 순서: 데이터 파일 → meta.json (**meta가 커밋 마커**). meta 없이 데이터 파일만 있으면 캐시 미스로 간주하고 재수집.

### 3.3 캐시 규칙 (README §8.3 멱등성, §19.3 "캐시 지원")

- 캐시 히트 = meta.json 파싱 가능 ∧ `status ∈ {"000","013"}` ∧ (`status=="000"` → 데이터 파일 존재). 히트면 API 호출 없음.
- `--force-download`(README §8.3과 동일한 플래그) 시 무시하고 재수집.
- 캐시 판정에서 데이터 파일 내용은 다시 파싱하지 않는다(손상 의심 시 사용자가 `--force-download`).

### 3.4 financial_api_raw.jsonl (README §19.3 출력물)

- 수집 실행 말미에 **디스크의 원문 파일들로부터 결정적으로 재생성**한다(멱등).
- 순서: `bsns_year` 오름차순 → `reprt_code` (11013, 11012, 11014, 11011 선언 순서) → `fs_div` (CFS, OFS) → 응답 내 행 순서.
- 한 줄 형식 — 응답 행에는 `fs_div`가 없으므로 provenance를 감싸서 보존:
  ```json
  {"bsns_year": "2024", "reprt_code": "11011", "fs_div": "CFS", "row": { ...응답 행 그대로... }}
  ```

## 4. 수집기

```python
class RequestOutcome(BaseModel):
    bsns_year: str
    reprt_code: ReprtCode
    fs_div: FsDiv
    result: Literal["FETCHED", "CACHED", "NO_DATA", "NO_DATA_CACHED"]
    row_count: int
    sj_div_counts: dict[str, int]        # {"BS": 120, "IS": 40, ...} — M2 DoD "BS·IS·CIS·CF·SCE 분리" 증빙
    rcept_nos: list[str]

class CollectionSummary(BaseModel):
    corp_code: str
    fetched_at: str                      # KST ISO8601
    outcomes: list[RequestOutcome]

def collect_financials(
    client: DartClient,
    corp_code: str,
    *,
    from_year: int,
    to_year: int,
    fs_divs: Sequence[FsDiv] = (FsDiv.CFS, FsDiv.OFS),
    out_dir: Path,                       # = {data_dir}/raw/dart/financials/{corp_code}
    force: bool = False,
    min_interval_seconds: float = 0.1,
    sleep: Callable[[float], None] = time.sleep,
) -> CollectionSummary:
```

- 연도 × 4개 reprt_code × fs_divs 전체를 순회(연 8요청 × 5개년 = 40요청 수준 — 쿼터 문제 없음).
- **실제 API 호출 사이에만** `min_interval_seconds` 대기(캐시 히트는 대기 없음). 값은 `configs/dart.yaml`의 `request.min_interval_seconds`(신설, 기본 0.1)에서 오고 `DartConfig`에 필드 추가.
- `sj_div_counts`: FETCHED 시 응답 행 파싱으로 계산해 meta에도 기록, CACHED 시 meta에서 읽음(구 meta에 없으면 빈 dict 허용).
- 013 → `NO_DATA`(신규 기록)/`NO_DATA_CACHED`(기존 negative cache). 그 외 DartApiError는 전파(전체 실행 중단 — 부분 실패 은폐 금지).
- 수집 후 `financial_api_raw.jsonl` 재생성, `collection_report.json`에 CollectionSummary 저장.

## 5. CLI — `collect-financials` 실구현

```bash
r2b collect-financials --company "SK하이닉스" --from-year 2021 --to-year 2025 \
  [--scopes CFS OFS] [--force-download] [--include-xbrl]
```

- 기업 식별: `_resolve_or_exit` (resolve-company와 동일 규칙 — AMBIGUOUS/NOT_FOUND 시 후보 출력 + exit 1).
- `--scopes` 값 검증: CFS/OFS 외 값이면 `typer.BadParameter`. 기본은 둘 다.
- `--from-year > --to-year`, `--from-year < 2015`는 `typer.BadParameter`.
- `--include-xbrl`: "[yellow]XBRL 수집은 Milestone B1에서 구현됩니다 — 이번 실행에서는 무시[/yellow]" 경고 후 계속(실패 아님).
- 출력: rich 테이블 — 행 = (연도, 보고서, scope, 결과, 행수, BS/IS/CIS/CF/SCE 행수 요약). 마지막에 저장 경로·jsonl 라인 수 출력.
- 종료 코드: 0 성공(NO_DATA 포함 — 미제출은 정상) / 1 식별 실패·DART 오류 / 3 설정 오류. `pretty_exceptions_show_locals=False` 유지.

## 6. 테스트 명세

### unit (오프라인, httpx.MockTransport — A1의 conftest 패턴 재사용)

fixture: `tests/fixtures/dart_api/fnltt_singl_acnt_all_sample.json` — 행 10~15개, sj_div 5종(BS/IS/CIS/CF/SCE) 포함, 동일 rcept_no. 013 응답은 인라인 dict로 충분.

| 대상 | 케이스 |
|---|---|
| fetch·저장 | 원문 텍스트 그대로 저장(파일 내용 == 응답 본문), meta의 sha256 일치, rcept_nos·row_count·sj_div_counts 기록 |
| 캐시 | 2회차 실행 → transport 호출 0회, 결과 CACHED / force=True → 재호출 / **데이터 파일만 있고 meta 없음 → 재수집** |
| negative cache | 013 → meta(status 013)만 생성 + NO_DATA, 2회차 NO_DATA_CACHED + 호출 0회 |
| jsonl | 병합 순서 결정성(연도→reprt_code→fs_div→행), provenance 필드, 총 라인 수 = row_count 합 |
| 경계 | from_year=2014 → ValueError / from_year > to_year → 오류 |
| interval | FETCHED 2건 사이 sleep 호출 검증(주입 sleep), CACHED만 있으면 sleep 0회 |
| client | get_json_text가 (payload, 원문 text) 반환, get_json 동작 불변(기존 테스트 그대로 통과) |
| CLI | happy path(모든 계층 mock) exit 0 + 테이블 출력 / --scopes 잘못된 값 → 오류 / --include-xbrl 경고 후 정상 진행 / 식별 실패 exit 1 / 키 미설정 exit 3 |

### integration (`@pytest.mark.integration`, 키 없으면 skip, **out_dir은 tmp_path**)

1. SK하이닉스(00164779) 2024·11011·CFS 실호출 → status 000, row_count > 100, sj_div에 `{"BS","CF","SCE"}` 포함 ∧ (`"IS"` 또는 `"CIS"`) 포함
2. 같은 요청 재실행 → CACHED, 호출 없음(전송 계층 카운트 불가하므로 meta mtime 불변으로 검증)
3. 2026·11011·CFS(아직 미제출) → NO_DATA로 기록되고 negative cache 동작

## 7. 완료 조건 (DoD — README §31 M2 매핑)

1. `make check` 전부 통과 (ruff·format·mypy strict·pytest, 기존 테스트 포함).
2. `r2b collect-financials --company "SK하이닉스" --from-year 2021 --to-year 2025` 실행 성공 — **최근 5개년 CFS·OFS 수집** (M2 "SK하이닉스 최근 5개년").
3. 수집 결과에 BS·IS·CIS·CF·SCE가 sj_div_counts로 분리 집계된다 (M2 "BS·IS·CIS·CF·SCE 분리").
4. 원문 텍스트 + sha256 meta로 raw 응답이 재현 가능하다 (M2 "raw 응답 재현 가능").
5. 동일 명령 재실행 시 API 호출 0회 — 전부 CACHED/NO_DATA_CACHED (M2 "동일 요청 중복 호출 방지").
6. 오류코드 처리: 013은 정상 흐름(negative cache), 그 외 DartApiError 전파 (§19.3 "API 오류코드 처리, 재시도 및 캐시 지원" — 재시도는 A1 client 재사용).
7. 키 비노출 원칙 유지.

## 8. 구현 노트

- 코드 스타일: 한국어 docstring + README § 참조, mypy strict 통과.
- `fetched_at`은 `ZoneInfo("Asia/Seoul")`.
- reprt_code 순회 순서는 `ReprtCode` 선언 순서(Q1→HALF→Q3→ANNUAL)를 사용해 연내 시간 순서와 일치시킨다.
- 파일명의 reprt_code는 코드 값("11011")을 사용한다 — 예: `2024_11011_CFS.json`.
- `collection_report.json`은 실행마다 덮어쓴다(이력 보관은 로깅·run manifest의 몫, README §29 — 후속).
