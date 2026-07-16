# B1+B2 구현 명세 — XBRL 원본 수집·파싱 (README §31 M3+M4, Wave 1)

- 근거: README §6.5(XBRL API), §8(저장 구조·manifest·멱등성), §9(파싱 명세), §10(Context 다양성 — 파싱은 전부 보존, 선택은 B3), §19.4~19.5(완료 조건)
- 입력: A1의 공시검색(실 API — 접수번호 확보), DartClient(§6.5 `fnlttXbrl.xml`은 get_bytes로 ZIP magic 검증 이미 내장)
- 비범위: Context 선택 규칙 적용·계정 표준화·API-XBRL 대조(B3), 주석 XBRL(후순위 §32)
- 새 의존성 금지 — XML은 표준 라이브러리 `xml.etree.ElementTree`(namespace 동적 처리 가능), ZIP은 `zipfile`.

## 0. 파일 소유권 (D8 병렬 규칙 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/core/dart/xbrl_downloader.py` (신규)
- `src/research_backtest/core/xbrl/**` (신규 패키지)
- `tests/unit/xbrl/**` (자체 conftest 허용), `tests/fixtures/xbrl/**`, `tests/integration/test_xbrl_pipeline.py`
- **금지**: `app/cli.py`(CLI 연결은 메인 세션), `core/dart/client.py` 등 기존 파일, `core/exceptions.py`(XbrlParseError 이미 존재), 공유 테스트 파일

## 1. B1 — XBRL 원본 수집 (`core/dart/xbrl_downloader.py`)

### 1.1 API·저장 (README §6.5, §8.1~8.3)

- `GET /api/fnlttXbrl.xml` params: `rcept_no`, `reprt_code` → ZIP binary. `DartClient.get_bytes` 사용(오류 XML 오인 방지 §19.4는 client가 이미 보장).
- 저장 — README §8.1 레이아웃 그대로:

```text
data/raw/dart/xbrl/{corp_code}/{rcept_no}/
├── response.zip          # 원본 무수정 보존
├── manifest.json         # §8.2 스키마 (아래)
├── extracted/            # 압축 해제 (ZIP 내부 파일명 고정 가정 금지)
└── checksum.sha256       # response.zip의 sha256 (텍스트 한 줄)
```

- manifest(§8.2 필드 그대로): corp_code, stock_code, rcept_no, reprt_code, report_name, filing_date(= rcept_no[:8]), downloaded_at(KST ISO8601), source="OPEN_DART_XBRL", http_status(=200), content_type, sha256.
- **ZIP 무결성 검증**: `zipfile.ZipFile.testzip()` 통과 후 압축 해제. 실패 시 부분 산출물 제거 후 DataValidationError.
- 압축 해제 보안: 경로 탈출(zip-slip) 방어 — entry 이름에 `..`·절대경로 포함 시 거부.
- **멱등성(§8.3)**: manifest.json 존재 ∧ checksum 파일과 response.zip sha256 일치 → 재다운로드 없음. `force=True`로 무시. manifest는 커밋 마커(마지막에 기록).
- 013/014(데이터·파일 없음)는 negative cache: `manifest.json`에 `"status": "013"` 형태로 기록하고 zip 없음 — 재실행 시 재조회 안 함.

### 1.2 배치 함수

```python
def download_xbrl_filings(
    client: DartClient, filings: Sequence[DartFiling], *, data_dir: Path,
    force: bool = False, min_interval_seconds: float = 0.1, sleep=time.sleep,
) -> list[XbrlDownloadOutcome]:   # rcept_no별 FETCHED/CACHED/NO_DATA(_CACHED)/실패사유
```

- `DartFiling.report_type` → `PERIODIC_REPORT_TO_REPRT_CODE`로 reprt_code 결정 (report_type None인 filing은 건너뛰고 기록).

## 2. B2 — XBRL 파싱 (`core/xbrl/`)

```text
core/xbrl/__init__.py
core/xbrl/discovery.py    # extracted/에서 instance 문서 탐색
core/xbrl/models.py       # XbrlFact·XbrlContext·XbrlUnit·XbrlDimension (README §9)
core/xbrl/parser.py       # instance 파싱
core/xbrl/store.py        # parquet 저장·로드
```

### 2.1 Instance 탐색 (README §8.1 "파일명 고정 가정 금지", §19.5)

- `extracted/` 재귀 탐색: 루트 요소가 `{http://www.xbrl.org/2003/instance}xbrl`인 XML 파일 전부 = instance 문서. 확장자 힌트(.xbrl 우선) 사용하되 의존하지 않는다.
- **복수 instance가 나올 수 있다**(연결/별도 분리 제출 가능성) — 전부 파싱하고 각 fact에 `source_file` 기록(README §9.2에 이미 필드 존재). 실제 구성(파일 수·명명)을 실측해 보고에 기록할 것.

### 2.2 파싱 규칙 (README §9 — 모델 필드는 §9.2~9.5 그대로)

