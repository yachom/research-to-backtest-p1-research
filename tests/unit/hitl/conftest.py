"""tests/unit/hitl 공용 픽스처 — 전부 오프라인(LLM·API·실 outputs/ 접근 없음)."""

import json
from pathlib import Path
from typing import Any, cast

import pytest

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "hitl"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURE_DIR


@pytest.fixture
def evidence_manifest_path() -> Path:
    return FIXTURE_DIR / "evidence_manifest.json"


def _load(name: str) -> Any:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture
def analyst_view_valid_payload() -> dict[str, Any]:
    return cast(dict[str, Any], _load("analyst_view_valid.json"))


@pytest.fixture
def analyst_view_violations() -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], _load("analyst_view_violations.json"))


@pytest.fixture
def hypothesis_valid_payload() -> dict[str, Any]:
    return cast(dict[str, Any], _load("hypothesis_valid.json"))


@pytest.fixture
def hypothesis_violations() -> dict[str, dict[str, Any]]:
    return cast(dict[str, dict[str, Any]], _load("hypothesis_violations.json"))
