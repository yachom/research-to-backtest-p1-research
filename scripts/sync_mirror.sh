#!/bin/zsh
# 과제별 미러 레포 스냅샷 빌더 — 정본 main 트리에서 반대편 프로젝트를 제외한 뷰 생성
set -euo pipefail
MAIN=/Users/baemingyu/project/MC_investment_homework
NAME=$1     # p1 | p2
URL=$2
DIR=$(mktemp -d)/mirror-$NAME
rm -rf "$DIR" && mkdir -p "$DIR"
git -C "$MAIN" archive main | tar -x -C "$DIR"
# 미러는 실행·CI를 지원하지 않는 열람용 뷰 — 워크플로 제거 (CI는 정본에서만)
rm -rf "$DIR/.github"

if [[ $NAME == p1 ]]; then
  TITLE="Project 1 — 기업 리서치·투자 가설 (미러)"
  FOCUS="Evidence Store → AI 분석·가설 후보 → 사용자 관점·가설 → 15-섹션 보고서"
  rm -rf "$DIR/src/research_backtest/quant" \
         "$DIR/tests/unit/strategy" "$DIR/tests/unit/backtest" \
         "$DIR/tests/unit/test_cli_backtest.py" "$DIR/tests/unit/test_cli_strategy_draft.py" \
         "$DIR/tests/integration/test_backtest_run.py" "$DIR/tests/integration/test_strategy_draft_live.py"
else
  TITLE="Project 2 — 전략 DSL·Point-in-Time 백테스트 (미러)"
  FOCUS="승인 가설 → 전략 DSL 초안·컴파일 → PIT 백테스트 엔진 → 강건성 분석"
  rm -rf "$DIR/src/research_backtest/research" \
         "$DIR/tests/unit/research" \
         "$DIR/tests/integration/test_candidates_live.py" "$DIR/tests/integration/test_evidence_build.py" \
         "$DIR/tests/integration/test_report_live.py"
fi

# README 상단에 미러 고지 삽입
NOTICE="> **⚠️ 미러 레포(읽기 전용) — $TITLE**
> 정본·실행·이슈 관리는 상위 레포 [research-to-backtest](https://github.com/yachom/research-to-backtest)에서 한다.
> 이 미러는 해당 프로젝트 관점의 코드·문서·제출물만 담은 뷰이며(반대편 프로젝트 디렉토리 제외),
> 일부 모듈이 상위의 공용 코드를 참조하므로 **단독 실행은 지원하지 않는다**.
> 범위: $FOCUS
"
printf '%s\n%s' "$NOTICE" "$(cat "$DIR/README.md")" > "$DIR/README.md"

cd "$DIR"
git init -q -b main
git add -A
git -c user.name="$(git -C "$MAIN" config user.name)" -c user.email="$(git -C "$MAIN" config user.email)" \
  commit -q -m "미러 스냅샷: $TITLE — 정본 research-to-backtest@$(git -C "$MAIN" rev-parse --short main)

자동 생성된 과제 뷰. 수정은 정본에서만 한다.

Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>"
git remote add origin "$URL"
git push -q -u --force origin main  # 미러는 읽기 전용 스냅샷 — 항상 정본으로 덮어씀
echo "$NAME OK -> $URL ($(git rev-parse --short HEAD))"
