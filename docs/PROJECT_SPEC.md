> **문서 이관 안내 (2026-07-15, C3' 문서 재편 — 구 README §25·정오표 6의 지시 이행)**
> 이 문서는 사용자가 작성한 기술 명세 원본(v1.0)의 **전문 보존본**이다. §1.1만
> 요구사항 v2(HITL — 1804_FEEDBACK.md)로 개정된 상태 그대로 옮겼다. 실행 방법은
> 재편된 `README.md`, 실행 계획·결정 기록은 `docs/MILESTONES.md`를 보라.

# Research-to-Backtest

## AI 기반 기업 리서치 및 투자전략 검증 시스템 개발 명세서

- 문서 버전: `v1.0`
- 프로젝트 유형: 채용 포트폴리오 / 금융 리서치 자동화 / 퀀트 백테스트
- 지원 시장: 대한민국 상장기업
- MVP 대상 기업: SK하이닉스 또는 삼성전자
- 핵심 데이터: OpenDART 공시·XBRL 재무제표, 주가, 투자자 수급, 산업 데이터
- 핵심 산출물:
  1. 기업·산업 분석 보고서
  2. 투자 가설
  3. 정형화된 매매전략
  4. 백테스트 결과 보고서

---

# 1. 프로젝트 개요

## 1.1 프로젝트 목적

> **(v1.1 개정, 2026-07-14 — Human-in-the-Loop 전환)** 본 프로젝트는 AI가 투자
> 판단을 대신하는 시스템이 아니다. DART·XBRL·시장 데이터를 구조화하고 분석
> 후보를 제시하되, **분석 관점과 투자 가설은 사용자가 직접 설정한다.** AI는
> 사용자의 가설을 실행 가능한 전략 규칙으로 변환하며, Python 백테스트가 이를
> 검증한다. 최종 가설의 채택·수정·기각 판단은 사용자가 내린다.
> 상세 명세: docs/HUMAN_IN_THE_LOOP.md, docs/AI_ROLE_BOUNDARY.md

사용자가 기업명과 분석 기준일을 입력하면 다음 과정을 수행한다.

```text
기업명·분석 기준일 입력
        ↓
데이터 수집 및 계산                        (Python)
        ↓
AI가 분석 후보와 상충 근거 정리              (AI)
        ↓
사용자가 분석 질문과 핵심 논지 작성           (사용자)
        ↓
사용자가 사용할 근거와 제외할 근거 선택        (사용자)
        ↓
사용자가 투자 가설 작성                     (사용자)
        ↓
AI가 가설을 측정 가능한 전략 DSL 초안으로 변환  (AI)
        ↓
사용자가 전략 규칙을 검토·수정·승인           (사용자)
        ↓
과거 데이터 기반 백테스트                   (Python)
        ↓
사용자가 결과를 해석하고 가설을 채택·수정·기각  (사용자)
```

AI의 역할은 다음과 같다.

- 공시·재무자료에서 사실과 후보 관계, 상충 근거 정리 (CandidateAnalysis)
- 참고용 가설 후보 제시 (승인된 가설이 아님)
- 승인된 사용자 가설의 전략 DSL 초안 변환
- 백테스트 결과의 사실 요약 초안

Python 엔진의 역할은 다음과 같다.

- 데이터 수집
- XBRL 파싱
- 재무계정 정규화
- 재무비율과 기술지표 계산
- 매매 신호 생성
- 백테스트
- 성과지표 계산

핵심 원칙은 다음과 같다.

> AI는 사실과 후보 관계를 정리하고 사용자의 가설을 구조화하는 보조 도구다.
> 분석 관점, 핵심 논지, 근거 선택, 투자 가설, 전략 승인, 결과 해석은 사용자가 담당한다.
> Python은 데이터 처리와 계산 및 검증을 담당한다.

---

# 2. 프로젝트 구성

전체 시스템은 두 개의 독립 프로젝트로 나눈다.

## Project 1. AI 기업 리서치 및 투자 가설 생성기

### 목적

기업명과 분석 기준일을 입력받아 다음 결과를 생성한다.

- 기업 식별 정보
- 재무제표 분석
- 공시 이벤트 분석
- 산업 및 시장 분석
- 투자 포인트
- 위험요인
- 투자 가설
- 근거가 연결된 기업분석 보고서

### 핵심 출력

```text
company_analysis.json
investment_hypothesis.json
research_report.md
evidence_manifest.json
```

---

## Project 2. 자연어 투자전략 변환 및 백테스트 엔진

### 목적

Project 1의 투자 가설 또는 사용자의 자연어 아이디어를 정형화된 전략으로 바꾼 뒤 과거 데이터로 검증한다.

### 핵심 출력

```text
strategy_spec.json
backtest_result.json
trade_log.csv
daily_portfolio.csv
backtest_report.html
```

---

# 3. 시스템 전체 입력

## 3.1 필수 입력

```python
from datetime import date
from pydantic import BaseModel


class ResearchRequest(BaseModel):
    company: str
    as_of_date: date
```

예시:

```json
{
  "company": "SK하이닉스",
  "as_of_date": "2025-12-31"
}
```

`company` 필드는 다음 값을 허용한다.

- 정식 기업명
- 약식 기업명
- 6자리 종목코드

예시:

```text
SK하이닉스
에스케이하이닉스
000660
```

---

## 3.2 선택 입력

```python
from typing import Literal


class ResearchOptions(BaseModel):
    market: Literal["KR"] = "KR"
    lookback_years: int = 5
    financial_statement_scope: Literal[
        "AUTO",
        "CFS",
        "OFS"
    ] = "AUTO"
    investment_horizon: Literal[
        "short_term",
        "medium_term",
        "long_term"
    ] = "medium_term"
    benchmark: str = "KOSPI"
    strategy_style: Literal[
        "long_only",
        "long_cash"
    ] = "long_cash"
    analysis_focus: list[str] = []
    include_news: bool = True
    include_investor_flow: bool = True
    include_industry_data: bool = True
```

---

# 4. 분석 기준일 원칙

## 4.1 Point-in-Time 원칙

모든 데이터는 `as_of_date` 이전에 공개된 정보만 사용한다.

예를 들어 분석 기준일이 `2025-12-31`인 경우:

- 2026년에 제출된 공시는 사용하지 않는다.
- 2026년에 정정된 재무제표를 소급하여 사용하지 않는다.
- 2025년 결산 수치라도 2026년에 공시되었다면 사용하지 않는다.
- 당시 투자자가 알 수 있었던 정보만 사용한다.

## 4.2 날짜 구분

시스템은 다음 날짜를 구분해서 저장해야 한다.

```text
회계기간 시작일
회계기간 종료일
공시 접수일
공시 이용 가능일
분석 기준일
백테스트 신호 발생일
실제 주문 체결일
```

## 4.3 데이터 이용 가능일

기본 규칙:

```text
available_from = 공시 접수일 다음 거래일
```

공시 시각 데이터를 신뢰성 있게 확보할 수 있다면 향후 고도화 단계에서 장중·장후 공시를 구분할 수 있다.

MVP에서는 보수적으로 접수일 다음 거래일부터 정보를 사용한다.

---

# 5. DART 데이터 아키텍처

OpenDART는 정기보고서에 제출된 XBRL 재무제표에 대해 주요계정, 전체 재무제표, 재무지표 및 XBRL 원본파일을 제공한다. 전체 재무제표 API는 재무상태표, 손익계산서, 포괄손익계산서, 현금흐름표, 자본변동표를 포함하고, `account_id`, `account_nm`, 당기·전기 금액 등을 반환한다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020))

