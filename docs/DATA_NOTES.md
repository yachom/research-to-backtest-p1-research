# 실데이터 관찰 기록 (Data Notes)

수집된 실제 데이터에서 관찰된, 후속 마일스톤 설계에 영향을 주는 특성을 기록한다.
(근거 데이터: SK하이닉스 00164779, 2021~2025 사업연도, 전체 재무제표 API 40개 응답 6,436행 — A2 수집분)

## A2 수집분 → A4(계정 정규화·시계열) 설계 입력

1. **SK하이닉스는 손익계산서(IS)가 없다 — 손익이 전부 CIS(포괄손익계산서)에 있다.**
   40/40 파일 모두 sj_div=IS 행이 0개. 매출액·영업이익·당기순이익이 단일
   포괄손익계산서 체계로 CIS에 담긴다. → **registry의 손익 계정은 IS·CIS 모두
   허용해야 한다** (configs/account_registry.yaml의 statement_type 처리 주의).

2. **`account_id = "-표준계정코드 미사용-"` 행이 857/6,436 (13.3%).**
   유형자산의취득 등 CF 세부계정이 다수 포함. → account_id 단독 매칭은 불가능,
   **account_nm(label) 매칭을 반드시 병행** (README §12.1 우선순위 그대로).

3. **동일 account_id가 여러 재무제표에 등장한다.**
   `ifrs-full_ProfitLoss`가 SCE에 176행, `ifrs-full_Equity`가 SCE에 280행 등.
   SCE는 account_detail에 차원 정보(`연결재무제표 [member]`, `자본 [구성요소]|…`)를
   담는다. → **account_id 매칭 시 sj_div 필터 필수**, SCE는 A4 범위에서 제외하거나
   account_detail 차원 해석 후 사용.

4. **금액 필드 특성**: `thstrm_amount` 빈 행 125개(1.9%, CF 44·BS 15·SCE 66) —
   빈 값→None 처리 필요(0과 구분, README §9.6). `thstrm_add_amount`(분기 누적)
   10.2%, `frmtrm_q_amount` 49% 존재 — **단독분기 역산(README §11) 소스 확보 확인됨**.

5. **currency는 100% KRW. 파일당 rcept_no는 전부 1개** — 이 기간 SK하이닉스
   정기보고서에 정정 흔적 없음. Current View 한계(financial_api.py docstring)는
   B4에서 처리하되, MVP 데이터에서는 실질 차이 없음.

6. **2024 반기보고서부터 SCE 행수 급증**(약 50→120행, 당기·전기 2기간 표시).
   SCE를 쓰게 되면 기간 구분 주의.

## A3 시장 데이터(pykrx 1.2.8) 관찰 — 2026-07-14 실측

1. **KRX 로그인 의무화(2025~)**: 수정주가 OHLCV(`adjusted=True`, Naver 경유)만
   무로그인 동작. 투자자 수급·지수·원주가는 `KRX_ID`/`KRX_PW`(data.krx.co.kr
   무료 계정) 필요. **미로그인 실패가 예외가 아니라 빈 DataFrame으로 위장**되므로
   어댑터는 빈 결과를 오류로 취급한다.
2. **pykrx는 import 시점에 로그인을 시도**한다 → 자격증명 주입(os.environ) 후
   lazy import 필수 (core/market/source.py). 로그인 성공 시 pykrx 자체가
   stdout에 `로그인 ID: <값>`을 출력한다(pykrx 코드, 억제 불가 — 허용 소음).
3. 무로그인 OHLCV 실측: 000660 2015-01-02~2026-07-13 **2,829행**, 컬럼
   시가/고가/저가/종가/거래량(int64)+등락률(float64). volume=0 행 0개,
   high<low 위반 0건. 수정주가는 소급 수정 방식이며 거래량은 미수정일 수 있음
   (신호·체결 모두 수정주가 기준 — MVP 설계 결정, source.py docstring).
4. 투자자 순매수 원본 컬럼(소스 실측): 기관합계/기타법인/개인/외국인합계/전체
   (`on="순매수"` 기본) → foreign/institution_net_buy_value로 매핑.
