"""JSON 강제 호출 — 추출·파싱·검증 재시도 루프 (명세 docs/specs/W3a-llm-evidence.md §2.3).

LLM 응답에서 코드펜스·잡담을 걷어내고(:func:`extract_json`) JSON으로
파싱·검증한다(:func:`complete_validated`). 파싱·검증까지 실패하면 오류
요지를 덧붙인 재요청 프롬프트로 최대 ``max_attempts``회 재시도한다.

**재시도 범위(명세 §2.3 해석)**: ``configs/llm.yaml``의 ``max_attempts``
주석은 "JSON 파싱·검증 실패 재시도 상한"이다. 즉 이 루프가 감싸는 것은
"LLM이 응답은 했지만 유효한 JSON이 아니었다"는 상황이다. ``client.
complete_text`` 호출 자체가 예외(인증·요금·전송 오류 등, ``is_error``)를
던지면 재시도 대상이 아니라 즉시 전파한다 — 코드펜스를 걷어내라는 재요청
프롬프트를 덧붙여봐야 인증·요금 문제는 해결되지 않기 때문이다.
"""

from __future__ import annotations

import json
from collections.abc import Callable

from research_backtest.core.exceptions import DataValidationError
from research_backtest.core.llm.client import LlmCallMetadata, LlmTextClient

_RETRY_SUFFIX_TEMPLATE = "\n\n이전 응답의 문제: {error}. 코드펜스·설명 없이 유효한 JSON만 출력하라."


def extract_json(text: str) -> str:
    """LLM 응답 텍스트에서 JSON 후보 문자열을 추출한다 (명세 §2.3, 결정적·순수 함수).

    우선순위:

    1. ```json ... ``` 또는 ``` ... ``` 코드펜스 내부(언어 태그 유무 무관).
    2. 코드펜스가 없으면 ``{``/``[`` 중 텍스트에서 **먼저 등장하는** 문자를
       최상위 JSON 값의 시작으로 보고, 그에 대응하는 마지막 닫는 문자
       (``}``/``]``)까지 슬라이스한다 — 배열(``[...]``)과 객체(``{...}``)를
       모두 지원하되, 설명문에 등장할 수 있는 반대쪽 괄호에 슬라이스가
       끊기지 않게 한다.
    3. 그래도 없으면 원문을 그대로(공백만 정리) 반환한다 — 호출자
       (:func:`complete_validated`)가 ``json.loads`` 실패로 재시도를
       판단한다.
    """
    fenced = _extract_fenced_block(text)
    if fenced is not None:
        return fenced.strip()

    sliced = _extract_bracket_slice(text)
    if sliced is not None:
        return sliced.strip()

    return text.strip()


def _extract_fenced_block(text: str) -> str | None:
    """첫 번째 ``` 코드펜스의 내부 텍스트를 추출한다 (언어 태그 라인은 건너뜀)."""
    fence = "```"
    start = text.find(fence)
    if start == -1:
        return None
    after_open = start + len(fence)
    newline = text.find("\n", after_open)
    if newline == -1:
        return None
    end = text.find(fence, newline)
    if end == -1:
        return None
    return text[newline + 1 : end]


def _extract_bracket_slice(text: str) -> str | None:
    """텍스트에서 먼저 등장하는 여는 괄호 종류를 기준으로 슬라이스한다."""
    brace_start = text.find("{")
    bracket_start = text.find("[")

    if brace_start == -1 and bracket_start == -1:
        return None

    if bracket_start != -1 and (brace_start == -1 or bracket_start < brace_start):
        end = text.rfind("]")
        return text[bracket_start : end + 1] if end > bracket_start else None

    end = text.rfind("}")
    return text[brace_start : end + 1] if end > brace_start else None


def complete_validated[T](
    client: LlmTextClient,
    *,
    system_prompt: str,
    user_prompt: str,
    validator: Callable[[object], T],
    max_attempts: int,
) -> tuple[T, LlmCallMetadata]:
    """LLM 호출 → JSON 추출·파싱·검증, 실패 시 재시도 (명세 §2.3).

    ``validator``는 파싱된 JSON 페이로드(``object``)를 받아 원하는 타입
    ``T``로 검증·변환한다(예: ``SomeModel.model_validate``, 리스트 검증
    함수). ``json.loads`` 또는 ``validator``가 실패하면 오류 요지를 덧붙인
    재요청 프롬프트로 재시도하고, ``max_attempts`` 소진 시 마지막 오류를
    포함한 :class:`DataValidationError`를 던진다.

    반환하는 :class:`LlmCallMetadata`는 **누적값**이다 — ``num_attempts``는
    성공까지 걸린 실제 시도 수, ``duration_ms``는 각 시도의 합, 토큰·비용은
    관측 가능한 시도분만 합산한다(``None``만 있으면 ``None`` 유지).
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts는 1 이상이어야 합니다: {max_attempts}")

    current_user_prompt = user_prompt
    last_error = "알 수 없는 오류"
    total_duration_ms = 0
    total_input_tokens: int | None = None
    total_output_tokens: int | None = None
    total_cost: float | None = None
    last_model = ""

    for attempt in range(1, max_attempts + 1):
        text, metadata = client.complete_text(
            system_prompt=system_prompt, user_prompt=current_user_prompt
        )
        total_duration_ms += metadata.duration_ms
        total_input_tokens = _accumulate_int(total_input_tokens, metadata.input_tokens)
        total_output_tokens = _accumulate_int(total_output_tokens, metadata.output_tokens)
        total_cost = _accumulate_float(total_cost, metadata.cost_usd_notional)
        last_model = metadata.model

        try:
            payload = json.loads(extract_json(text))
        except json.JSONDecodeError as err:
            last_error = f"JSON 파싱 실패: {err}"
            current_user_prompt = user_prompt + _RETRY_SUFFIX_TEMPLATE.format(error=last_error)
            continue

        try:
            # validator가 어떤 예외를 던질지 알 수 없어(pydantic ValidationError 등)
            # 의도적으로 넓게 잡는다 — 재시도 판단을 위한 경계(명세 §2.3).
            validated = validator(payload)
        except Exception as err:
            last_error = f"검증 실패: {err}"
            current_user_prompt = user_prompt + _RETRY_SUFFIX_TEMPLATE.format(error=last_error)
            continue

        accumulated = LlmCallMetadata(
            model=last_model,
            num_attempts=attempt,
            duration_ms=total_duration_ms,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            cost_usd_notional=total_cost,
        )
        return validated, accumulated

    raise DataValidationError(
        f"LLM 응답이 {max_attempts}회 시도 후에도 유효한 JSON으로 검증되지 않았습니다: {last_error}"
    )


def _accumulate_int(total: int | None, value: int | None) -> int | None:
    if value is None:
        return total
    return (total or 0) + value


def _accumulate_float(total: float | None, value: float | None) -> float | None:
    if value is None:
        return total
    return (total or 0.0) + value