본 시스템에서는 재무 데이터를 세 개 계층으로 수집한다.

```text
Layer 1. OpenDART 주요계정 API
Layer 2. OpenDART 전체 재무제표 API
Layer 3. OpenDART XBRL 원본파일
```

## 5.1 계층별 사용 목적

| 계층 | 목적 | 사용 우선순위 |
|---|---|---:|
| 주요계정 API | 빠른 조회 및 기본 계정 확인 | 보조 |
| 전체 재무제표 API | 일반 재무분석용 주 데이터 | 1순위 |
| XBRL 원본 | 원본 보존, 세부 계정, Context 검증 | 1순위 |
| 공시 원문 XML | 사업내용·주석·서술형 정보 | 별도 |

주요계정 API는 재무상태표와 손익계산서 중심의 주요 항목을 제공한다. 반면 전체 재무제표 API는 현금흐름표와 자본변동표를 포함한 모든 계정과목을 제공하므로, 본 프로젝트의 기본 재무 ETL은 전체 재무제표 API를 사용한다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016))

---

# 6. DART API 사용 명세

## 6.1 기업 고유번호 조회

### 목적

사용자 입력 기업명을 DART의 8자리 `corp_code`와 연결한다.

### 저장 필드

```python
class DartCorporation(BaseModel):
    corp_code: str
    corp_name: str
    stock_code: str | None
    modify_date: str
```

### 처리 규칙

1. DART 고유번호 파일을 내려받는다.
2. XML을 파싱한다.
3. 종목코드가 있는 상장기업을 우선한다.
4. 기업명 정규화 값을 생성한다.
5. 로컬 캐시에 저장한다.

### 캐시 갱신

```text
기본: 주 1회
강제 갱신: --refresh-corp-codes
```

### 기업명 정규화

다음 문자열을 제거하거나 통일한다.

```text
주식회사
(주)
㈜
공백
대소문자
일부 특수문자
```

단, 원본 기업명은 별도로 보존한다.

---

## 6.2 공시검색 API

### 목적

기업의 정기보고서 및 주요 공시 접수번호를 찾는다.

### 주요 입력

```text
corp_code
bgn_de
end_de
last_reprt_at
pblntf_ty
page_no
page_count
```

### 주요 출력

```text
corp_code
corp_name
stock_code
report_nm
rcept_no
flr_nm
rcept_dt
rm
```

공시검색 API의 `rcept_no`는 공시 원문 및 XBRL 원본파일 조회의 핵심 식별자로 사용한다. 공시검색은 회사·기간·공시유형을 기준으로 검색할 수 있으며 결과에 접수번호와 접수일이 포함된다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019001&utm_source=chatgpt.com))

### 정기보고서 필터

```text
사업보고서
반기보고서
분기보고서
```

### 보고서 코드

| 보고서 | `reprt_code` |
|---|---:|
| 1분기보고서 | `11013` |
| 반기보고서 | `11012` |
| 3분기보고서 | `11014` |
| 사업보고서 | `11011` |

---

## 6.3 단일회사 주요계정 API

### Endpoint

```text
GET /api/fnlttSinglAcnt.json
```

### 용도

- 기본 재무계정의 빠른 확인
- 전체 재무제표 API 결과와 비교
- ETL 품질 검증
- 연결·별도 재무제표 존재 여부 확인

### 입력

```python
class DartMajorAccountRequest(BaseModel):
    corp_code: str
    bsns_year: str
    reprt_code: str
```

### 핵심 출력

```text
rcept_no
bsns_year
stock_code
reprt_code
account_nm
fs_div
fs_nm
sj_div
sj_nm
thstrm_nm
thstrm_dt
thstrm_amount
frmtrm_nm
frmtrm_dt
frmtrm_amount
```

### 사용 제한

주요계정 API의 응답만으로 완전한 재무제표를 구성하지 않는다.

다음 항목은 전체 재무제표 또는 XBRL 원본에서 조회한다.

- 현금흐름표 세부 계정
- 자본변동표
- 기업 확장계정
- 세분화된 자산·부채 계정
- 계정의 XBRL Context
- 단위·기간·차원 정보

---

## 6.4 단일회사 전체 재무제표 API

### Endpoint

```text
GET /api/fnlttSinglAcntAll.json
```

### 목적

기업 재무분석에 필요한 주 데이터셋을 구축한다.

### 입력 스키마

```python
from typing import Literal


class DartFullFinancialRequest(BaseModel):
    corp_code: str
    bsns_year: str
    reprt_code: Literal[
        "11011",
        "11012",
        "11013",
        "11014"
    ]
    fs_div: Literal["CFS", "OFS"]
```

OpenDART 전체 재무제표 API는 사업연도, 보고서 코드와 함께 연결재무제표 `CFS` 또는 별도재무제표 `OFS` 구분을 필수 입력으로 받는다. 데이터는 2015년 이후부터 제공된다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020))

### API 응답 필드

```python
class DartFinancialAccountRaw(BaseModel):
    rcept_no: str
    reprt_code: str
    bsns_year: str
    corp_code: str

    sj_div: str
    sj_nm: str

    account_id: str
    account_nm: str
    account_detail: str | None

    thstrm_nm: str | None
    thstrm_amount: str | None
    thstrm_add_amount: str | None

    frmtrm_nm: str | None
    frmtrm_amount: str | None

    frmtrm_q_nm: str | None
    frmtrm_q_amount: str | None
    frmtrm_add_amount: str | None

    ord: str | None
    currency: str | None
```

### 재무제표 구분

| `sj_div` | 의미 |
|---|---|
| `BS` | 재무상태표 |
| `IS` | 손익계산서 |
| `CIS` | 포괄손익계산서 |
| `CF` | 현금흐름표 |
| `SCE` | 자본변동표 |

OpenDART 응답의 `account_id`는 XBRL 표준계정 ID를 표시한다. 표준계정을 사용하지 않은 경우 별도의 비표준계정 표시가 반환될 수 있으므로, `account_nm`만이 아니라 `account_id`와 기업 확장계정을 함께 처리해야 한다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020))

---

## 6.5 XBRL 재무제표 원본파일 API

### Endpoint

```text
GET /api/fnlttXbrl.xml
```

### 출력

```text
ZIP binary
```

### 입력

```python
class DartXbrlRequest(BaseModel):
    rcept_no: str
    reprt_code: str
```

OpenDART XBRL API는 공시검색에서 얻은 접수번호와 보고서 코드를 입력받아 ZIP 형식의 XBRL 원본파일을 반환한다. 공식 가이드상 파일이 없는 경우와 조회 데이터가 없는 경우 등이 별도 상태 코드로 구분된다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019))

### XBRL 원본 사용 목적

1. DART JSON API 결과 검증
2. 기업 고유 확장계정 수집
3. Context 정보 확인
4. 기간형·시점형 계정 구분
5. 연결·별도 및 차원 정보 확인
6. 단위 및 배율 확인
7. 원본 파일의 재현 가능한 보존
8. 향후 주석 XBRL 분석 확장

