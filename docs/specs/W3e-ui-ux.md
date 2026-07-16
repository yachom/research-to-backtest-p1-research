# W3e — Streamlit UX 감사·개선 명세 (2026-07-15)

> 발주: 메인 세션. 배경: 사용자가 실제 run을 돌리며 보고한 UX 결함 2건을 코드로
> 역추적한 감사 결과와 개선 계약. 단일 트랙(**Sonnet**, 브랜치 `w3e-ui-ux`).

## 1. 감사 결과 (실사용 보고 → 근본 원인)

### U1. "화면 하나 제출하면 초기 화면으로 돌아가 다시 run을 선택해야 한다" — 원인 2중

- **U1a. 네비게이션 상태 비보존**: 본문이 `st.tabs`인데 Streamlit 탭은 rerun마다
  **첫 탭으로 리셋**된다(상태 보존 API 없음). 모든 저장 버튼이 `st.rerun()`으로
  끝나므로 매 제출 후 화면①로 떨어진다 (streamlit_app.py:34·54).
- **U1b. run 선택 리셋**: 사이드바 selectbox 옵션 라벨이
  `"{run_id} — {company} [{상태}]"`로 **가변 상태를 포함**한다(screens.py:126~).
  저장이 상태를 전진시키면 직전에 선택했던 옵션 문자열이 목록에서 사라지고,
  Streamlit은 존재하지 않는 값 대신 index 0("(선택 안 함)")으로 되돌린다 —
  전이가 일어나는 **모든** 제출에서 run 선택이 풀린다.

### U2. "관점 선택이 id만 나와서 일일이 확인해야 한다"

- 화면 ③의 선택/제외 근거 multiselect와 제외 이유 라벨, 화면 ④의 근거
  multiselect가 전부 **원시 evidence_id 목록**이다(`load_evidence_manifest_ids` —
  actions.py:267, screens.py:561·568·587·722).
- 그런데 `evidence_manifest.json`에는 이미 `statement`(한국어 한 문장)·`category`·
  `significance_score`가 들어 있다(실파일 확인) — **데이터는 있는데 UI가 안 쓴다**.

### U3. 저장 후 다음 단계로 자동 이동 없음 (U1과 결합해 체감 악화)

매 단계: 제출 → 화면① 리셋(U1a) → run 재선택(U1b) → 다음 탭 수동 클릭 —
3회 조작이 강제된다. 정상 UX는 "저장 → 성공 표시 → 다음 화면 자동 진입" 1회다.

### U4. (경미) run 생성 진입점 이중화

사이드바 expander와 화면①에 같은 생성 폼이 둘 — 혼란 요인.

### U5. (경미·보류) LLM 대기 중 실시간 경과 카운터 부재

W3d가 사전 예상·사후 실측은 넣었다. 단일 블로킹 호출 중 실시간 카운터는
스레드 없이는 불가(W3d 이탈 기록 3) — 이번 범위에서 제외, 기록만.

## 2. 개선 설계 (구현 계약)

### F1. run 선택 안정화 (U1b)

- selectbox **옵션 값을 불변인 run_id**로 바꾸고 표시는 `format_func`로:
  `format_func=lambda rid: f"{rid} — {company} [{상태}]"` (None 옵션은 "(선택 안 함)").
  상태가 전이돼도 옵션 정체성(run_id)이 유지되므로 선택이 풀리지 않는다.
- 기존 pending 자동 선택(`_PENDING_RUN_SELECT_KEY`)은 run_id 매칭이므로 값 대입만
  run_id로 단순화(라벨 조립·startswith 매칭 제거).

### F2. 상태 보존 네비게이션 (U1a)

- `st.tabs` 제거 → **사이드바 radio**(또는 `st.segmented_control`) +
  `key="nav_screen"`으로 현재 화면을 session_state에 보존. rerun에도 유지된다.
- 잠긴 화면도 목록에는 보이되 선택 시 기존 잠금 배너(사유)를 그대로 렌더
  (게이트 UX 불변). streamlit_app.py의 분기(선택 run 없음/있음)는 유지하되
  "화면 렌더 1개만" 구조로 단순화한다.

