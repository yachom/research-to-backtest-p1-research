# A3 구현 명세 — 시장 데이터 수집 (신설 마일스톤, docs/MILESTONES.md Phase A)

- 목적: Phase A 관통에 필요한 시장 데이터 계층 — ① 수정주가 OHLCV ② 투자자별 순매수(외국인·기관) ③ 벤치마크 KOSPI 지수 ④ KRX 거래일 캘린더(README §4.3 `available_from`의 전제).
- 근거: README §3.2(수급 옵션), §21.2~21.3(가격·수급 지표), §22(백테스트 정렬), MILESTONES D1(개정).
- 비범위: 지표 계산(sma·rolling 등 — A5/A6), 뉴스, 산업 데이터.
- 의존성: **pykrx 1.2.8, pandas, pyarrow — 이미 pyproject에 추가·설치됨.** 추가 의존성 금지. mypy는 `pykrx.*` override가 이미 설정됨 — pykrx 반환값은 어댑터에서 타입 경계를 명시한다.

## 0. 전제 사실 (2026-07-14 실측 — 구현이 반드시 반영할 것)

KRX가 2025년부터 데이터 조회에 로그인을 의무화했다. pykrx 1.2.8 기준:

| 호출 | 로그인 | 비고 |
|---|---|---|
| `stock.get_market_ohlcv(..., adjusted=True)` | **불필요** | 정상 동작 확인. 컬럼: 시가/고가/저가/종가/거래량/등락률 |
| `stock.get_market_ohlcv(..., adjusted=False)` | 필요 | 미로그인 시 **예외가 아니라 빈 DataFrame** + stdout에 오류 출력 |
| `stock.get_market_trading_value_by_date(...)` | 필요 | 〃 (빈 DF) |
| `stock.get_index_ohlcv(...)` | 필요 | 미로그인 시 KeyError 등 예외 발생 가능 |

- pykrx는 `os.getenv("KRX_ID")`/`os.getenv("KRX_PW")`로 **환경변수에서 직접** 자격증명을 읽어 자동 로그인한다. 우리 Settings(.env)는 os.environ에 자동 반영되지 않으므로, **어댑터가 Settings의 krx_id/krx_pw를 os.environ에 주입**해야 한다(이미 환경변수가 있으면 덮어쓰지 않음).
- 미로그인 실패가 "빈 DataFrame"으로 나타나므로, **빈 결과를 절대 정상으로 취급하지 않는다** — 자격증명 부재면 호출 전에 차단, 자격증명이 있는데 빈 결과면 오류.

## 1. 모듈 배치

```text
src/research_backtest/core/market/__init__.py
src/research_backtest/core/market/source.py      # MarketDataSource Protocol + PykrxSource
src/research_backtest/core/market/collector.py   # 수집·캐시·raw 저장 + normalized 병합
src/research_backtest/core/market/calendar.py    # KrxTradingCalendar
```

CLI: `app/cli.py`에 `collect-market` 명령 신설. `core/config.py`에 Settings 필드(krx_id, krx_pw)와 `MarketConfig`/`load_market_config` 추가, `configs/market.yaml` 신설.

## 2. source.py — 소스 어댑터

```python
class MarketDataSource(Protocol):
    def fetch_ohlcv(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame: ...
    def fetch_investor_value(self, stock_code: str, from_date: date, to_date: date) -> pd.DataFrame: ...
    def fetch_index_ohlcv(self, index_code: str, from_date: date, to_date: date) -> pd.DataFrame: ...

class PykrxSource:
    def __init__(self, *, krx_id: str = "", krx_pw: str = "") -> None: ...
    @property
    def has_krx_credentials(self) -> bool: ...
```