---

## 6.6 공시서류 원본파일 API

### Endpoint

```text
GET /api/document.xml
```

### 입력

```text
rcept_no
```

### 출력

```text
ZIP binary
```

이 API는 XBRL 재무제표 파일과 별개로 전체 공시보고서 원문을 XML 형태로 내려받는 데 사용한다. OpenDART 공식 문서는 접수번호를 입력받아 공시보고서 원본 ZIP 파일을 제공한다고 안내한다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS001&apiId=2019003&utm_source=chatgpt.com))

### 사용 목적

- 사업의 내용
- 주요 제품 및 서비스
- 위험요인
- 연구개발
- 설비투자
- 시장점유율
- 원재료
- 매출 구조
- 주요 계약
- 감사의견
- 재무제표 주석 연결

---

# 7. 연결재무제표와 별도재무제표 선택 규칙

## 7.1 기본 규칙

```text
연결재무제표가 존재하면 CFS 우선
연결재무제표가 없으면 OFS 사용
```

### 이유

기업 전체의 경제적 실체와 종속회사를 포함한 실적을 분석하기 위해 연결재무제표를 기본으로 한다.

## 7.2 별도재무제표를 함께 사용하는 경우

- 지주회사
- 배당가능이익 분석
- 별도 기준 자회사 배당수익 분석
- 자회사 투자주식 분석
- 연결과 별도의 이익 차이가 큰 기업
- 모회사 자체의 재무안정성 분석

## 7.3 저장 원칙

CFS와 OFS 중 하나를 버리지 않는다.

```text
raw 계층: CFS·OFS 모두 저장
normalized 계층: CFS·OFS 모두 정규화
analytics 계층: 기본 분석 scope를 별도로 지정
```

### 분석 메타데이터

```json
{
  "primary_financial_scope": "CFS",
  "available_scopes": ["CFS", "OFS"],
  "selection_reason": "연결재무제표 존재"
}
```

---

# 8. XBRL 파일 저장 구조

## 8.1 원본 보존 원칙

수집한 XBRL 원본 ZIP과 압축 해제 파일을 수정하지 않고 보관한다.

```text
data/
└── raw/
    └── dart/
        └── xbrl/
            └── {corp_code}/
                └── {rcept_no}/
                    ├── response.zip
                    ├── manifest.json
                    ├── extracted/
                    │   ├── instance_file.xbrl
                    │   ├── schema_file.xsd
                    │   ├── presentation.xml
                    │   ├── calculation.xml
                    │   ├── definition.xml
                    │   └── label.xml
                    └── checksum.sha256
```

실제 ZIP 내부 파일명과 구성은 제출 파일에 따라 다를 수 있으므로 파일명을 고정하여 가정하지 않는다.

## 8.2 Manifest

```json
{
  "corp_code": "00164779",
  "stock_code": "000660",
  "rcept_no": "20250318001234",
  "reprt_code": "11011",
  "report_name": "사업보고서",
  "filing_date": "2025-03-18",
  "downloaded_at": "2026-07-14T14:00:00+09:00",
  "source": "OPEN_DART_XBRL",
  "http_status": 200,
  "content_type": "application/zip",
  "sha256": "..."
}
```

## 8.3 멱등성

동일한 접수번호와 보고서 코드에 대해 이미 정상 파일이 존재하면 다시 다운로드하지 않는다.

강제 재수집 옵션:

```bash
--force-download
```

---

# 9. XBRL 파싱 명세

## 9.1 XBRL 기본 구성요소

파서는 다음 요소를 처리한다.

```text
Fact
Concept
Context
Unit
Period
Entity
Dimension
Member
Decimals
Scale
Nil
Footnote
```

## 9.2 Fact

각 재무 수치를 하나의 Fact로 변환한다.

```python
from decimal import Decimal


class XbrlFact(BaseModel):
    concept_qname: str
    concept_namespace: str
    concept_local_name: str

    context_id: str
    unit_id: str | None

    raw_value: str | None
    numeric_value: Decimal | None

    decimals: str | None
    scale: int | None
    is_nil: bool

    source_file: str
```

## 9.3 Context

```python
class XbrlContext(BaseModel):
    context_id: str
    entity_identifier: str

    period_type: str
    instant_date: str | None
    start_date: str | None
    end_date: str | None

    scenario_dimensions: list["XbrlDimension"]
    segment_dimensions: list["XbrlDimension"]
```

## 9.4 Dimension

```python
class XbrlDimension(BaseModel):
    axis_qname: str
    member_qname: str | None
    typed_member_value: str | None
```

### 예시

```text
연결 기준
별도 기준
지배기업 소유주지분
비지배지분
사업부문
지역
제품군
```

## 9.5 Unit

```python
class XbrlUnit(BaseModel):
    unit_id: str
    measure: str | None
    numerator: str | None
    denominator: str | None
```

예:

```text
KRW
shares
KRW/shares
pure
```

## 9.6 Numeric 변환

다음 순서로 수치를 변환한다.

```text
1. is_nil 여부 확인
2. 쉼표 및 공백 제거
3. 괄호 음수 처리
4. Decimal 변환
5. scale 반영
6. unit 저장
7. decimals 저장
```

### 금지 사항

- 모든 숫자를 `float`로 즉시 변환하지 않는다.
- 빈 값과 0을 동일하게 처리하지 않는다.
- 단위가 다른 값을 단순 합산하지 않는다.
- `decimals`를 실제 배율로 오해하지 않는다.
- 화면 표시용 문자열을 원본 값으로 덮어쓰지 않는다.

---

# 10. XBRL Context 선택 규칙

동일한 계정 Concept에 여러 Context가 존재할 수 있다.

예:

```text
당기 연결 기준
당기 별도 기준
전기 연결 기준
분기 3개월
분기 누적
지배기업 소유주
비지배지분
특정 사업부문
```

## 10.1 기본 Context 우선순위

### 재무상태표

```text
1. 분석 대상 결산일과 instant date 일치
2. 연결 또는 별도 scope 일치
3. 추가 Dimension이 없는 기본 Context
4. 기업 전체 기준
```

### 손익계산서·현금흐름표

```text
1. 회계기간 시작·종료일 일치
2. 누적기간 여부 일치
3. 연결 또는 별도 scope 일치
4. Dimension이 없는 기본 Context
```

## 10.2 분기 손익 구분

분기·반기보고서의 손익계산서는 다음 값을 구분한다.

```text
3개월 단일분기 금액
누적 금액
전년 동기 3개월 금액
전년 동기 누적 금액
```

DART 전체 재무제표 API에서도 분·반기 손익계산서의 `thstrm_amount`는 3개월 금액, `thstrm_add_amount`는 누적금액으로 구분된다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019020))

### 내부 필드

```python
class FinancialPeriodValue(BaseModel):
    period_type: str
    period_start: str | None
    period_end: str
    duration_type: str

    value: Decimal | None

    fiscal_year: int
    fiscal_quarter: int | None

    is_cumulative: bool
    is_single_quarter: bool
```

---

# 11. 분기 단독 실적 계산

일부 데이터는 누적값만 제공될 수 있다.

## 11.1 손익계산서 단독 분기 계산