5. 거래일 캘린더는 KOSPI 지수(1001) 거래일에서 구축 — 종목 거래정지에
   영향받지 않는다. coverage 밖 조회는 CalendarRangeError로 즉시 실패
   (주말 로직 대체 금지 — 룩어헤드 방지).

## A4 재무 정규화 실측 (2026-07-14, 00164779 5개년 빌드)

1. **분기 CF의 금액 의미론은 손익과 정반대다.** CF 행에는 `thstrm_add_amount`
   필드가 아예 없고 **`thstrm_amount`가 누적(YTD)**이다(손익은 thstrm=3개월,
   add=누적). 단독분기 CF는 인접 누적의 차분으로만 얻으며 Q2~Q4가 전부
   DERIVED_QUARTER다. telescoping 성질로 4개 단독 합=연간이 5개년 모두 정확 성립.
2. **trade_receivables는 concept 미일치의 실증 사례**: SK하이닉스는 표준
   `ifrs-full:TradeAndOtherCurrentReceivables`가 아니라
   `ifrs-full_CurrentTradeReceivables`로 보고 → label('매출채권') 경로로 매칭됨.
   README §12.1의 concept→label 다단 매칭이 실제로 필요함을 확인.
3. **YoY abs-분모 규약 실측**: 2023 적자 기저에서 2024 operating_income_yoy가
   전 분기 양수(1.85/2.90/4.92/22.36), 2025는 1.578/0.685/0.619/1.372(Q4 파생).
   §23 기본 전략의 "YoY > 0.2" 신호가 의미 있게 작동하는 값 범위다.
4. 회계식(자산=부채+자본) 50개 기간 전부 통과 — 단 2022 Q3 영업이익 교차검증에서
   정확히 -1,000,000 KRW 반올림 오차 관찰(허용오차 경계 사례, `<=` 판정 필요).
5. 매칭 커버리지: 11개 계정 × 40파일 전량, UNRESOLVED 0. 미매칭 3,765행은
   세부계정(정상), SCE 2,231행은 설계대로 스킵.

## B1+B2 XBRL 실측 (2026-07-14, SK하이닉스 2021~2025 정기보고서 22건)

1. **ZIP 구성**: instance **1개**(`entity{corp_code}_{결산일}.xbrl`) + `.xsd` + 링크베이스
   5종(`_cal`·`_def`·`_pre`·`_lab-ko`·`_lab-en`). 파일명에 결산일이 들어가 비고정 →
   루트 태그 `{xbrli}xbrl`로 판별(파일명 가정 금지 원칙 유효).
2. **연결·별도는 파일로 분리되지 않는다** — 단일 instance 안에서
   `ConsolidatedAndSeparateFinancialStatementsAxis`(Consolidated/SeparateMember)
   **차원**으로 구분된다. ⇒ **README §10.1의 "추가 Dimension이 없는 기본 Context"
   규칙은 실데이터에 존재하지 않는다**(차원 0 context는 1개뿐, Assets 아님).
   B3의 Context 선택 규칙은 "연결/별도 축 **하나만** 있는 context에서 scope에 맞는
   member 선택"으로 수정해야 한다.
3. entity identifier scheme=`http://dart.fss.or.kr/ifrs/CIK`, 값=corp_code.
   차원은 전부 segment(scenario 미사용). nil fact 0건, decimals에 `INF` 존재.
4. 규모: 사업보고서당 fact 7,000~8,000(ifrs-full 5,000~6,300 + 기업 확장
   entity00164779 ~1,000 + dart ~600~830 + dart-gcd 114), context ~2,400~2,900.
5. **2020.12 사업보고서는 원본(20210322000782)과 [기재정정](20210330000776) 두
   접수번호가 모두 수집됨** — B4 정정공시 버전 그래프의 실데이터 케이스.

## A6 첫 실데이터 백테스트 관찰 (2026-07-14, §23 기본 전략, 000660)

1. **거래 5건 전부 2024~2025** — 재무 metrics가 2021-05부터 존재(2021-Q1 YoY에
   2020 분기 필요)하고, 진입 3중 조건(YoY>20% ∧ 외인 20일 순매수>0 ∧ 60일 돌파)이
   드묾. 2016~2020 무포지션은 테스트로 고정(재무 신호 부재 시 NaN→False 정상 동작).