- `__init__`: krx_id/krx_pw가 주어졌고 `os.environ`에 `KRX_ID`/`KRX_PW`가 없으면 주입한다(있으면 존중). 값 로깅 금지.
- `fetch_ohlcv`: `get_market_ohlcv(adjusted=True)` — **수정주가 사용을 명시적 설계 결정으로 docstring에 기록** (소급 수정 방식, 신호·체결 모두 수정주가 기준 — MVP 단순화. 거래량은 미수정일 수 있음도 기록).
- `fetch_investor_value` / `fetch_index_ohlcv`: 호출 전 `has_krx_credentials` 확인 — 없으면 `MarketAuthError`(신설, 아래) raise. **빈 DataFrame 반환 시 `DataValidationError`** ("KRX 응답이 비어 있음 — 자격증명·기간 확인" 힌트 포함).
- 반환 DataFrame 계약(어댑터가 보장, 컬럼 드리프트는 즉시 실패):
  - ohlcv/index: index=`date`(datetime.date로 정규화), columns=`open,high,low,close,volume` (등락률·거래대금 등 잉여 컬럼은 버림. index에 거래대금 있으면 `trading_value`로 유지 가능 — 선택)
  - investor: index=`date`, columns=`foreign_net_buy_value,institution_net_buy_value` (+`individual_net_buy_value` 있으면 유지). 원본 컬럼 `외국인합계`/`기관합계` 부재 시 `DataValidationError` — **실제 컬럼명을 구현 중 실측해 docstring과 DATA_NOTES 보고에 기록할 것**
- pykrx가 stdout에 찍는 로그인 경고·오류 문자열은 억제하지 않아도 된다(허용된 소음). 단 우리 로그에는 자격증명 값 미출력.
- pykrx 호출은 날짜를 `YYYYMMDD` 문자열로 받는다 — 어댑터 경계에서만 변환.

`core/exceptions.py`에 추가:

```python
class MarketAuthError(ResearchBacktestError):
    """KRX 로그인 자격증명(KRX_ID/KRX_PW)이 없어 수집할 수 없는 데이터셋 (MILESTONES D1 개정)."""
```

## 3. collector.py — 수집·캐시·저장

### 3.1 저장 구조

```text
data/raw/market/pykrx/{stock_code}/ohlcv.parquet + ohlcv.meta.json
data/raw/market/pykrx/{stock_code}/investor_value.parquet + investor_value.meta.json
data/raw/market/pykrx/index_{index_code}/ohlcv.parquet + ohlcv.meta.json
data/normalized/market/{stock_code}/daily.parquet          # 병합본 (A6 입력)
data/normalized/market/index_{index_code}/daily.parquet
data/normalized/market/calendar/krx_trading_days.parquet   # date 단일 컬럼
```

- raw parquet은 어댑터 반환 스키마 그대로(date index 포함) 저장.
- meta.json: `{"params": {...}, "from_date", "to_date", "row_count", "date_min", "date_max", "fetched_at"(KST ISO8601), "source": "PYKRX", "pykrx_version"}`. 쓰기 순서: parquet → meta (meta가 커밋 마커 — A2와 동일 규칙).

### 3.2 캐시 규칙

- 히트 = meta 파싱 가능 ∧ **요청 범위 ⊆ [meta.from_date, meta.to_date]** ∧ parquet 존재. 히트면 재수집 없음.
- 미스(범위 확장 포함) = `요청 ∪ 저장` 전체 범위를 재수집해 통째로 덮어쓴다(단순·정확 우선 — 일 단위 append는 후순위).
- `--force-download`로 무시.
- 실제 소스 호출 사이에만 `min_interval_seconds` 대기(configs/market.yaml, 기본 0.3초).

### 3.3 수집 오케스트레이션

```python
class MarketCollectionSummary(BaseModel):
    stock_code: str
    index_code: str
    outcomes: list[DatasetOutcome]   # dataset: OHLCV/INVESTOR_VALUE/INDEX/CALENDAR/DAILY_MERGED
                                     # result: FETCHED/CACHED/SKIPPED_NO_AUTH/BUILT
                                     # row_count, date_min, date_max

def collect_market_data(source, *, stock_code, index_code, from_date, to_date,
                        data_dir, force=False, min_interval_seconds=0.3, sleep=time.sleep,
                        ) -> MarketCollectionSummary:
```