```text
2분기 단독 = 반기 누적 - 1분기 누적
3분기 단독 = 3분기 누적 - 반기 누적
4분기 단독 = 연간 - 3분기 누적
```

## 11.2 현금흐름표 단독 분기 계산

동일한 방식으로 계산할 수 있으나 다음을 확인한다.

- 동일 계정
- 동일 scope
- 동일 단위
- 동일 회계연도
- 동일 계정 태그
- 정정공시 여부

## 11.3 파생값 표시

원본과 계산값을 구분한다.

```json
{
  "value": 1250000000000,
  "value_type": "DERIVED_QUARTER",
  "derivation": {
    "operation": "SUBTRACTION",
    "left_fact_id": "2025Q3_CUMULATIVE_OP",
    "right_fact_id": "2025H1_CUMULATIVE_OP"
  }
}
```

---

# 12. 계정 표준화 명세

## 12.1 계정 식별 우선순위

```text
1. XBRL account_id 또는 Concept QName
2. 표준 택사노미 계정 여부
3. 기업 확장계정의 label
4. account_nm
5. 재무제표 위치
6. Context와 단위
```

## 12.2 표준계정 Registry

```python
class CanonicalAccount(BaseModel):
    canonical_id: str
    korean_name: str
    english_name: str

    statement_type: str
    balance_type: str | None
    period_type: str

    accepted_concepts: list[str]
    accepted_labels: list[str]
```

### 예시

```yaml
revenue:
  korean_name: 매출액
  statement_type: IS
  period_type: duration
  accepted_concepts:
    - ifrs-full:Revenue
  accepted_labels:
    - 매출액
    - 수익(매출액)
    - 영업수익

operating_income:
  korean_name: 영업이익
  statement_type: IS
  period_type: duration
  accepted_labels:
    - 영업이익
    - 영업이익(손실)

cash_and_cash_equivalents:
  korean_name: 현금및현금성자산
  statement_type: BS
  period_type: instant
  accepted_concepts:
    - ifrs-full:CashAndCashEquivalents
```

## 12.3 기업 확장계정

기업이 표준계정 대신 확장계정을 사용하면 다음 정보를 기록한다.

```python
class ExtensionAccountMapping(BaseModel):
    corp_code: str
    concept_qname: str
    label_ko: str | None
    statement_type: str
    mapped_canonical_id: str | None
    mapping_method: str
    mapping_confidence: float
    manually_reviewed: bool
```

### 매핑 방법

```text
EXACT_CONCEPT
EXACT_LABEL
ALIAS_MATCH
STRUCTURAL_MATCH
LLM_SUGGESTED
MANUAL
UNMAPPED
```

### 규칙

LLM이 제안한 계정 매핑을 자동 확정하지 않는다.

```text
LLM 제안
↓
규칙 기반 검증
↓
금액·재무제표 위치 확인
↓
수동 검토 또는 낮은 신뢰도 표시
```

---

# 13. 재무 데이터 표준 모델

## 13.1 정규화된 Fact

```python
class NormalizedFinancialFact(BaseModel):
    fact_id: str

    corp_code: str
    stock_code: str
    rcept_no: str

    report_code: str
    report_name: str
    filing_date: str
    available_from: str

    fiscal_year: int
    fiscal_quarter: int | None

    fs_scope: str
    statement_type: str

    canonical_account_id: str | None
    source_account_id: str
    source_account_name: str
    source_concept_qname: str | None

    period_type: str
    period_start: str | None
    period_end: str

    value: Decimal | None
    currency: str | None
    unit: str | None

    is_cumulative: bool
    is_derived: bool
    is_restated: bool

    context_id: str | None
    dimensions: list[dict]

    source_layer: str
    source_file: str | None
```

## 13.2 데이터 출처 구분

```text
DART_MAJOR_ACCOUNT_API
DART_FULL_FINANCIAL_API
DART_XBRL
DERIVED
MANUAL_OVERRIDE
```

---

# 14. 재무 데이터 수집 순서

## 14.1 기본 수집 흐름

```text
기업 식별
↓
공시검색 API로 정기보고서 목록 조회
↓
각 보고서의 접수번호 확정
↓
전체 재무제표 API CFS 조회
↓
전체 재무제표 API OFS 조회
↓
XBRL ZIP 다운로드
↓
XBRL 파일 압축 해제
↓
XBRL Fact·Context·Unit 파싱
↓
DART JSON과 XBRL 결과 비교
↓
정규화
↓
품질 검증
↓
분석용 재무 시계열 생성
```

## 14.2 수집 기간

기본:

```text
최근 5개 사업연도
최근 8개 분기
```

백테스트:

```text
백테스트 시작일 이전 최소 5개 분기 추가 수집
```

성장률 및 이동평균 계산에 필요한 선행 데이터를 확보한다.

---

# 15. 정정공시 처리

OpenDART는 제출인이 기준일 이후 재무제표를 정정할 경우 수치가 변경될 수 있다고 명시한다. 따라서 최신 API 응답만 저장하면 당시 투자자가 보았던 값을 재현하지 못할 수 있다. ([오픈다트](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003))

## 15.1 원칙

각 접수번호를 독립된 버전으로 저장한다.

```text
원본 공시
기재정정 공시
첨부정정 공시
```

## 15.2 버전 모델

```python
class FilingVersion(BaseModel):
    rcept_no: str
    original_rcept_no: str | None

    filing_date: str
    report_name: str

    revision_type: str | None
    is_latest_version: bool
    supersedes_rcept_no: str | None
```

## 15.3 분석 모드

### Current View

현재 기준 최신 정정 재무제표 사용

### Point-in-Time View

분석 기준일 당시 이용 가능했던 최신 버전 사용

백테스트에는 반드시 `Point-in-Time View`를 사용한다.

---

# 16. 재무제표 품질 검증

## 16.1 구조 검증

- BS 계정에 instant Context가 사용되었는가
- IS·CIS·CF 계정에 duration Context가 사용되었는가
- CFS와 OFS가 혼합되지 않았는가
- 단위가 존재하는가
- 접수번호와 사업연도가 일치하는가

## 16.2 회계식 검증

### 재무상태표

```text
자산총계 ≈ 부채총계 + 자본총계
```

### 현금흐름표

```text
기초 현금
+ 현금의 증가·감소
+ 환율변동효과
≈ 기말 현금
```

### 자본

```text
지배기업 소유주지분
+ 비지배지분
≈ 자본총계
```

## 16.3 허용 오차

```python
absolute_tolerance = 1_000_000
relative_tolerance = 0.001
```

기업의 표시단위와 반올림을 고려해 절대 오차와 상대 오차를 함께 사용한다.

## 16.4 API-XBRL 교차검증

대표 계정에 대해 다음을 비교한다.

```text
전체 재무제표 API 값
XBRL 원본 파싱 값
주요계정 API 값
```

검증 대상:

- 자산총계
- 부채총계
- 자본총계
- 매출액
- 영업이익
- 당기순이익
- 영업활동현금흐름

### 결과 스키마

```python
class ReconciliationResult(BaseModel):
    canonical_account_id: str
    period_end: str
    fs_scope: str

    api_value: Decimal | None
    xbrl_value: Decimal | None
    major_account_value: Decimal | None

    absolute_difference: Decimal | None
    relative_difference: float | None

    status: str
    reason: str | None
```

