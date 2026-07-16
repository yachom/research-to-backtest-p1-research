"""프롬프트 버전 로더 (명세 docs/specs/W3a-llm-evidence.md §2.4, docs/HUMAN_IN_THE_LOOP.md §5).

프롬프트는 ``{name}_v{version}.txt`` 파일로 버전 관리된다(과제 2 증빙,
docs/HUMAN_IN_THE_LOOP.md §5: ``{candidate_analysis,hypothesis_candidate}_v1.txt``
등). 실제 프롬프트 파일은 Wave 3b(C1'·C2')가 작성한다 — 이 모듈은
로더·렌더러만 제공하고, L1 테스트는 ``tmp_path``에 픽스처 파일을 만들어
검증한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from research_backtest.core.exceptions import ConfigError, DataValidationError


class _TrackingMapping(dict[str, object]):
    """``str.format_map``에서 실제로 조회된 키를 추적하는 dict (명세 §2.4).

    잉여 kwargs 검출용 — ``str.format``/``format_map``은 사용되지 않는 키를
    무시하므로, 이 클래스가 실제 조회를 가로채 기록한다.
    """

    def __init__(self, values: dict[str, object]) -> None:
        super().__init__(values)
        self.used_keys: set[str] = set()

    def __getitem__(self, key: str) -> object:
        value = super().__getitem__(key)
        self.used_keys.add(key)
        return value


@dataclass(frozen=True)
class PromptTemplate:
    """버전 관리되는 프롬프트 템플릿 (명세 §2.4).

    ``render(**kwargs)``는 ``str.format`` 기반이다:

    - 템플릿이 요구하는 변수가 kwargs에 없으면 ``KeyError``를
      :class:`DataValidationError`로 변환한다(프롬프트-코드 불일치 조기 발견).
    - kwargs에는 있지만 템플릿이 쓰지 않는 잉여 인자도 오류로 취급한다(오타로
      인한 무음 무시 방지).
    """

    name: str
    version: int
    text: str

    @property
    def prompt_version(self) -> str:
        """AIUsageRecord.prompt_version에 넣는 문자열 표현 (docs/OUTPUT_SCHEMA.md §7)."""
        return f"v{self.version}"

    def render(self, **kwargs: object) -> str:
        mapping = _TrackingMapping(kwargs)
        try:
            rendered = self.text.format_map(mapping)
        except KeyError as err:
            missing = err.args[0] if err.args else "?"
            raise DataValidationError(
                f"프롬프트 '{self.name}_v{self.version}' 렌더링에 필요한 변수 "
                f"'{missing}'가 누락되었습니다 (제공된 인자: {sorted(kwargs)})."
            ) from err

        unused = set(kwargs) - mapping.used_keys
        if unused:
            raise DataValidationError(
                f"프롬프트 '{self.name}_v{self.version}' 렌더링에 사용되지 않은 "
                f"잉여 인자: {sorted(unused)}"
            )
        return rendered


def load_prompt(dir_path: Path, name: str, version: int) -> PromptTemplate:
    """``{dir_path}/{name}_v{version}.txt``를 읽어 :class:`PromptTemplate`을 만든다.

    파일이 없으면 경로를 포함한 :class:`ConfigError`(설정·배치 문제로 취급).
    """
    path = dir_path / f"{name}_v{version}.txt"
    if not path.exists():
        raise ConfigError(f"프롬프트 파일이 없습니다: {path}")
    text = path.read_text(encoding="utf-8")
    return PromptTemplate(name=name, version=version, text=text)