- 순서: ① OHLCV(무로그인) → ② investor·③ index(자격증명 있을 때만; 없으면 해당 outcome을 `SKIPPED_NO_AUTH`로 기록하고 계속 — **부분 수집 모드**) → ④ 캘린더 빌드(**index 데이터가 있을 때만** — 지수 거래일이 시장 캘린더다; 종목 날짜로 대체하지 않는다) → ⑤ normalized daily 병합.
- normalized daily (A6 입력): ohlcv 기준으로 investor를 date로 left-join. 컬럼: `open,high,low,close,volume,foreign_net_buy_value,institution_net_buy_value`. investor가 없으면(부분 모드) 가격 컬럼만으로 생성하고 meta에 `has_investor_flows: false` 기록.
- 검증(위반 시 `DataValidationError`): date 중복·비단조 / `high < low` 행 / ohlcv 빈 결과. 로그 기록(실패 아님): investor와 ohlcv의 날짜 차집합 개수, volume=0 행 수.

## 4. calendar.py — KRX 거래일 캘린더

```python
class CalendarRangeError(ResearchBacktestError): ...   # core/exceptions.py에 추가

class KrxTradingCalendar:
    """KRX 거래일 캘린더 — KOSPI 지수 거래일에서 구축 (README §4.3의 실캘린더).

    core.dates.TradingCalendar 프로토콜을 만족한다. WeekdayCalendar는 이제
    테스트 전용이며 프로덕션 available_from 계산은 이 클래스를 쓴다.
    """
    def __init__(self, trading_days: Sequence[date]) -> None: ...   # 정렬·중복 제거, 빈 목록 거부
    @property
    def coverage(self) -> tuple[date, date]: ...
    def is_trading_day(self, d: date) -> bool: ...
    def next_trading_day(self, d: date) -> date: ...   # d 이후 첫 거래일(strictly after)
    @classmethod
    def from_parquet(cls, path: Path) -> "KrxTradingCalendar": ...
```

- **coverage 밖 조회는 `CalendarRangeError`** — 주말 로직으로 조용히 대체하지 않는다(룩어헤드·오정렬의 싹). `is_trading_day`도 coverage 밖이면 raise.
- `next_trading_day`는 bisect 사용(캘린더는 ~3,000일 규모지만 백테스트 루프에서 반복 호출됨).
- 빌드 함수: `build_calendar_from_index(index_daily: pd.DataFrame) -> list[date]` — index 날짜 추출·정렬. collector가 parquet으로 저장.

## 5. 설정

- `Settings`(core/config.py): `krx_id: str = ""`, `krx_pw: str = ""` 추가 (.env.example은 이미 갱신됨).
- `configs/market.yaml` 신설 + `MarketConfig`/`load_market_config`(DartConfig 패턴 그대로):

```yaml
# 시장 데이터 수집 설정 (docs/MILESTONES.md D1, 명세 A3)
source: pykrx
request:
  min_interval_seconds: 0.3
defaults:
  start_date: "2015-01-01"   # 전체 재무제표 API 제공 범위(2015~)와 정렬
  index_code: "1001"          # KOSPI
```

## 6. CLI — `collect-market`

```bash
r2b collect-market (--company "SK하이닉스" | --stock-code 000660) \
  [--from-date 2015-01-01] [--to-date <어제>] [--index 1001] [--force-download]
```

- `--company`/`--stock-code`는 **정확히 하나만** — 둘 다/둘 다 없음은 `typer.BadParameter`. `--company`는 `_resolve_or_exit` 재사용(DART 키 필요), `--stock-code`는 DART 없이 동작.
- 기본값: from=configs/market.yaml의 start_date, to=**KST 오늘−1일**(장중 미완성 봉 방지 — docstring에 이유 기록), index=설정값.
- 출력: rich 테이블(데이터셋별 결과·행수·기간) + 저장 경로. `SKIPPED_NO_AUTH`가 있으면 마지막에 노란 경고: "투자자 수급·지수는 KRX 로그인 필요 — .env에 KRX_ID/KRX_PW 설정 후 재실행하면 가격 캐시는 유지된 채 나머지만 수집된다."
- 종료 코드: 0 성공(부분 수집 포함) / 1 소스·검증 오류 / 3 설정 오류.