### 상태

```text
MATCH
ROUNDING_DIFFERENCE
CONTEXT_MISMATCH
SCOPE_MISMATCH
ACCOUNT_MAPPING_MISMATCH
MISSING_IN_API
MISSING_IN_XBRL
REQUIRES_REVIEW
```

---

# 17. 재무비율 계산 명세

## 17.1 성장성

```text
매출액 YoY
영업이익 YoY
순이익 YoY
자산 성장률
자본 성장률
```

## 17.2 수익성

```text
매출총이익률
영업이익률
순이익률
ROA
ROE
ROIC
```

## 17.3 안정성

```text
부채비율
유동비율
당좌비율
순차입금
순차입금/자본
이자보상배율
```

## 17.4 현금흐름

```text
영업현금흐름
잉여현금흐름
영업현금흐름/영업이익
CAPEX/매출
현금전환율
```

## 17.5 운전자본

```text
재고자산 증가율
매출채권 증가율
매입채무 증가율
재고일수
매출채권 회전일수
현금전환주기
```

## 17.6 계산 결과 메타데이터

```python
class CalculatedMetric(BaseModel):
    metric_id: str
    corp_code: str
    period_end: str

    value: Decimal | None
    unit: str

    input_fact_ids: list[str]
    formula_version: str

    calculation_status: str
    warning_codes: list[str]
```

---

# 18. 재무 데이터와 LLM 연결

## 18.1 원칙

LLM에 XBRL 원본 전체를 직접 입력하지 않는다.

다음 단계로 전처리한다.

```text
XBRL 원본
↓
Fact·Context 파싱
↓
표준계정 매핑
↓
재무 시계열 구축
↓
재무비율 계산
↓
중요 변화 탐지
↓
Evidence Package 생성
↓
LLM 분석
```

## 18.2 Financial Evidence

```python
class FinancialEvidence(BaseModel):
    evidence_id: str
    category: str

    statement: str
    current_value: Decimal | None
    comparison_value: Decimal | None
    change_rate: float | None

    period: str
    comparison_period: str | None

    source_fact_ids: list[str]
    rcept_no: str
    filing_date: str

    significance_score: float
```

### 예시

```json
{
  "evidence_id": "FIN_OP_MARGIN_2025Q3",
  "category": "PROFITABILITY",
  "statement": "2025년 3분기 누적 영업이익률이 전년 동기 대비 개선되었다.",
  "current_value": 0.284,
  "comparison_value": 0.176,
  "change_rate": 0.108,
  "period": "2025Q3_YTD",
  "comparison_period": "2024Q3_YTD",
  "source_fact_ids": [
    "FACT_REVENUE_2025Q3_YTD",
    "FACT_OP_2025Q3_YTD"
  ],
  "rcept_no": "20251114001234",
  "filing_date": "2025-11-14",
  "significance_score": 0.91
}
```

---

# 19. Project 1 세부 파이프라인

## 19.1 단계 P1-01: 기업 식별

### 입력

```text
기업명 또는 종목코드
```

### 출력

```text
corp_code
stock_code
corp_name
market
industry
```

### 완료 조건

- 기업명과 DART 법인코드 매핑 성공
- 종목코드 검증 성공
- 다중 후보 처리 가능
- 상장폐지·비상장 여부 표시

---

## 19.2 단계 P1-02: 정기보고서 목록 수집

### 입력

```text
corp_code
lookback_years
as_of_date
```

### 출력

```text
filings.json
```

### 완료 조건

- 사업·반기·분기보고서 구분
- 접수번호 저장
- 접수일 저장
- 정정공시 연결
- 분석 기준일 이후 공시 제외

---

## 19.3 단계 P1-03: 전체 재무제표 API 수집

### 출력

```text
financial_api_raw.jsonl
```

### 완료 조건

- CFS·OFS 조회
- 2015년 이후 지원
- 보고서별 결과 저장
- API 오류코드 처리
- 재시도 및 캐시 지원

---

## 19.4 단계 P1-04: XBRL 원본 수집

### 출력

```text
response.zip
manifest.json
checksum.sha256
```

### 완료 조건

- 접수번호별 ZIP 다운로드
- Content-Type 검증
- ZIP 무결성 검증
- 파일 해시 저장
- 중복 다운로드 방지
- 오류 응답을 ZIP으로 오인하지 않음

OpenDART는 XBRL 원본 API의 정상·미조회·파일 없음·요청 제한 등을 상태 코드로 구분하므로 binary 응답이라도 오류 XML 여부를 먼저 검사해야 한다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019))

---

## 19.5 단계 P1-05: XBRL 파싱

### 출력

```text
xbrl_facts.parquet
xbrl_contexts.parquet
xbrl_units.parquet
xbrl_dimensions.parquet
```

### 완료 조건

- Namespace 동적 처리
- 모든 Fact 추출
- Context 연결
- Unit 연결
- Dimension 추출
- Nil 처리
- Numeric 변환
- 원본 QName 보존

---

## 19.6 단계 P1-06: 계정 표준화

### 출력

```text
normalized_financial_facts.parquet
unmapped_accounts.csv
account_mapping_report.json
```

### 완료 조건

- 표준계정 매핑
- 기업 확장계정 보존
- 비표준계정 목록 출력
- 매핑 신뢰도 기록
- 매핑 근거 기록
- 수동 수정 설정 지원

---

## 19.7 단계 P1-07: API-XBRL 정합성 검증

### 출력

```text
reconciliation_report.json
reconciliation_failures.csv
```

### 완료 조건

- 대표 계정 7개 이상 비교
- 기간·scope·Context 일치 확인
- 차이 원인 분류
- 일정 오차 이상이면 실패 처리

---

## 19.8 단계 P1-08: 재무 시계열 생성

### 출력

```text
annual_financials.parquet
quarterly_financials.parquet
financial_metrics.parquet
```

### 완료 조건

- 연간 5개년
- 최근 8개 분기
- 누적·단독분기 구분
- 공시일 및 available_from 저장
- 정정공시 버전 보존

---

## 19.9 단계 P1-09: 공시 원문 분석

### 출력

```text
disclosure_sections.jsonl
material_events.json
```

### 완료 조건

- 원문 XML 다운로드
- 보고서 섹션 분리
- 사업내용·위험요인·설비투자 추출
- 근거 문장 보존
- 원문 위치 정보 저장

---

## 19.10 단계 P1-10: 기업분석 및 투자 가설 생성

### 출력

```text
company_analysis.json
investment_hypothesis.json
research_report.md
```

### 완료 조건

- 모든 핵심 주장에 `evidence_id` 연결
- 사실·해석·가설 구분
- 데이터가 없는 내용 추정 금지
- 가설을 측정 가능한 변수로 표현
- 최소 하나의 반증 조건 포함

---

# 20. Project 2 세부 명세

## 20.1 입력

```python
class StrategyGenerationRequest(BaseModel):
    ticker: str
    hypothesis_id: str
    idea: str
    start_date: str
    end_date: str
    benchmark: str
```

## 20.2 투자 가설 입력 예시

