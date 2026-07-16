# A5 구현 명세 — 전략 DSL 스키마·지표·컴파일러 (README §31 M8 축소, Wave 1)

- 근거: README §20(입력), §21(허용 지표·연산자), §23(기본 전략·전략 JSON), §22(정렬 원칙), MILESTONES 정오표 1(lag)
- 원칙: **임의 Python 코드 실행 없음** (README M8 DoD) — 전략은 선언적 JSON만, 컴파일러가 화이트리스트 지표·연산자만 해석한다.
- 비범위: 백테스트 실행·체결·비용(A6), 자연어→DSL LLM 변환(C2), 재무 지표 계산(A4 — 여기서는 컬럼으로 참조만)

## 0. 파일 소유권 (D8 병렬 규칙 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/quant/strategy/**` (신규 패키지)
- `tests/unit/strategy/**` (자체 conftest 허용), `tests/fixtures/strategy/**`
- **금지**: `app/cli.py`, `core/**`, `configs/**`, 공유 테스트 파일. 예외는 `core.exceptions.StrategyValidationError`(이미 존재) 사용.

## 1. 모듈 배치

```text
quant/strategy/__init__.py
quant/strategy/schema.py       # 전략 JSON pydantic 스키마 (README §23.4)
quant/strategy/registry.py     # 지표 레지스트리 (README §21) + lag 문법
quant/strategy/indicators.py   # 가격·수급 지표 계산 (no-lookahead)
quant/strategy/compiler.py     # 검증 + 신호 계산기 컴파일
```

## 2. 스키마 (README §23.4의 JSON이 **그대로** 검증을 통과해야 한다)

```python
class Condition(BaseModel):
    left: str                                  # 지표명
    operator: Literal[">", ">=", "<", "<=", "==",
                      "cross_above", "cross_below", "between"]
    right: float | int | str | list[float]    # 숫자 | 지표명 | between용 [low, high]

class ConditionGroup(BaseModel):               # 재귀 — all/any 정확히 하나
    all: list["Condition | ConditionGroup"] | None = None
    any: list["Condition | ConditionGroup"] | None = None

class MaxHoldingRule(BaseModel):
    type: Literal["max_holding_days"]; value: int          # 거래일 기준
class StopLossRule(BaseModel):
    type: Literal["stop_loss"]; value: float               # 예: -0.10 (진입가 대비)

class ExitSpec(BaseModel):
    any: list[Condition | ConditionGroup | MaxHoldingRule | StopLossRule]

class ExecutionSpec(BaseModel):
    signal_time: Literal["close"] = "close"
    trade_time: Literal["next_open"] = "next_open"         # README §23.3 고정

class UniverseSpec(BaseModel):
    type: Literal["single_asset"]; tickers: list[str]      # MVP: 길이 1

class StrategySpec(BaseModel):
    strategy_name: str
    version: str = "1.0"
    universe: UniverseSpec
    entry: ConditionGroup
    exit: ExitSpec
    execution: ExecutionSpec = ExecutionSpec()
```

- `not` 연산자(§21.4)는 스키마에 자리만 두고 MVP 미지원이면 명시적 StrategyValidationError("미지원")로 처리해도 된다 — 조용한 무시 금지.
- 알 수 없는 필드는 거부(`extra="forbid"`).

## 3. 지표 레지스트리 (README §21 + lag 문법 정식화 — 정오표 1 해소)

- 기본 지표명 화이트리스트: §21.1 재무 12종(A4 metric_id와 동일 명명 — 컬럼 참조만), §21.2 가격 16종, §21.3 수급 4종.
- **lag 문법**: `{base}_lag{n}` (n ≥ 1 정수, 예: `rolling_high_60_lag1`) — base가 화이트리스트에 있으면 유효. README §23.4 예시가 그대로 통과해야 한다.
- 미지원 지표는 `StrategyValidationError`에 **지표명과 허용 목록 힌트**를 담아 거부 (README §31 M8 "지원하지 않는 변수 처리").
- 레지스트리는 각 지표의 소스 분류를 안다: `PRICE`(ohlcv에서 계산) / `FLOW`(수급에서 계산) / `FINANCIAL`(A4 metrics 컬럼 참조) — A6가 데이터 준비에 사용.

## 4. indicators.py — 지표 계산 (핵심: no-lookahead)

입력: A3 daily.parquet 스키마의 DataFrame(`date, open, high, low, close, volume, foreign_net_buy_value, institution_net_buy_value`, date 오름차순).

