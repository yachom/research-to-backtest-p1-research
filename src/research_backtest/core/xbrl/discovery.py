"""extracted/에서 XBRL instance 문서를 탐색한다 (README §8.1, §19.5, 명세 §2.1).

ZIP 내부 파일명·구성은 제출마다 다르므로 고정 가정하지 않는다(README §8.1).
instance 판별은 확장자가 아니라 **루트 요소가 ``{xbrli}xbrl``인지**로 한다 —
확장자 힌트(.xbrl 우선)는 정렬에만 쓰고 판별에 의존하지 않는다.

실측(SK하이닉스 사업보고서): ZIP 1개에 instance 1개(``entity{corp}_{date}.xbrl``)
+ 스키마(.xsd) + 링크베이스(_def/_cal/_pre/_lab-ko/_lab-en.xml). 링크베이스·스키마의
루트는 각각 ``{link}linkbase``·``{xsd}schema``이므로 자연히 제외된다. 다만 연결/별도
분리 제출 등으로 복수 instance가 나올 수 있어 전부(정렬 후) 반환한다.
"""

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger("r2b.xbrl.discovery")

XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRL_ROOT_TAG = f"{{{XBRLI_NS}}}xbrl"
XBRL_EXTENSION = ".xbrl"


def find_instance_documents(extracted_dir: Path) -> list[Path]:
    """extracted/를 재귀 탐색해 instance 문서 경로를 결정적 순서로 반환한다.

    정렬: (.xbrl 확장자 우선) → extracted_dir 기준 상대경로 문자열 오름차순.
    이 순서가 파싱·저장의 결정성(README M4)을 뒷받침한다.
    """
    if not extracted_dir.is_dir():
        return []
    instances: list[Path] = []
    for path in sorted(extracted_dir.rglob("*")):
        if path.is_file() and _is_instance_document(path):
            instances.append(path)
    instances.sort(
        key=lambda p: (p.suffix.lower() != XBRL_EXTENSION, p.relative_to(extracted_dir).as_posix())
    )
    logger.debug("instance 문서 %d개 탐색: %s", len(instances), [p.name for p in instances])
    return instances


def _is_instance_document(path: Path) -> bool:
    """루트 요소가 ``{xbrli}xbrl``이면 instance로 판별한다.

    첫 start 이벤트만 읽고 중단해 대용량 파일(수 MB)도 저렴하게 판별한다.
    XML이 아니거나 파싱 실패면 instance가 아니다(False).
    """
    try:
        for _event, element in ET.iterparse(path, events=("start",)):
            return bool(element.tag == XBRL_ROOT_TAG)
    except ET.ParseError:
        return False
    except OSError:
        return False
    return False
