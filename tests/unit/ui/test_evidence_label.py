"""``evidence_label``·``load_evidence_entries`` 단위테스트 (docs/specs/W3e-ui-ux.md F4).

Streamlit에 의존하지 않는 순수 함수라 AppTest 없이 직접 검증한다 — 라벨 문자열
자체는 AppTest의 ``format_func`` 적용 결과에 접근하기 어렵기 때문에(명세 §3-3
"AppTest는 format_func 적용 문자열 접근이 제한적") 여기서 단위테스트로 고정하고,
multiselect의 저장 값이 evidence_id임은 ``tests/unit/ui/test_streamlit_app.py``의
AppTest가 확인한다.
"""

from __future__ import annotations

import json
from pathlib import Path

from research_backtest.app.ui import actions
from research_backtest.core.hitl.store import RunStore


def _write_manifest(run_dir: Path, evidence: list[dict[str, object]]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence_manifest.json").write_text(
        json.dumps({"evidence": evidence}, ensure_ascii=False), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# load_evidence_entries — manifest 파싱 (신버전 전체 필드 · 구버전 폴백 · 없음/손상)
# ---------------------------------------------------------------------------


def test_load_evidence_entries_parses_full_manifest(tmp_path: Path) -> None:
    store = RunStore(tmp_path, "run1")
    _write_manifest(
        store.run_dir,
        [
            {
                "evidence_id": "FIN_OP_INCOME_TURN_2024Q4",
                "category": "PROFITABILITY",
                "statement": "영업이익이 흑자로 전환되었다.",
                "significance_score": 0.913,
            }
        ],
    )

    entries = actions.load_evidence_entries(store)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.evidence_id == "FIN_OP_INCOME_TURN_2024Q4"
    assert entry.category == "PROFITABILITY"
    assert entry.statement == "영업이익이 흑자로 전환되었다."
    assert entry.significance_score == 0.913


def test_load_evidence_entries_fallback_when_fields_missing(tmp_path: Path) -> None:
    """구버전 manifest — evidence_id만 있는 항목도 유효한 항목으로 파싱한다."""
    store = RunStore(tmp_path, "run1")
    _write_manifest(store.run_dir, [{"evidence_id": "FIN_LEGACY_2020Q1"}])

    entries = actions.load_evidence_entries(store)

    assert len(entries) == 1
    entry = entries[0]
    assert entry.evidence_id == "FIN_LEGACY_2020Q1"
    assert entry.category == ""
    assert entry.statement == ""
    assert entry.significance_score is None


def test_load_evidence_entries_missing_file_returns_empty(tmp_path: Path) -> None:
    store = RunStore(tmp_path, "run1")
    assert actions.load_evidence_entries(store) == []


def test_load_evidence_entries_malformed_json_returns_empty(tmp_path: Path) -> None:
    store = RunStore(tmp_path, "run1")
    store.run_dir.mkdir(parents=True, exist_ok=True)
    (store.run_dir / "evidence_manifest.json").write_text("not json", encoding="utf-8")

    assert actions.load_evidence_entries(store) == []


def test_load_evidence_manifest_ids_delegates_to_entries(tmp_path: Path) -> None:
    store = RunStore(tmp_path, "run1")
    _write_manifest(
        store.run_dir,
        [
            {"evidence_id": "A", "category": "GROWTH", "statement": "x", "significance_score": 0.1},
            {"evidence_id": "B"},
        ],
    )

    assert actions.load_evidence_manifest_ids(store) == ["A", "B"]


# ---------------------------------------------------------------------------
# evidence_label — 라벨 구성·60자 축약·구버전 폴백
# ---------------------------------------------------------------------------


def test_evidence_label_full_entry() -> None:
    entry = actions.EvidenceEntry(
        evidence_id="FIN_OP_INCOME_TURN_2024Q4",
        category="PROFITABILITY",
        statement="영업이익이 흑자로 전환되었다.",
        significance_score=0.913,
    )

    label = actions.evidence_label(entry)

    assert label == (
        "[PROFITABILITY] 영업이익이 흑자로 전환되었다. (유의도 0.91 · FIN_OP_INCOME_TURN_2024Q4)"
    )


def test_evidence_label_truncates_statement_at_60_chars() -> None:
    long_statement = "가" * 80
    entry = actions.EvidenceEntry(
        evidence_id="FIN_X", category="GROWTH", statement=long_statement, significance_score=0.5
    )

    label = actions.evidence_label(entry)

    # "[GROWTH] " 접두 + 60자로 축약된 statement("…" 포함) + " (유의도 0.50 · FIN_X)".
    truncated = long_statement[:60] + "…"
    assert label == f"[GROWTH] {truncated} (유의도 0.50 · FIN_X)"
    assert long_statement not in label


def test_evidence_label_short_statement_not_truncated() -> None:
    entry = actions.EvidenceEntry(
        evidence_id="FIN_X", category="GROWTH", statement="짧은 문장", significance_score=0.5
    )

    label = actions.evidence_label(entry)

    assert "…" not in label
    assert "짧은 문장" in label


def test_evidence_label_legacy_manifest_no_statement_falls_back_to_id() -> None:
    """구버전 manifest(statement 없음) 폴백 — evidence_id만 반환한다."""
    entry = actions.EvidenceEntry(
        evidence_id="FIN_LEGACY_2020Q1", category="", statement="", significance_score=None
    )

    assert actions.evidence_label(entry) == "FIN_LEGACY_2020Q1"


def test_evidence_label_missing_category_and_score_omitted_gracefully() -> None:
    """category·significance_score만 없는 경우(statement는 있음) — 부재 필드만 빠진다."""
    entry = actions.EvidenceEntry(
        evidence_id="FIN_X", category="", statement="문장입니다.", significance_score=None
    )

    label = actions.evidence_label(entry)

    assert label == "문장입니다. (유의도 - · FIN_X)"
    assert "[" not in label