```python
def compute_indicators(daily: pd.DataFrame, required: set[str]) -> pd.DataFrame:
    """요구된 지표 컬럼을 추가해 반환한다. FINANCIAL 지표는 여기서 계산하지 않는다
    (A6가 A4 metrics를 as-of join으로 공급). 미지원 지표는 StrategyValidationError."""
```

정의(전부 t 시점까지의 정보만 사용, README §22 취지):
- `sma_N` = close.rolling(N).mean() (N=5/20/60/120, min_periods=N)
- `rolling_high_N` = high.rolling(N).max() — **당일 포함**. `_lag1`이 붙으면 `.shift(1)` (README §23.4 각주: 돌파 조건은 직전 N일 고점 사용)
- `return_Nd` = close.pct_change(N) (N=20/60)
- `volatility_20` = close.pct_change().rolling(20).std() * sqrt(252) (연환산 — docstring 명시)
- `rsi_14` = Wilder RSI (EMA α=1/14) — 계산식 docstring 기록
- `atr_14` = Wilder ATR(TR = max(h−l, |h−prev_c|, |l−prev_c|))
- `foreign_net_buy_5d/20d` = foreign_net_buy_value.rolling(5/20).sum() (기관 동일)
- `close`·`open`·`high`·`low`·`volume`는 pass-through
- lag: 임의 지표에 `.shift(n)`

워밍업 구간(rolling 미충족)은 NaN 유지 — 채우지 않는다(A6가 NaN 신호를 False로 처리).

## 5. compiler.py

```python
class PositionRules(BaseModel):
    max_holding_days: int | None
    stop_loss: float | None

class CompiledStrategy(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    spec: StrategySpec
    required_columns: set[str]         # 신호 계산에 필요한 전체 지표명(lag 변형 포함)
    financial_columns: set[str]        # 이 중 FINANCIAL 분류 (A6가 join으로 공급할 컬럼)
    position_rules: PositionRules      # max_holding/stop_loss — 엔진(A6)이 상태 기반으로 집행

def compile_strategy(spec: StrategySpec) -> CompiledStrategy: ...
def entry_signal(compiled: CompiledStrategy, frame: pd.DataFrame) -> pd.Series: ...
def exit_signal(compiled: CompiledStrategy, frame: pd.DataFrame) -> pd.Series: ...   # 조건 기반 exit만 (룰 기반은 엔진)
```

- 조건 평가: left 컬럼 vs right(숫자 or 컬럼). `cross_above(l, r)` = `(l > r) & (l.shift(1) <= r.shift(1))`; `cross_below` 대칭; `between` = `[low, high]` 폐구간.
- NaN 관여 비교는 **False** (신호 없음) — bool 시리즈 반환 전 `fillna(False)`.
- 검증 시점: `compile_strategy`에서 전체 지표·연산자·구조 검증 완료(실행 시 예외 없음). `ExitSpec.any` 안의 MaxHolding/StopLoss는 position_rules로 분리하고 나머지 조건만 exit_signal로 컴파일.
- README §23.1~23.3 기본 전략을 `tests/fixtures/strategy/earnings_flow_breakout.json`으로 저장(§23.4 JSON 그대로) — A6·C2가 재사용한다.

## 6. 테스트 (`tests/unit/strategy/`, 전부 오프라인)

| 대상 | 케이스 |
|---|---|
| schema | §23.4 JSON 검증 통과(fixture 그대로) / extra 필드 거부 / all·any 동시 지정 거부 / 미지원 연산자 거부 |
| registry | 화이트리스트 수용 / `rolling_high_60_lag1` 유효 / `foo_lag1`·`sma_7` 거부(힌트 포함) / 소스 분류(PRICE·FLOW·FINANCIAL) |
| indicators | 손계산 대조(소형 프레임): sma·rolling_high(당일 포함)와 lag1(당일 제외) 구분·return·rolling sum / rsi·atr Wilder 공식 스모크 / **no-lookahead: t행 지표가 t+1행 값 변경에 불변** (프레임 절단 비교 property 테스트) |
| compiler | 기본 전략 컴파일 → required_columns 정확(예: operating_income_yoy는 financial_columns) / cross_above 경계(어제 같음→오늘 초과 = True, 계속 위 = False) / NaN → False / position_rules 분리 / entry·exit 시리즈를 손계산 소형 프레임과 대조 |

## 7. DoD

1. ruff·mypy strict·pytest 전부 통과 (본인 워크트리 전체 스위트)
2. README §23.4 JSON이 무수정으로 검증·컴파일된다
3. no-lookahead 테스트 존재·통과 (README §28.3 취지의 지표 레벨 버전)
4. 미지원 지표·연산자는 전부 명시적 StrategyValidationError