```json
{
  "hypothesis_id": "HYP_001",
  "ticker": "000660",
  "as_of_date": "2021-12-31",
  "title": "실적 모멘텀과 수급 및 가격 돌파의 결합",
  "rationale": "이익 개선이 수급과 가격 추세로 확인되는 구간에서 정보 반영이 이어질 가능성이 있다.",
  "observable_variables": [
    "operating_income_yoy",
    "foreign_net_buy_20d",
    "price_breakout_60d"
  ],
  "holding_period_days": 60,
  "evidence_ids": [
    "FIN_OP_YOY_2021Q3",
    "FLOW_FOREIGN_20211231"
  ]
}
```

---

# 21. 전략 DSL

## 21.1 허용 재무지표

```text
revenue_yoy
operating_income_yoy
net_income_yoy
operating_margin
roe
roa
debt_ratio
net_debt
operating_cash_flow
free_cash_flow
inventory_yoy
receivables_yoy
```

## 21.2 허용 가격지표

```text
close
open
high
low
volume
sma_5
sma_20
sma_60
sma_120
rolling_high_20
rolling_high_60
return_20d
return_60d
volatility_20
rsi_14
atr_14
```

## 21.3 허용 수급지표

```text
foreign_net_buy_5d
foreign_net_buy_20d
institution_net_buy_5d
institution_net_buy_20d
```

## 21.4 허용 연산자

```text
>
>=
<
<=
==
cross_above
cross_below
between
and
or
not
```

---

# 22. 재무 데이터의 백테스트 정렬

## 22.1 절대 원칙

재무 수치는 회계기간 종료일부터 사용할 수 없다.

```text
2025년 3분기 종료일: 2025-09-30
공시 접수일: 2025-11-14
사용 가능일: 2025-11-17 또는 다음 거래일
```

## 22.2 As-of Join

가격 데이터에 재무 데이터를 병합할 때 다음 조건을 사용한다.

```python
price_date >= financial_available_from
```

다음 공시가 공개될 때까지 직전 공시값을 유지한다.

## 22.3 금지

```text
회계기간 종료일 기준 병합
현재 다운로드한 최신 수정값의 과거 소급 적용
연간 실적을 결산일 당일부터 사용
분기 누적값을 단독분기로 잘못 사용
```

---

# 23. 백테스트 기본 전략

## 23.1 진입 조건

```text
영업이익 YoY > 20%
AND 최근 20거래일 외국인 누적 순매수 > 0
AND 종가가 직전 60거래일 고점을 상향 돌파
```

## 23.2 청산 조건

```text
종가가 20일 이동평균을 하향 돌파
OR 최대 보유기간 60거래일
OR 진입가 대비 손실률 -10%
```

## 23.3 실행

```text
t일 종가 기준 신호 계산
t+1 거래일 시가 체결
```

## 23.4 전략 JSON

```json
{
  "strategy_name": "EarningsFlowBreakout",
  "version": "1.0",
  "universe": {
    "type": "single_asset",
    "tickers": ["000660"]
  },
  "entry": {
    "all": [
      {
        "left": "operating_income_yoy",
        "operator": ">",
        "right": 0.20
      },
      {
        "left": "foreign_net_buy_20d",
        "operator": ">",
        "right": 0
      },
      {
        "left": "close",
        "operator": "cross_above",
        "right": "rolling_high_60_lag1"
      }
    ]
  },
  "exit": {
    "any": [
      {
        "left": "close",
        "operator": "cross_below",
        "right": "sma_20"
      },
      {
        "type": "max_holding_days",
        "value": 60
      },
      {
        "type": "stop_loss",
        "value": -0.10
      }
    ]
  },
  "execution": {
    "signal_time": "close",
    "trade_time": "next_open"
  }
}
```

`rolling_high_60`은 당일 고가를 포함하면 돌파 조건이 왜곡될 수 있으므로 기본적으로 `lag(1)` 된 직전 60일 고점을 사용한다.

---

# 24. 백테스트 결과

## 24.1 성과지표

```text
누적수익률
CAGR
연환산 변동성
Sharpe Ratio
Sortino Ratio
Maximum Drawdown
Calmar Ratio
승률
평균 손익
Profit Factor
거래 횟수
평균 보유기간
시장 노출률
벤치마크 초과수익률
Information Ratio
```

## 24.2 강건성 검증

```text
인샘플·아웃오브샘플
파라미터 민감도
거래비용 민감도
하위 기간 분석
시장 국면 분석
조건 제거 분석
Buy & Hold 비교
```

## 24.3 조건 제거 분석

다음 전략을 비교한다.

```text
가격 모멘텀만
실적 모멘텀만
실적 + 가격
실적 + 수급
실적 + 수급 + 가격
```

이를 통해 각 조건의 기여도를 확인한다.

---

# 25. 레포 구조

```text
research-to-backtest/
│
├── README.md
├── pyproject.toml
├── .env.example
├── Makefile
│
├── docs/
│   ├── PROJECT_SPEC.md
│   ├── DATA_DICTIONARY.md
│   ├── XBRL_PARSING_SPEC.md
│   ├── STRATEGY_DSL.md
│   └── MILESTONES.md
│
├── configs/
│   ├── dart.yaml
│   ├── account_registry.yaml
│   ├── company_account_overrides.yaml
│   ├── backtest.yaml
│   └── logging.yaml
│
├── src/
│   ├── common/
│   │   ├── models.py
│   │   ├── constants.py
│   │   ├── exceptions.py
│   │   ├── dates.py
│   │   └── logging.py
│   │
│   ├── data_sources/
│   │   ├── dart/
│   │   │   ├── client.py
│   │   │   ├── corp_code.py
│   │   │   ├── disclosure_search.py
│   │   │   ├── financial_api.py
│   │   │   ├── xbrl_downloader.py
│   │   │   ├── document_downloader.py
│   │   │   └── error_codes.py
│   │   ├── market/
│   │   ├── investor_flow/
│   │   └── news/
│   │
│   ├── xbrl/
│   │   ├── archive.py
│   │   ├── discovery.py
│   │   ├── namespaces.py
│   │   ├── parser.py
│   │   ├── facts.py
│   │   ├── contexts.py
│   │   ├── units.py
│   │   ├── dimensions.py
│   │   └── labels.py
│   │
│   ├── financials/
│   │   ├── normalizer.py
│   │   ├── account_registry.py
│   │   ├── account_mapper.py
│   │   ├── period_resolver.py
│   │   ├── quarter_derivation.py
│   │   ├── reconciliation.py
│   │   ├── validation.py
│   │   └── metrics.py
│   │
│   ├── disclosures/
│   │   ├── parser.py
│   │   ├── section_splitter.py
│   │   ├── event_classifier.py
│   │   └── evidence_builder.py
│   │
│   ├── research/
│   │   ├── pipeline.py
│   │   ├── evidence_store.py
│   │   ├── financial_analyzer.py
│   │   ├── market_analyzer.py
│   │   ├── hypothesis_generator.py
│   │   └── report_generator.py
│   │
│   ├── strategy/
│   │   ├── schema.py
│   │   ├── indicator_registry.py
│   │   ├── translator.py
│   │   ├── validator.py
│   │   └── compiler.py
│   │
│   ├── backtest/
│   │   ├── engine.py
│   │   ├── data_alignment.py
│   │   ├── broker.py
│   │   ├── portfolio.py
│   │   ├── costs.py
│   │   ├── metrics.py
│   │   ├── robustness.py
│   │   └── charts.py
│   │
│   └── app/
│       ├── cli.py
│       └── streamlit_app.py
│
├── tests/
│   ├── fixtures/
│   │   ├── dart_api/
│   │   └── xbrl/
│   ├── unit/
│   │   ├── test_xbrl_parser.py
│   │   ├── test_context_resolver.py
│   │   ├── test_account_mapper.py
│   │   ├── test_quarter_derivation.py
│   │   ├── test_reconciliation.py
│   │   └── test_metrics.py
│   ├── integration/
│   │   ├── test_dart_pipeline.py
│   │   └── test_research_pipeline.py
│   └── backtest/
│       ├── test_no_lookahead.py
│       ├── test_next_open_execution.py
│       └── test_financial_asof_join.py
│
├── data/
│   ├── raw/
│   ├── normalized/
│   ├── analytics/
│   └── cache/
│
└── outputs/
    └── {run_id}/
        ├── run_manifest.json
        ├── research_report.md
        ├── company_analysis.json
        ├── investment_hypothesis.json
        ├── strategy_spec.json
        ├── backtest_result.json
        ├── trade_log.csv
        └── charts/
```

