# A6 구현 명세 — 백테스트 엔진 (README §31 M9, Wave 2)

- 근거: README §22(재무 정렬·As-of Join·금지 사항), §23(기본 전략·실행), §24.1(성과지표), §28.3(룩어헤드 방지 테스트), 1804_FEEDBACK §8·§13(승인 게이트), configs/backtest.yaml
- 입력 계약(전부 병합 완료된 실물): A3 `data/normalized/market/{stock_code}/daily.parquet`·`index_{code}/daily.parquet`·KRX 캘린더, A4 `data/normalized/financials/{corp_code}/financial_metrics.parquet`(available_from 포함), A5 `quant/strategy`(compile_strategy·compute_indicators·entry_signal/exit_signal·PositionRules — A5 인계 계약과 test fixture `earnings_flow_breakout.json` 참고), H1 `core/hitl`(gates.ensure_strategy_approved, StrategyReview)
- 비범위: 강건성 분석(C3'), CLI 연결(메인 세션), run_id 오케스트레이션(메인 세션), BacktestInterpretation 작성(사용자)

## 0. 파일 소유권 (D8 — 이 목록 밖 파일 수정 금지)

- `src/research_backtest/quant/backtest/**` (신규 패키지)
- `tests/unit/backtest/**`(자체 conftest 허용), `tests/fixtures/backtest/**`, `tests/integration/test_backtest_run.py`
- **금지**: `quant/strategy/**`(A5 산출물 — 소비만), `core/**`, `app/cli.py`, configs(**소비만**), 공유 테스트 파일

## 1. 모듈 배치

```text
quant/backtest/__init__.py
quant/backtest/data.py       # 데이터 준비: daily + financial metrics as-of join
quant/backtest/engine.py     # 신호→체결 시뮬레이션 (포지션 상태 머신)
quant/backtest/costs.py      # 수수료·세금·슬리피지 (configs/backtest.yaml 로더 포함)
quant/backtest/metrics.py    # 성과지표 (§24.1)
quant/backtest/runner.py     # 게이트 강제 진입점 + 산출물 저장
```

## 2. data.py — As-of Join (README §22, **가장 중요한 정확성 요구**)

```python
def build_backtest_frame(
    daily: pd.DataFrame,            # A3 스키마 (date,open,high,low,close,volume,수급 2컬럼)
    metrics: pd.DataFrame,          # A4 financial_metrics.parquet
    *, fs_scope: str = "CFS",
    start_date: date, end_date: date,
) -> pd.DataFrame:
```

- metrics를 `fs_scope`로 필터 → metric_id별로 wide 피벗(컬럼명 = metric_id — A5 financial_columns와 동일 명명) → **available_from 기준 `merge_asof`**로 daily에 병합: 거래일 t에는 `available_from <= t`인 가장 최근 값만 보인다. 다음 공시 전까지 직전 값 유지(§22.2).
- **금지 사항(§22.3) 코드화**: period_end 기준 병합 금지 — join 키는 오직 available_from. 병합 후 방어 검증: 임의 행 t의 metric 값이 유래한 available_from ≤ t 임을 확인하는 `assert_no_lookahead(frame_meta)` 헬퍼를 제공하고 위반 시 `LookaheadError`.
- 동일 metric의 available_from 중복(같은 날 두 값 — 이론상 정정) 시 rcept_dt 최신 우선, 발생 사실 기록.
- 워밍업: start_date 이전 구간의 지표 계산을 위해 daily는 start보다 앞서 로드하고, 결과 frame은 [start,end]로 절단하되 rolling 지표는 절단 전에 계산(A5 compute_indicators 호출 순서: **financial join → compute_indicators → 절단**, A5 인계 권장 순서).

## 3. engine.py — 체결 시뮬레이션 (README §23.3)

포지션 상태 머신 (single asset, long/cash):

- 신호는 t일 **종가 기준**으로 평가(entry_signal/exit_signal[t] — A5가 이미 t까지 정보만 사용), 체결은 **t+1 거래일 시가**.
- **진입**: flat ∧ entry_signal[t] → t+1 open 매수. 체결가 = `open*(1+slippage_rate)`, 수수료 = 체결금액×commission_rate. 주식 수 = `floor(cash/체결가)` 정수, 잔여는 현금.
- **청산** (t일 종가 시점 판정, 우선순위 순):
  1. `stop_loss`: `close[t]/entry_price - 1 <= stop_loss` (진입가 대비 종가, 장중 저가 미반영 — docstring에 한계 명시)
  2. `max_holding_days`: 보유 거래일 수(진입 체결일 포함) ≥ N
  3. 조건 exit: exit_signal[t]
  → t+1 open 매도. 체결가 = `open*(1-slippage_rate)`, 수수료 + `sell_tax_rate`(매도 금액 기준).
- 동시 발생 시 청산 우선. 청산 체결일(t+1)의 종가 신호로 재진입 판정 가능(그날 close 기준 → t+2 open 진입) — 같은 봉 재진입 없음.
- 마지막 날 처리: 신호가 마지막 거래일이면 체결 불가로 폐기. 데이터 종료 시 미청산 포지션은 마지막 종가로 강제 청산하고 `exit_reason="END_OF_DATA"` 표기.
- 진입 당일(체결일)의 stop/max 판정도 그날 종가부터 시작한다.
- TradeRecord: entry_signal_date, entry_date, entry_price, shares, exit_signal_date, exit_date, exit_price, holding_days(거래일), pnl(원), pnl_pct, exit_reason(`SIGNAL|MAX_HOLDING|STOP_LOSS|END_OF_DATA`), costs(수수료+세금 합계).
- 일별 기록 DailyPortfolioRow: date, position(0/1), shares, cash, equity(= cash + shares×close), daily_return.

## 4. metrics.py — 성과지표 (§24.1 전 항목, 공식 고정)

일별 수익률 r = equity.pct_change(). 연환산 계수 252(거래일). 정의:

누적수익률 `equity[-1]/equity[0]-1` / CAGR `(equity[-1]/equity[0])**(252/일수)-1` /
연환산 변동성 `std(r)*√252` / Sharpe `mean(r)/std(r)*√252`(rf=0 명시) /
Sortino `mean(r)/std(r[r<0])*√252` / MDD `min(equity/cummax(equity)-1)` /
Calmar `CAGR/|MDD|` / 승률 `wins/총거래` / 평균 손익 avg_win·avg_loss·payoff 분리 /
Profit Factor `Σ이익/|Σ손실|` / 거래 횟수·평균 보유기간 / 시장 노출률 `포지션 보유일/전체 거래일` /
벤치마크(KOSPI close 동일 기간 buy&hold) 누적수익률·초과수익률 /
Information Ratio `mean(r−r_bm)/std(r−r_bm)*√252`.

- **엣지 케이스**: 거래 0건 → 거래 기반 지표 None + `has_trades=false` 플래그, std=0·분모 0 → None (NaN 아님). Buy & Hold 비교(전략과 동일 비용으로 첫날 매수-마지막날 보유)도 포함(README M9 DoD "Buy & Hold 비교").
- `BacktestResult`(pydantic): 기간·설정 에코(전략명·비용 파라미터), 성과지표 전부, 벤치마크·B&H 비교, has_trades.

## 5. runner.py — 게이트 강제 진입점 (1804 §8·§13, AI_ROLE_BOUNDARY §3)

```python
def execute_approved_strategy(
    review: StrategyReview, *, data_dir: Path, stock_code: str, corp_code: str,
    start_date: date, end_date: date, out_dir: Path, backtest_config: BacktestConfig,
) -> BacktestResult:
```

- **첫 줄에서 `gates.ensure_strategy_approved(review)`** — 승인 없으면 ApprovalGateError(형식적 검사가 아니라 실행 경로의 유일한 공식 진입점이 되도록, engine을 직접 부르는 것은 테스트·연구용으로만 docstring에 명시).
- `review.final_strategy`를 A5 `parse_strategy_spec → compile_strategy`로 재검증(승인본이 DSL 규칙을 여전히 만족하는지).
- 산출물 저장: `out_dir/backtest_result.json`, `trade_log.csv`, `daily_portfolio.csv` (README §2 Project 2 출력, HITL 산출물 규약과 파일명 일치).

## 6. 테스트

### unit (`tests/unit/backtest/`, 오프라인 — 손으로 만든 소형 시계열 fixture)

| 대상 | 케이스 |
|---|---|
| data | as-of join: 공시 전 NaN·공시 후 값·다음 공시에서 교체(경계일 available_from 당일 포함) / **§28.3-1: 공시일 이전 행에 재무값 존재하면 실패** 테스트 / scope 필터 |
| engine | 진입·청산 각 사유(SIGNAL/MAX_HOLDING/STOP_LOSS/END_OF_DATA) 손계산 대조 / **§28.3-3: t 종가 신호가 t 종가·t 시가에 체결되면 실패**(체결가는 반드시 t+1 open) / 마지막 날 신호 폐기 / 정수 주식수·현금 잔여 / 재진입 시퀀스 / 비용 반영 손계산 |
| metrics | 각 지표 손계산 대조(3~5거래 fixture) / 0거래·std=0 엣지 → None / B&H 비교 |
| runner | 미승인 review → ApprovalGateError·산출물 미생성 / 승인 review → 3개 파일 생성 |
| no-lookahead property | **데이터를 뒤에서 절단해도 절단 전 구간의 거래 목록이 동일** (핵심 property, §28.3 총괄) |

### integration (`tests/integration/test_backtest_run.py`, DATA_DIR 실데이터 — 없으면 skip)

- §23 기본 전략(fixture JSON)으로 2016-01-01~2025-12-31 실행: 예외 없이 완료, 결과 파일 3종 생성, `assert_no_lookahead` 통과, B&H·KOSPI 비교 산출. 거래 수·성과는 **assert하지 말고 보고에 기록**(전략 성과는 검증 대상이 아니라 관찰 대상).
- 주의: 재무 metrics는 2021~2025 데이터뿐이므로 2016~2020 구간은 재무 신호 부재(NaN→False)로 무포지션이 정상 — 이 동작 자체를 확인. 2021~2025 부분 구간 실행도 함께 보고.

## 7. DoD

1. ruff·mypy strict·pytest 전부 통과 (워크트리 전체 스위트)
2. §28.3 룩어헤드 테스트 3종(공시일 정렬·당일 체결 금지·절단 불변) 전부 존재·통과
3. 실데이터 기본 전략 end-to-end 실행 성공 + 결과 요약을 보고에 포함 (거래 수·수익률·MDD·B&H 대비 — 있는 그대로)
4. 미승인 전략이 runner를 통과할 수 없음을 테스트로 증명
5. 데이터 접근은 DATA_DIR 환경변수(메인 데이터 디렉토리), API 키 불필요
