"""RunManifest 모델·저장소 테스트 (docs/specs/CLI-integration.md §5.0).

RunManifest는 실행 1건의 불변 식별 메타(run_id·기업 식별 정보·기준일 등)이며,
RunState(진행 상태·전이 이력)와 역할이 분리되어 있다 — 이 테스트는 필드
왕복(round-trip)·extra 거부·RunStore를 통한 save/load·미존재 안내 메시지를
확인한다.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.hitl.models import RunManifest
from research_backtest.core.hitl.store import RunStore


def _manifest(**overrides: object) -> RunManifest:
    payload: dict[str, object] = {
        "run_id": "20260715_090000_SKHYNIX",
        "company_query": "SK하이닉스",
        "corp_code": "00164779",
        "corp_name": "SK하이닉스",
        "corp_eng_name": "SK hynix Inc.",
        "stock_code": "000660",
        "as_of_date": "2025-12-31",
        "created_at": "2026-07-15T09:00:00+09:00",
        "code_version": "abc1234",
    }
    payload.update(overrides)
    return RunManifest.model_validate(payload)


# ---------------------------------------------------------------------------
# 필드 왕복·검증
# ---------------------------------------------------------------------------


def test_run_manifest_round_trips_all_fields() -> None:
    manifest = _manifest()
    restored = RunManifest.model_validate_json(manifest.model_dump_json())
    assert restored == manifest
    assert restored.run_id == "20260715_090000_SKHYNIX"
    assert restored.company_query == "SK하이닉스"
    assert restored.corp_code == "00164779"
    assert restored.corp_name == "SK하이닉스"
    assert restored.corp_eng_name == "SK hynix Inc."
    assert restored.stock_code == "000660"
    assert restored.as_of_date == "2025-12-31"
    assert restored.created_at == "2026-07-15T09:00:00+09:00"
    assert restored.code_version == "abc1234"


def test_run_manifest_optional_fields_default_to_none() -> None:
    manifest = _manifest(corp_eng_name=None, code_version=None)
    assert manifest.corp_eng_name is None
    assert manifest.code_version is None


def test_run_manifest_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        _manifest(unexpected_field="not allowed")


def test_run_manifest_rejects_missing_required_field() -> None:
    payload = _manifest().model_dump()
    del payload["stock_code"]
    with pytest.raises(ValidationError):
        RunManifest.model_validate(payload)


# ---------------------------------------------------------------------------
# RunStore를 통한 save/load 왕복 (§5.0 — 기존 _write_json/_read_json 패턴)
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> RunStore:
    return RunStore(tmp_path / "outputs", "20260715_090000_SKHYNIX")


def test_save_run_manifest_writes_run_manifest_json(store: RunStore) -> None:
    manifest = _manifest()
    path = store.save_run_manifest(manifest)
    assert path == store.run_dir / "run_manifest.json"
    assert path.exists()


def test_load_run_manifest_round_trip(store: RunStore) -> None:
    manifest = _manifest()
    store.save_run_manifest(manifest)
    assert store.load_run_manifest() == manifest


def test_saved_run_manifest_is_pretty_printed_with_indent(store: RunStore) -> None:
    store.save_run_manifest(_manifest())
    raw = (store.run_dir / "run_manifest.json").read_text(encoding="utf-8")
    assert "\n  " in raw  # indent=2 (기존 _write_json 패턴)


def test_missing_run_manifest_raises_data_validation_error_with_guidance(
    store: RunStore,
) -> None:
    with pytest.raises(DataValidationError, match="create-run"):
        store.load_run_manifest()


def test_missing_run_manifest_is_not_a_bare_file_not_found_error(store: RunStore) -> None:
    with pytest.raises(DataValidationError):
        try:
            store.load_run_manifest()
        except FileNotFoundError:
            pytest.fail("FileNotFoundError가 노출되면 안 된다 — DataValidationError여야 한다")


def test_run_manifest_and_run_state_are_separate_files(store: RunStore) -> None:
    """RunManifest(불변 메타)와 RunState(진행 상태)는 역할·파일이 분리된다 (§5.0)."""
    from research_backtest.core.hitl.states import create_run_state

    store.save_run_manifest(_manifest())
    run_state = create_run_state(
        "20260715_090000_SKHYNIX", "SK하이닉스", "2025-12-31", actor="user"
    )
    store.save_run_state(run_state)

    manifest_path = store.run_dir / "run_manifest.json"
    state_path = store.run_dir / "run_state.json"
    assert manifest_path != state_path
    assert manifest_path.exists()
    assert state_path.exists()