---

# 26. 실행 명령어

## 26.1 DART 기업 식별

```bash
python -m src.app.cli resolve-company \
  --company "SK하이닉스"
```

## 26.2 DART 재무자료 수집

```bash
python -m src.app.cli collect-financials \
  --company "SK하이닉스" \
  --from-year 2020 \
  --to-year 2025 \
  --scopes CFS OFS \
  --include-xbrl
```

## 26.3 XBRL 파싱

```bash
python -m src.app.cli parse-xbrl \
  --corp-code 00164779 \
  --rcept-no 20250318001234
```

## 26.4 재무 데이터 검증

```bash
python -m src.app.cli reconcile-financials \
  --company "SK하이닉스" \
  --year 2024 \
  --report annual
```

## 26.5 기업 분석

```bash
python -m src.app.cli research \
  --company "SK하이닉스" \
  --as-of-date 2025-12-31 \
  --lookback-years 5
```

## 26.6 백테스트

```bash
python -m src.app.cli backtest \
  --hypothesis outputs/latest/investment_hypothesis.json \
  --start-date 2016-01-01 \
  --end-date 2025-12-31 \
  --benchmark KOSPI
```

---

# 27. API 오류 처리

## 27.1 DART 상태 코드

처리 대상:

```text
000 정상
010 등록되지 않은 키
011 사용할 수 없는 키
012 접근할 수 없는 IP
013 조회 데이터 없음
014 파일 없음
020 요청 제한 초과
021 조회 회사 수 초과
100 부적절한 필드
101 부적절한 접근
800 시스템 점검
900 정의되지 않은 오류
901 개인정보 보유기간 만료 키
```

OpenDART 공식 XBRL API 문서는 요청 제한 초과, 데이터 없음, 파일 없음, 시스템 점검 등의 상태 코드를 구분하고 있다. ([오픈다트](https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019019))

## 27.2 재시도 정책

재시도 가능:

```text
020
800
900
HTTP 429
HTTP 500~599
네트워크 타임아웃
```

즉시 실패:

```text
010
011
012
100
101
901
```

## 27.3 Backoff

```text
1초
2초
4초
8초
최대 4회
```

---

# 28. 테스트 명세

## 28.1 XBRL Parser 테스트

- Namespace가 다른 파일
- 표준계정과 확장계정 혼재
- instant Context
- duration Context
- Dimension 포함 Context
- nil Fact
- 음수값
- scale 포함 값
- KRW 및 shares 단위
- 동일 Concept의 복수 Context

## 28.2 재무 표준화 테스트

- 매출액 계정 별칭
- 영업이익 손실 계정
- 비표준 계정
- 연결·별도 혼합 방지
- 누적·단독분기 구분
- 4분기 역산

## 28.3 룩어헤드 방지 테스트

```text
공시일 이전 가격 행에 재무값이 존재하면 실패
정정공시일 이전에 정정값이 적용되면 실패
당일 종가 신호로 당일 종가 매수하면 실패
```

## 28.4 정합성 테스트

- 자산 = 부채 + 자본
- API와 XBRL 대표 계정 비교
- 동일 접수번호 재수집 시 결과 동일
- 파일 해시 일치

---

# 29. 로깅 및 실행 추적

모든 실행은 `run_id`를 가진다.

```json
{
  "run_id": "20260714_140000_SKHYNIX",
  "company": "SK하이닉스",
  "corp_code": "00164779",
  "stock_code": "000660",
  "as_of_date": "2025-12-31",
  "code_version": "git-commit-hash",
  "config_hash": "...",
  "started_at": "...",
  "completed_at": "...",
  "status": "SUCCESS"
}
```

기록 대상:

- API 요청
- 수집 파일
- 데이터 버전
- XBRL 파싱 통계
- 미매핑 계정 수
- 검증 실패 수
- LLM 모델 및 프롬프트 버전
- 백테스트 설정
- Git commit

---

# 30. 보안

## 30.1 환경변수

```text
DART_API_KEY
LLM_API_KEY
KIS_APP_KEY
KIS_APP_SECRET
```

## 30.2 금지

- API 키 커밋
- `.env` 커밋
- 로그에 인증키 출력
- 오류 URL에 전체 인증키 기록

## 30.3 `.env.example`

```text
DART_API_KEY=
LLM_API_KEY=
KIS_APP_KEY=
KIS_APP_SECRET=
```

---

# 31. 개발 Milestone

## Milestone 0. 프로젝트 기반 구축

### 목표

레포 구조와 공통 실행환경을 구축한다.

### 작업

- `pyproject.toml`
- 환경변수 관리
- 로깅
- Pydantic 모델
- CLI 골격
- 테스트 환경
- CI 설정

### 완료 조건

- `pytest` 실행 성공
- CLI help 출력
- `.env.example` 존재
- lint 및 type check 실행 가능

---

## Milestone 1. DART 기업·공시 식별

### 목표

기업명으로 DART 법인과 정기보고서를 찾는다.

### 작업

- 고유번호 파일 수집
- 기업명 정규화
- 기업 검색
- 공시검색 API
- 정기보고서 분류
- 정정공시 식별

### 완료 조건

```bash
resolve-company "SK하이닉스"
```

실행 시 다음이 출력된다.

```text
corp_code
stock_code
corp_name
최근 사업보고서 접수번호
최근 분기보고서 접수번호
```

---

## Milestone 2. DART 전체 재무제표 API

### 목표

전체 재무제표 API로 CFS·OFS 원본 데이터를 수집한다.

### 작업

- API client
- 보고서 코드 처리
- CFS·OFS 수집
- raw JSON 저장
- 오류코드 처리
- 캐시

### 완료 조건

- SK하이닉스 최근 5개년 수집
- BS·IS·CIS·CF·SCE 분리
- raw 응답 재현 가능
- 동일 요청 중복 호출 방지

---

## Milestone 3. XBRL 원본 수집

### 목표

접수번호별 XBRL ZIP 파일을 저장한다.

### 작업

- binary 응답 처리
- 오류 XML 탐지
- ZIP 무결성 검사
- 압축 해제
- manifest
- checksum

### 완료 조건

