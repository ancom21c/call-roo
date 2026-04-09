from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from callroo_printer.config import LLMProfileConfig


@dataclass(frozen=True)
class LLMCallResult:
    request_url: str
    request_payload: dict[str, object]
    response_payload: dict[str, object] | None
    raw_text: str | None
    parsed_json: dict[str, object] | None
    text: str
    tag: str | None
    error: str | None


class OpenAICompatClient:
    def __init__(self, config: LLMProfileConfig):
        self.config = config

    def generate_fortune(
        self,
        max_chars: int,
        *,
        current_time_hint: str | None = None,
        variation_hint: str | None = None,
        recent_fortunes: tuple[str, ...] = (),
        allowed_tags: tuple[str, ...] = (),
    ) -> LLMCallResult:
        request_url = self._chat_completions_url()
        user_prompt = _compose_user_prompt(
            self.config.prompt,
            current_time_hint=current_time_hint,
            variation_hint=variation_hint,
            recent_fortunes=recent_fortunes,
            allowed_tags=allowed_tags,
            response_tag_key=self.config.response_tag_key,
            current_time_hint_pre=self.config.current_time_hint_pre,
            current_time_hint_post=self.config.current_time_hint_post,
            cleaned_examples_pre=self.config.cleaned_examples_pre,
            cleaned_examples_post=self.config.cleaned_examples_post,
        )
        payload: dict[str, object] = {
            "model": self.config.model,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "messages": [
                {"role": "system", "content": self.config.system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "chat_template_kwargs": {
                "enable_thinking": self.config.enable_thinking,
            },
        }

        request = urllib.request.Request(
            request_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            return LLMCallResult(
                request_url=request_url,
                request_payload=payload,
                response_payload=None,
                raw_text=None,
                parsed_json=None,
                text="",
                tag=None,
                error=f"LLM request failed: {exc}",
            )

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            return LLMCallResult(
                request_url=request_url,
                request_payload=payload,
                response_payload=body,
                raw_text=None,
                parsed_json=None,
                text="",
                tag=None,
                error=f"Unexpected LLM response: {exc}",
            )

        raw_text = _flatten_content(content)
        parsed_json = extract_json_object(raw_text)
        if parsed_json is None:
            return LLMCallResult(
                request_url=request_url,
                request_payload=payload,
                response_payload=body,
                raw_text=raw_text,
                parsed_json=None,
                text="",
                tag=None,
                error="LLM response did not contain a valid JSON object.",
            )

        extracted_text = parsed_json.get(self.config.response_json_key)
        if not isinstance(extracted_text, str):
            return LLMCallResult(
                request_url=request_url,
                request_payload=payload,
                response_payload=body,
                raw_text=raw_text,
                parsed_json=parsed_json,
                text="",
                tag=None,
                error=(
                    f"LLM JSON response did not contain string field "
                    f"{self.config.response_json_key!r}."
                ),
            )

        return LLMCallResult(
            request_url=request_url,
            request_payload=payload,
            response_payload=body,
            raw_text=raw_text,
            parsed_json=parsed_json,
            text=sanitize_text(extracted_text, max_chars=max_chars),
            tag=_extract_selected_tag(
                parsed_json,
                response_tag_key=self.config.response_tag_key,
                allowed_tags=allowed_tags,
            ),
            error=None,
        )

    def _chat_completions_url(self) -> str:
        endpoint = self.config.endpoint.strip()
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return urllib.parse.urljoin(endpoint.rstrip("/") + "/", "chat/completions")

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.config.api_key_env:
            api_key = os.getenv(self.config.api_key_env)
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        return headers


def sanitize_text(text: str, max_chars: int) -> str:
    normalized_text = (
        text.replace("\\r\\n", "\n")
        .replace("\\n", "\n")
        .replace("\\r", "\n")
    )
    raw_lines = normalized_text.splitlines()
    if raw_lines:
        normalized_lines = [
            re.sub(r"\s+", " ", line).strip() for line in raw_lines if line.strip()
        ]
        collapsed = "\n".join(normalized_lines).strip()
    else:
        collapsed = re.sub(r"\s+", " ", normalized_text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    if max_chars <= 1:
        return collapsed[:max_chars]
    return collapsed[: max_chars - 1].rstrip() + "…"


def _compose_user_prompt(
    base_prompt: str,
    *,
    current_time_hint: str | None = None,
    variation_hint: str | None,
    recent_fortunes: tuple[str, ...] = (),
    allowed_tags: tuple[str, ...] = (),
    response_tag_key: str = "tag",
    current_time_hint_pre: str = "이번 운세 기준 시각:",
    current_time_hint_post: str = (
        "위 시각의 공기와 타이밍을 반영하되, 문장을 숫자 나열처럼 쓰지는 마."
    ),
    cleaned_examples_pre: str = "최근 출력 예시와 겹치지 말 것:",
    cleaned_examples_post: str = (
        "위 예시와 첫 행 시작어, 핵심 명사, 계절어, 분위기, 결말 어미를 반복하지 마."
    ),
) -> str:
    sections = [base_prompt.strip()]

    if current_time_hint:
        sections.append(
            _compose_hint_block(
                current_time_hint_pre,
                f"- {current_time_hint.strip()}",
                current_time_hint_post,
            )
        )

    cleaned_examples = [
        " / ".join(line.strip() for line in fortune.splitlines() if line.strip())
        for fortune in recent_fortunes
        if fortune.strip()
    ]
    if cleaned_examples:
        recent_lines = "\n".join(
            f"{index}. {fortune}"
            for index, fortune in enumerate(cleaned_examples, start=1)
        )
        sections.append(
            _compose_hint_block(
                cleaned_examples_pre,
                recent_lines,
                cleaned_examples_post,
            )
        )

    if allowed_tags:
        tag_lines = "\n".join(f"- {tag}" for tag in allowed_tags)
        sections.append(
            "\n".join(
                (
                    "이번 출력 태그 후보:",
                    tag_lines,
                    f"반드시 위 후보 중 문구와 가장 어울리는 태그 하나를 골라 JSON의 {response_tag_key} 필드에 정확히 넣어.",
                )
            )
        )

    if variation_hint:
        sections.append(f"이번 출력 변주 지시:\n- {variation_hint.strip()}")

    return "\n\n".join(section for section in sections if section)


def _compose_hint_block(pre_hint: str, body: str, post_hint: str) -> str:
    parts = []
    for part in (pre_hint, body, post_hint):
        cleaned = part.strip()
        if cleaned:
            parts.append(cleaned)
    return "\n".join(parts)


def _flatten_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "".join(parts)
    return str(content)


def extract_json_object(text: str) -> dict[str, object] | None:
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def _extract_selected_tag(
    payload: dict[str, object],
    *,
    response_tag_key: str,
    allowed_tags: tuple[str, ...],
) -> str | None:
    raw_tag = payload.get(response_tag_key)
    if not isinstance(raw_tag, str):
        return None
    cleaned_tag = _normalize_tag(raw_tag)
    if not cleaned_tag:
        return None
    if not allowed_tags:
        return cleaned_tag

    normalized_allowed = {
        _normalize_tag(tag).casefold(): tag
        for tag in allowed_tags
        if _normalize_tag(tag)
    }
    return normalized_allowed.get(cleaned_tag.casefold())


def _normalize_tag(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