- **Namespace 동적 처리**: prefix를 하드코딩하지 않는다. 각 요소의 QName `{uri}local`에서 uri·local 분리, concept_qname은 문서 선언 prefix 기반 `prefix:local`(prefix 미상이면 uri 축약 매핑) — concept_namespace(uri)와 concept_local_name을 항상 별도 보존하므로 prefix 표기는 참고용.
- Fact = xbrli 표준 네임스페이스(instance·linkbase) 밖의, `contextRef`를 가진 리프 요소. `unitRef`, `decimals`, `xsi:nil` 수집.
- Context(§9.3): entity identifier(+scheme), period(instant | startDate/endDate | forever), `segment`·`scenario` 내 `xbrldi:explicitMember`(dimension=axis_qname, text=member_qname)와 `typedMember`(내용 문자열화) → XbrlDimension 목록.
- Unit(§9.5): 단일 measure 또는 divide(unitNumerator/unitDenominator).
- Numeric 변환(§9.6 순서 그대로): is_nil → 쉼표·공백 제거 → 괄호 음수 → `Decimal` → decimals 저장. **모든 값을 float로 즉시 변환 금지** — raw_value(str)와 numeric_value를 함께 보존. scale 속성은 표준 XBRL instance에 없으므로 None 고정(iXBRL 전용 — docstring에 근거 기록).
- 실패는 `XbrlParseError`(원인 파일·요소 경로 포함).

### 2.3 저장 (README §19.5 출력)

`data/normalized/xbrl/{corp_code}/{rcept_no}/` 아래 4개 parquet:

- `xbrl_facts.parquet`: concept_qname, concept_namespace, concept_local_name, context_id, unit_id, raw_value(str), numeric_value(float64 — KRW 정수 범위는 float64로 정확, 정밀 비교는 raw_value로 가능), decimals(str), is_nil(bool), source_file
- `xbrl_contexts.parquet`: context_id, entity_identifier, entity_scheme, period_type(instant/duration/forever), instant_date, start_date, end_date, dimension_count
- `xbrl_units.parquet`: unit_id, measure, numerator, denominator
- `xbrl_dimensions.parquet`: context_id, axis_qname, member_qname, typed_member_value, container(segment/scenario)

파싱은 결정적(동일 입력 → 동일 출력, README M4 완료 조건) — 정렬 규칙(파일명→문서 순서)을 고정하라.

## 3. 테스트

### unit (`tests/unit/xbrl/`, 오프라인)

fixture: 손으로 만든 소형 instance XML 2종(`tests/fixtures/xbrl/`) — ① 표준+확장 네임스페이스 혼재, instant·duration context, 명시적 dimension 2개(segment/scenario 각각), nil fact, 음수·쉼표 값, KRW·shares·divide unit ② 다른 prefix 선언(같은 uri)으로 namespace 동적 처리 검증. ZIP fixture는 인메모리 생성(A1 conftest 패턴 참조하되 자체 conftest에 구현).

| 대상 | 케이스 |
|---|---|
| downloader | 저장 레이아웃·manifest 스키마 / sha256 일치 / 멱등(재호출 0회) / force / zip 손상 → 오류·부분 산출물 없음 / zip-slip 거부 / 013 negative cache |
| discovery | 비고정 파일명에서 instance 탐지 / 복수 instance / instance 아닌 XML 무시 |
| parser | fact 수·필드 정확(손계산 대조) / context 연결 / dimension 추출 / nil / 쉼표·음수·Decimal / 동일 concept 복수 context 보존(README M4) / prefix 상이 문서에서 동일 concept_namespace |
| store | parquet 왕복(스키마·행수) / 결정성(두 번 파싱 → 동일 bytes 또는 동일 DataFrame) |

### integration (`tests/integration/test_xbrl_pipeline.py` — DART_API_KEY 없으면 skip)

- SK하이닉스(00164779) **최근 사업보고서 1건**(A1 disclosure_search 실호출로 rcept_no 획득) 다운로드 → manifest·checksum 생성, 재실행 CACHED
- 파싱 → fact 1,000개 이상, ifrs-full 네임스페이스 존재, entity identifier에 corp 식별자 포함, instant·duration context 공존, 자산총계(local_name `Assets`, dimension 없는 context) numeric > 0
- **작업 디렉토리 주의**: 실 다운로드는 `data/`(심링크된 메인 데이터 디렉토리)에 저장 — 이후 마일스톤의 실데이터가 된다. 단 파싱 산출물 임시 검증은 tmp_path 사용 가능.

## 4. DoD

1. ruff·mypy strict·pytest 전부 통과 (본인 워크트리 전체 스위트)
2. 실 XBRL 1건 이상 다운로드·파싱 성공 (integration)
3. 동일 rcept_no 재실행 시 재다운로드 없음, 파싱 재실행 결과 동일
4. **배치 실행**: 2021~2025 정기보고서 전체(약 20건)의 XBRL 다운로드를 실제 수행해 `data/raw/dart/xbrl/00164779/`에 적재하고, 건별 결과(FETCHED/NO_DATA 등)를 보고에 포함 — B3의 실데이터가 된다 (다운로드만, 전체 파싱은 대표 1~2건)
5. ZIP 내부 실제 구성(instance 파일 수·명명 규칙·연결/별도 분리 여부)을 보고에 기록 — DATA_NOTES 후보