2. **손절의 갭 리스크가 실데이터에서 그대로 발현**: 2025-01 거래는 -10% 손절
   기준인데 실현손실 -12.9% — 종가 기준 판정 후 익일 시가 체결이라 갭다운을 못
   잡는다(설계에 문서화된 한계의 실증). 보고서 한계 절의 소재.
3. 표본 수(5거래)가 작아 통계적 유의성 논의 필수 — §24.3 조건 제거 분석과 함께
   보고서에서 정면으로 다룰 것(C3').
4. 시장 노출 5~10%로 자산 B&H(~20배)에 크게 못 미치나 2021~2025 KOSPI 대비
   +67.6%p 초과 — "노출 대비 효율"과 "절대 수익" 관점을 분리해 해석해야 함.
5. 실데이터 financial_metrics는 현재 4종(YoY 3종+operating_margin)뿐 —
   roe·debt_ratio 등을 쓰는 전략은 A4 확장 전까지 컴파일 단계에서 명시적 실패.

## B3 API-XBRL 대조 실측 (2026-07-14, 290행 대조)

1. **연간 5개년 × CFS/OFS × 7계정 = 70건 전량 MATCH(100%)** — 전체 290행 중
   MATCH 260, REQUIRES_REVIEW 30(전부 benign), CONTEXT/SCOPE/MISSING 계열 0건.
2. **Q1 손익의 구조적 context 중복**: Q1 보고서는 3개월(...dFQQ)과 누적(...dFQA)
   context가 **동일 (1/1~3/31) 기간·동일 값**으로 공존 → 날짜만으로 구분 불가,
   후보 2개 → REQUIRES_REVIEW 30건의 전부(값은 일치). Q1은 단독=누적이므로 무해.
3. **연결/별도 member 실측**: 축 `ifrs-full:ConsolidatedAndSeparateFinancialStatementsAxis`,
   member `ConsolidatedMember`/`SeparateMember`.
4. **오분류 함정 2건**: ① dart taxonomy uri(`.../ifrs/dart`)가 'ifrs' 문자열을
   부분포함 — naive 포함 매칭 금지, taxonomy tail 기준으로 판정 ② `dart-gcd:`에도
   동명 Consolidated/SeparateMember가 있으나 **다른 축**(StatementInformationAxis)
   — "해당 축 ∧ 차원 정확히 1개" 조건으로 배제.
5. registry 7계정의 concept/label 매칭이 XBRL 쪽에서도 100% 동작 — 개정 불요.

## 기업 식별(A1) 관찰

- 축약 검색어가 동명의 비상장사에 정확 일치할 수 있다 — 예: "삼성" →
  비상장 "삼성"(00893765)에 EXACT_NAME 매칭. 후보 테이블 + 종목코드 안내로
  커버 중. alias 테이블은 후순위.
- 고유번호 파일 규모: 118,484개사 (2026-07-14 기준).

## 다기업 확장 관찰 (W3d — 네이버 첫 수집, 2026-07-15)

- **네이버(00266961) 2020 CFS에서 cross_source_consistency 2/80 위반**: revenue·
  operating_income의 3Q 누적이 (반기누적 + Q3단독)과 불일치(예: 매출 3.79조 ≠ 5.00조).
  2020년 LINE 계열 중단영업 재분류로 분기 보고서 간 계정 범위가 달라진 것으로 추정 —
  SK하이닉스에서는 나타나지 않던 유형. 검증은 표시용(비차단, CLI build-financials와
  동일 선례)이라 준비 플로는 정상 완료. 후속 과제: 중단영업/재분류 감지 규칙(§32
  다기업 일반화의 일부).
- 신규 기업 최초 준비 실측(2015~2025): 재무 88요청 17.4초 · 시장(신규 로그인 포함)
  37.4초 · 빌드 0.12초 — 합 ~55초. UI 예상 시간 산식을 이 실측으로 보정(명세 W3d §3).