### F3. 저장 성공 시 자동 전진 (U3)

- pending-nav 패턴(기존 pending run-select와 동일 메커니즘): 저장 성공 경로가
  `_goto_screen_on_next_rerun(n)`으로 예약 → 다음 rerun에서 nav 위젯 생성 **전**에
  `nav_screen`에 적용. 매핑: ③저장→④, ④승인→⑤, ⑤초안 생성→⑤ 유지, ⑤승인→⑥,
  ⑥백테스트 완료→⑦, ⑦제출→⑦ 유지(완료 안내), ②후보 생성→③, 화면①/사이드바
  run 생성→②. 각 저장 직후 `st.toast`(또는 성공 배너)로 "저장됨 — 화면 N로 이동".
- run 생성의 기존 pending run-select와 조합되어 "생성 → 새 run 선택 + 화면②"가
  한 번에 이뤄져야 한다.

### F4. 근거를 사람이 읽는 형태로 (U2)

- actions에 `load_evidence_entries(store) -> list[EvidenceEntry]`
  (`EvidenceEntry(evidence_id, category, statement, significance_score)`) 신설 —
  manifest의 기존 필드를 그대로 파싱(부재 필드는 빈 값 허용 — 구버전 manifest 호환).
  기존 `load_evidence_manifest_ids`는 이를 위임 사용.
- 공용 라벨 헬퍼 `evidence_label(entry) -> str`:
  `"[카테고리] statement (유의도 0.91 · FIN_…)"` — statement 60자 축약.
- 적용 지점: 화면 ③ 선택/제외 multiselect(`format_func`), 제외 이유 라벨
  (id 대신 라벨), 화면 ④ 근거 multiselect(`format_func`), 화면 ②의 후보 카드에서
  evidence_ids 나열부(있다면 동일 라벨). 저장되는 값은 여전히 **id**(모델 계약 불변).

### F5. run 생성 일원화 (U4)

- 사이드바 expander의 생성 폼 제거, "새 run은 화면①에서" 캡션 + 화면①로 이동
  버튼(pending-nav 재사용)으로 대체. (화면①이 데이터 준비 패널까지 갖춘 정본 경로.)

## 3. 수용 기준 (AppTest·단위로 검증)

1. **선택 유지**: 화면③ 저장(상태 전이 발생) 후 예외 없음 + sidebar 선택 run_id
   동일 + 현재 네비게이션이 화면④ (신규 AppTest — U1·U3 회귀 고정).
2. run 생성(기존 run 존재 상태) → 새 run 선택 + 네비게이션 화면② (기존 회귀
   테스트 확장).
3. `evidence_label`·`load_evidence_entries` 단위 테스트: 라벨 구성·60자 축약·
   구버전 manifest(statement 없음) 폴백. (AppTest는 format_func 적용 문자열
   접근이 제한적이므로 라벨은 단위 테스트로, multiselect의 옵션 값이 id임은
   AppTest로 확인.)
4. 잠긴 화면 선택 시 잠금 배너 렌더(게이트 UX 불변), 기존 862 테스트 무손상
   (탭 → radio 전환으로 깨지는 기존 AppTest는 새 네비게이션 방식으로 갱신).
5. `make check` 클린.

## 4. 소유권

- **수정**: `app/streamlit_app.py`, `app/ui/{state,actions,screens}.py`,
  `tests/unit/ui/` 전체. **금지**: `core/`·`research/`·`quant/`·`app/cli.py`·
  `app/commands/`·`docs/`·`configs/`. LLM 호출 예산 0회(전부 Fake/픽스처).
- 게이트·검증·상태 전이 정책 및 저장 산출물 형식 불변.

## 5. 진행 관례

W3d와 동일 — 워크트리·`make check`·소유권 diff 확인·명세 이탈 보고, 트레일러
`Co-Authored-By: Claude Sonnet <noreply@anthropic.com>`. 병합·수동 E2E 재검증·
미러 동기화·푸시는 메인 세션.