## 7. 테스트 명세

### unit (오프라인 — FakeSource로 pykrx 미호출)

FakeSource: 손으로 만든 작은 DataFrame(거래일 8일, 주말 낀 범위) 반환, 호출 횟수 기록.

| 대상 | 케이스 |
|---|---|
| collector 캐시 | 첫 수집 FETCHED → 동일 범위 재수집 CACHED(소스 호출 0회) / 범위 확장 → 재수집(요청∪저장) / force 재수집 / parquet만 있고 meta 없음 → 미스 |
| 부분 수집 | 자격증명 없는 FakeSource(MarketAuthError) → OHLCV FETCHED + investor/index SKIPPED_NO_AUTH + 캘린더 미생성 + daily는 가격 컬럼만 |
| 병합 | daily 컬럼 스키마 고정 / left-join 정확성(수급 없는 날 NaN) / date 중복 → DataValidationError / high<low → DataValidationError |
| calendar | is/next 기본 동작(주말·공휴일 갭) / **coverage 밖 → CalendarRangeError** / next가 strictly after / from_parquet 왕복 / 빈 목록 거부 |
| source | PykrxSource가 os.environ 주입(있으면 미덮어씀 — monkeypatch로 검증) / 자격증명 없이 investor 호출 → MarketAuthError (pykrx 호출 자체가 없어야 함 — monkeypatch로 pykrx 함수를 폭탄으로 대체해 검증) |
| CLI | --company/--stock-code 상호배타 / 부분 수집 경고 출력 + exit 0 / 검증 오류 exit 1 |
| config | market.yaml 로드(test_configs.py 패턴) |

### integration (`@pytest.mark.integration`)

1. **무로그인 경로(항상 실행)**: 000660 OHLCV 2024-01-02~01-31 live → 행수 19~23, 컬럼 계약 일치, close > 0.
2. **KRX 로그인 경로(`KRX_ID`/`KRX_PW` 환경변수 없으면 skip)**: investor·index 소범위 live → 컬럼 계약 일치; 캘린더에 2025-01-01 미포함(휴장) + 주말 미포함.
3. tmp_path 사용(실 data/ 오염 금지).

## 8. 완료 조건 (DoD)

1. `make check` 전부 통과.
2. `r2b collect-market --stock-code 000660` 실행 성공 — 자격증명 없으면 **부분 수집(가격+경고)으로 exit 0**, 있으면 전체 수집.
3. 재실행 시 소스 호출 없음(CACHED).
4. (KRX 자격증명 확보 후 — 메인 세션이 확인) 수급·지수·캘린더 수집, 캘린더가 주말·2025-01-01을 휴장으로 판정, `available_from(금요일 접수일, calendar)` == 다음 월요일(거래일일 때).
5. WeekdayCalendar는 프로덕션 경로에서 미사용(테스트 전용) 유지.
6. 자격증명 값이 로그·출력·meta에 미노출.

## 9. 구현 노트

- pandas는 pandas-stubs로 타입, pykrx 반환은 어댑터에서 `pd.DataFrame`으로 즉시 고정(cast 필요 시 명시).
- parquet 저장은 `to_parquet(engine="pyarrow")`; date는 저장 전 컬럼으로 reset해 명시 스키마(`date` 컬럼)로 통일해도 좋다 — 로드 함수와 왕복 일관성만 보장하라.
- KST = ZoneInfo("Asia/Seoul") — A1·A2와 동일.
- 코드 스타일: 한국어 docstring + README·MILESTONES § 참조, mypy strict·ruff 통과.
