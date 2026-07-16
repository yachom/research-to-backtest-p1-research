"""instance 탐색 단위 테스트 (README §8.1 파일명 비고정, 명세 §2.1) — 오프라인."""

from pathlib import Path

from research_backtest.core.xbrl.discovery import find_instance_documents

# 루트가 instance가 아닌 문서(스키마·링크베이스) — 탐색에서 제외되어야 한다
SCHEMA_XML = b'<?xml version="1.0"?><xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"/>'
LINKBASE_XML = (
    b'<?xml version="1.0"?><link:linkbase xmlns:link="http://www.xbrl.org/2003/linkbase"/>'
)
NOT_XML = b"this is not xml at all \x00\x01"


def test_finds_instance_with_nonfixed_filename(
    standard_instance_bytes: bytes, tmp_path: Path
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    # 고정 파일명 가정 금지 — 임의 이름·.xml 확장자여도 루트 태그로 판별
    (extracted / "arbitrary_report_name.xbrl").write_bytes(standard_instance_bytes)

    found = find_instance_documents(extracted)
    assert [p.name for p in found] == ["arbitrary_report_name.xbrl"]


def test_detects_instance_even_with_xml_extension(
    standard_instance_bytes: bytes, tmp_path: Path
) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    # .xbrl가 아니라 .xml이어도 instance면 탐지한다 (확장자에 의존하지 않음)
    (extracted / "instance_doc.xml").write_bytes(standard_instance_bytes)

    found = find_instance_documents(extracted)
    assert [p.name for p in found] == ["instance_doc.xml"]


def test_ignores_non_instance_documents(standard_instance_bytes: bytes, tmp_path: Path) -> None:
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    (extracted / "entity_2024-12-31.xbrl").write_bytes(standard_instance_bytes)
    (extracted / "entity_2024-12-31.xsd").write_bytes(SCHEMA_XML)
    (extracted / "entity_2024-12-31_pre.xml").write_bytes(LINKBASE_XML)
    (extracted / "entity_2024-12-31_lab-ko.xml").write_bytes(LINKBASE_XML)
    (extracted / "readme.txt").write_bytes(NOT_XML)

    found = find_instance_documents(extracted)
    # 스키마·링크베이스·비XML은 제외, instance 1개만
    assert [p.name for p in found] == ["entity_2024-12-31.xbrl"]


def test_multiple_instances_all_found_and_sorted(
    standard_instance_bytes: bytes, altprefix_instance_bytes: bytes, tmp_path: Path
) -> None:
    extracted = tmp_path / "extracted"
    (extracted / "nested").mkdir(parents=True)
    # 복수 instance(연결/별도 분리 등) — 전부 찾고 (.xbrl 우선 → 상대경로) 정렬
    (extracted / "b_second.xbrl").write_bytes(altprefix_instance_bytes)
    (extracted / "a_first.xbrl").write_bytes(standard_instance_bytes)
    (extracted / "nested" / "c_third.xml").write_bytes(altprefix_instance_bytes)

    found = find_instance_documents(extracted)
    # .xbrl 확장자 우선(a_first, b_second) 후 .xml(nested/c_third)
    assert [p.relative_to(extracted).as_posix() for p in found] == [
        "a_first.xbrl",
        "b_second.xbrl",
        "nested/c_third.xml",
    ]


def test_empty_or_missing_dir_returns_empty(tmp_path: Path) -> None:
    assert find_instance_documents(tmp_path / "does_not_exist") == []
    empty = tmp_path / "empty"
    empty.mkdir()
    assert find_instance_documents(empty) == []