- 사업보고서 XBRL 다운로드 성공
- 파일 해시 생성
- 재실행 시 캐시 사용
- 오류 응답이 파일로 저장되지 않음

---

## Milestone 4. XBRL Parser

### 목표

XBRL 원본에서 Fact·Context·Unit·Dimension을 추출한다.

### 작업

- Instance document 탐색
- Namespace 처리
- Fact 파싱
- Context 파싱
- Unit 파싱
- Dimension 파싱
- Numeric 변환
- Parquet 저장

### 완료 조건

- 대표 사업보고서의 전체 numeric Fact 추출
- Concept와 Context 연결
- 동일 Concept의 복수 Context 보존
- 파싱 결과를 재실행해도 동일

---

## Milestone 5. 계정 표준화 및 정합성 검증

### 목표

재무 데이터를 분석 가능한 공통계정으로 변환한다.

### 작업

- Account Registry
- 표준 Concept 매핑
- Label alias 매핑
- 확장계정 처리
- API-XBRL 비교
- 회계식 검증

### 완료 조건

다음 계정이 자동 매핑된다.

```text
매출액
영업이익
당기순이익
자산총계
부채총계
자본총계
현금및현금성자산
영업활동현금흐름
유형자산 취득
재고자산
매출채권
```

---

## Milestone 6. 재무 시계열 및 지표

### 목표

연간·분기 재무 시계열과 비율을 생성한다.

### 작업

- 기간 해석
- 단독분기 계산
- 전년 동기 비교
- TTM
- 재무비율
- available_from
- Point-in-Time Dataset

### 완료 조건

- 5개년 연간 재무표
- 8개 분기 단독 실적
- 매출·영업이익 성장률
- 영업이익률
- ROE
- 부채비율
- FCF
- 공시일 기준 데이터 정렬

---

## Milestone 7. 기업 리서치 엔진

### 목표

재무·공시·시장 근거를 이용해 기업 분석을 생성한다.

### 작업

- Financial Evidence
- 공시 섹션 분석
- 중요 변화 탐지
- Evidence Store
- LLM 구조화 출력
- 보고서 생성

### 완료 조건

- 모든 핵심 주장에 evidence ID 존재
- 근거 없는 주장 차단
- 투자 포인트와 위험요인 분리
- A4 1~2매 보고서 생성

---

## Milestone 8. 투자 가설 및 전략 DSL

### 목표

기업 분석 결과를 검증 가능한 전략으로 변환한다.

### 작업

- Hypothesis schema
- Indicator Registry
- Strategy schema
- 자연어 변환 프롬프트
- Validator
- 지원하지 않는 변수 처리

### 완료 조건

- 투자 가설 1개 이상 생성
- Strategy JSON 생성
- JSON Schema 검증
- 임의 Python 코드 실행 없음

---

## Milestone 9. 백테스트 엔진

### 목표

전략을 과거 데이터로 검증한다.

### 작업

- 가격 데이터 정렬
- 재무 As-of Join
- 신호 계산
- 다음 날 시가 체결
- 거래비용
- Portfolio
- 성과지표
- 거래 로그

### 완료 조건

- 룩어헤드 테스트 통과
- Buy & Hold 비교
- CAGR·Sharpe·MDD 계산
- 거래내역 출력

---

## Milestone 10. 강건성 분석 및 제출물

### 목표

최종 포트폴리오를 완성한다.

### 작업

- 인샘플·아웃오브샘플
- 파라미터 민감도
- 조건 제거 분석
- 차트
- Streamlit
- README
- 과제용 보고서

### 완료 조건

다음 파일이 생성된다.

```text
과제1_기업산업분석.pdf
과제2_AI활용검증자료.pdf
research_report.md
strategy_spec.json
backtest_report.html
README.md
```

---

# 32. MVP 범위

## 반드시 구현

- 기업명 → DART corp_code
- 정기보고서 검색
- 전체 재무제표 API
- XBRL 원본 다운로드
- XBRL Fact·Context·Unit 파싱
- 주요 재무계정 정규화
- API-XBRL 대표 계정 검증
- 최근 5개년 및 8개 분기 재무 데이터
- 공시일 기준 Point-in-Time 정렬
- 기업 분석
- 투자 가설 1개
- 단일 종목 롱·현금 백테스트
- 거래비용
- 성과지표와 차트

## 후순위

- 모든 산업 지원
- 복수 종목 포트폴리오
- 실시간 주문
- 자동매매
- 전체 주석 XBRL 분석
- 모든 기업 확장계정 자동 매핑
- 장중 공시 시각 정밀 처리
- 복잡한 파생상품 전략

---

# 33. Definition of Done

프로젝트는 다음 조건을 모두 만족할 때 포트폴리오 제출 가능한 상태로 판단한다.

1. 사용자가 기업명을 입력할 수 있다.
2. 정확한 DART 법인코드와 종목코드를 찾는다.
3. 분석 기준일 이전의 공시만 선택한다.
4. 전체 재무제표 API 데이터를 수집한다.
5. XBRL 원본 ZIP을 보존한다.
6. XBRL Fact와 Context를 파싱한다.
7. 연결·별도 및 기간을 구분한다.
8. 주요 재무계정을 공통계정으로 매핑한다.
9. API와 XBRL의 대표 계정 수치를 비교한다.
10. 연간·분기 재무 시계열을 생성한다.
11. 재무정보를 공시일 이후부터만 사용한다.
12. AI 분석의 모든 핵심 주장에 근거가 연결된다.
13. 투자 가설이 측정 가능한 변수로 표현된다.
14. 전략이 허용된 DSL로만 생성된다.
15. 백테스트에 미래 정보가 사용되지 않는다.
16. 거래비용과 다음 거래일 체결을 반영한다.
17. 벤치마크와 성과를 비교한다.
18. 코드, 데이터, 프롬프트 및 설정 버전을 재현할 수 있다.
19. 과제 1과 과제 2가 하나의 흐름으로 설명된다.
20. README만 읽어도 실행 방법과 설계 의도를 이해할 수 있다.

---

# 34. 포트폴리오에서 강조할 핵심

본 프로젝트에서 XBRL을 사용하는 이유는 단순히 API를 하나 더 사용했다는 사실이 아니다.

강조해야 할 내용은 다음과 같다.

> OpenDART의 주요계정 API만 사용하는 대신, 전체 재무제표 API와 XBRL 원본을 함께 수집해 세부 계정과 Context를 보존했습니다. 연결·별도 재무제표, 단독분기·누적 실적, 공시 시점 및 정정공시를 구분해 백테스트에서 미래 정보가 유입되지 않도록 설계했습니다. 또한 API 결과와 XBRL 원본 수치를 교차검증하고, AI에는 원본 숫자를 직접 계산하게 하지 않고 검증된 재무 Evidence만 제공했습니다.

OpenDART는 XBRL 재무제표를 상장법인과 주요 IFRS 적용 법인이 제출한 재무정보로 제공하지만, 제출인의 책임하에 작성된 자료이며 금융감독원이 정확성과 완전성을 보장하지 않는다고 안내한다. 따라서 본 시스템은 원문 보존, API-XBRL 교차검증, 정정공시 버전 관리 및 근거 추적을 핵심 품질 관리 절차로 둔다. ([오픈다트](https://opendart.fss.or.kr/guide/main.do?apiGrpCd=DS003))