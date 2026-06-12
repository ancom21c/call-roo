from __future__ import annotations

import json
import os
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from callroo_printer.config import LLMModelConfig, LLMProfileConfig
from callroo_printer.web_search import WebSearchClient


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
    model_name: str | None = None
    attempts: tuple[dict[str, str], ...] = ()


class OpenAICompatClient:
    def __init__(self, config: LLMProfileConfig):
        self.config = config
        self._web_search: WebSearchClient | None = None
        web_cfg = getattr(config, "web_search", None)
        if web_cfg is not None and web_cfg.enabled and (
            web_cfg.signs or web_cfg.tool_calling_enabled
        ):
            self._web_search = WebSearchClient(web_cfg)

    def prefetch_web_search(self, *, date_key: str | None = None) -> dict[str, object]:
        web_cfg = getattr(self.config, "web_search", None)
        if self._web_search is None or web_cfg is None or not web_cfg.signs:
            return {"ok": False, "profile": self.config.name, "count": 0}
        if date_key is None:
            date_key = datetime.now().astimezone().strftime("%Y-%m-%d")

        fetched: list[dict[str, object]] = []
        for sign in web_cfg.signs:
            query, snippets = self._web_search.fetch_snippets(
                sign=sign,
                date_key=date_key,
            )
            fetched.append(
                {
                    "sign": sign,
                    "query": query,
                    "snippet_count": len(snippets),
                }
            )
        return {
            "ok": True,
            "profile": self.config.name,
            "date_key": date_key,
            "count": len(fetched),
            "items": fetched,
        }

    def generate_fortune(
        self,
        max_chars: int,
        *,
        current_time_hint: str | None = None,
        recent_fortunes: tuple[str, ...] = (),
        allowed_tags: tuple[str, ...] = (),
    ) -> LLMCallResult:
        web_sign: str | None = None
        web_snippets: tuple[str, ...] = ()
        if self._web_search is not None:
            web_cfg = getattr(self.config, "web_search", None)
            if web_cfg is None or not web_cfg.signs:
                web_cfg = None
            else:
                web_sign = random.choice(web_cfg.signs)
                date_key = datetime.now().astimezone().strftime("%Y-%m-%d")
                _query, web_snippets = self._web_search.fetch_snippets(
                    sign=web_sign, date_key=date_key
                )
        web_cfg = getattr(self.config, "web_search", None) if web_sign else None
        user_prompt = _compose_user_prompt(
            self.config.prompt,
            current_time_hint=current_time_hint,
            recent_fortunes=recent_fortunes,
            allowed_tags=allowed_tags,
            response_tag_key=self.config.response_tag_key,
            current_time_hint_pre=self.config.current_time_hint_pre,
            current_time_hint_post=self.config.current_time_hint_post,
            cleaned_examples_pre=self.config.cleaned_examples_pre,
            cleaned_examples_post=self.config.cleaned_examples_post,
            web_sign=web_sign,
            web_snippets=web_snippets,
            web_context_pre=(web_cfg.context_pre if web_cfg else ""),
            web_context_post=(web_cfg.context_post if web_cfg else ""),
            web_sign_directive=(web_cfg.sign_directive if web_cfg else ""),
        )
        attempts: list[dict[str, str]] = []
        last_result: LLMCallResult | None = None
        for model_config in self._model_configs():
            result = self._generate_with_model(
                model_config,
                user_prompt=user_prompt,
                max_chars=max_chars,
                allowed_tags=allowed_tags,
                previous_attempts=tuple(attempts),
            )
            attempt = {
                "name": model_config.name,
                "endpoint": model_config.endpoint,
                "model": model_config.model,
                "url": result.request_url,
                "status": "error" if result.error else "ok",
            }
            if result.error:
                attempt["error"] = result.error
            attempts.append(attempt)
            result = _with_attempts(result, tuple(attempts))
            if result.error is None:
                return result
            last_result = result

        if last_result is not None:
            return _with_attempts(last_result, tuple(attempts))
        raise RuntimeError("No LLM model candidates configured.")

    def _generate_with_model(
        self,
        model_config: LLMModelConfig,
        *,
        user_prompt: str,
        max_chars: int,
        allowed_tags: tuple[str, ...] = (),
        previous_attempts: tuple[dict[str, str], ...] = (),
    ) -> LLMCallResult:
        request_url = self._chat_completions_url(model_config)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        tools = self._web_search_tools()
        max_tool_rounds = self._web_search_tool_max_rounds() if tools else 0
        tool_rounds = 0
        payload = self._build_chat_payload(
            model_config,
            messages=messages,
            tools=tools,
        )
        body: dict[str, object] | None = None

        while True:
            try:
                body = self._post_chat_completion(
                    request_url,
                    payload,
                    model_config=model_config,
                )
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
                    model_name=model_config.name,
                    attempts=previous_attempts,
                )
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                return LLMCallResult(
                    request_url=request_url,
                    request_payload=payload,
                    response_payload=None,
                    raw_text=None,
                    parsed_json=None,
                    text="",
                    tag=None,
                    error=f"Invalid LLM response JSON: {exc}",
                    model_name=model_config.name,
                    attempts=previous_attempts,
                )

            message = _extract_response_message(body)
            if message is None:
                return LLMCallResult(
                    request_url=request_url,
                    request_payload=payload,
                    response_payload=body,
                    raw_text=None,
                    parsed_json=None,
                    text="",
                    tag=None,
                    error="Unexpected LLM response: choices[0].message missing.",
                    model_name=model_config.name,
                    attempts=previous_attempts,
                )

            tool_calls = self._supported_tool_calls(message)
            if not tool_calls:
                content = message.get("content")
                break

            if tool_rounds >= max_tool_rounds:
                return LLMCallResult(
                    request_url=request_url,
                    request_payload=payload,
                    response_payload=body,
                    raw_text=None,
                    parsed_json=None,
                    text="",
                    tag=None,
                    error="LLM exceeded web search tool call limit.",
                    model_name=model_config.name,
                    attempts=previous_attempts,
                )

            messages.append(_assistant_tool_call_message(message, tool_calls))
            messages.extend(self._execute_tool_calls(tool_calls))
            tool_rounds += 1
            payload = self._build_chat_payload(
                model_config,
                messages=messages,
                tools=tools,
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
                model_name=model_config.name,
                attempts=previous_attempts,
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
                model_name=model_config.name,
                attempts=previous_attempts,
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
            model_name=model_config.name,
            attempts=previous_attempts,
        )

    def _build_chat_payload(
        self,
        model_config: LLMModelConfig,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": model_config.model,
            "temperature": model_config.temperature,
            "max_tokens": model_config.max_tokens,
            "messages": messages,
            "chat_template_kwargs": {
                "enable_thinking": model_config.enable_thinking,
            },
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    def _post_chat_completion(
        self,
        request_url: str,
        payload: dict[str, object],
        *,
        model_config: LLMModelConfig,
    ) -> dict[str, object]:
        request = urllib.request.Request(
            request_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=self._headers(model_config),
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=model_config.timeout_seconds
        ) as response:
            body = json.loads(response.read().decode("utf-8"))
        if isinstance(body, dict):
            return body
        raise json.JSONDecodeError("LLM response root is not an object", "", 0)

    def _web_search_tools(self) -> list[dict[str, Any]]:
        web_cfg = getattr(self.config, "web_search", None)
        if (
            self._web_search is None
            or web_cfg is None
            or not web_cfg.enabled
            or not web_cfg.tool_calling_enabled
        ):
            return []

        return [
            {
                "type": "function",
                "function": {
                    "name": _normalize_tool_name(web_cfg.tool_name),
                    "description": web_cfg.tool_description,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search query to send to the web search API.",
                            },
                            "count": {
                                "type": "integer",
                                "description": "Number of web results to return. Defaults to the configured count.",
                                "minimum": 1,
                                "maximum": 12,
                            },
                        },
                        "required": ["query"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def _web_search_tool_max_rounds(self) -> int:
        web_cfg = getattr(self.config, "web_search", None)
        if web_cfg is None:
            return 0
        return max(1, int(web_cfg.tool_max_rounds))

    def _supported_tool_calls(
        self,
        message: dict[str, Any],
    ) -> list[dict[str, Any]]:
        web_cfg = getattr(self.config, "web_search", None)
        if web_cfg is None:
            return []
        expected_name = _normalize_tool_name(web_cfg.tool_name)
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            return []
        supported: list[dict[str, Any]] = []
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            if function.get("name") == expected_name:
                supported.append(tool_call)
        return supported

    def _execute_tool_calls(
        self,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        web_cfg = getattr(self.config, "web_search", None)
        if self._web_search is None or web_cfg is None:
            return []
        tool_name = _normalize_tool_name(web_cfg.tool_name)
        tool_messages: list[dict[str, Any]] = []
        for index, tool_call in enumerate(tool_calls, start=1):
            call_id = str(tool_call.get("id") or f"{tool_name}-{index}")
            payload = self._execute_web_search_tool(tool_call)
            tool_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": tool_name,
                    "content": json.dumps(payload, ensure_ascii=False),
                }
            )
        return tool_messages

    def _execute_web_search_tool(self, tool_call: dict[str, Any]) -> dict[str, Any]:
        web_cfg = getattr(self.config, "web_search", None)
        if web_cfg is None:
            return {"error": "web_search is not configured.", "results": []}
        function = tool_call.get("function")
        raw_arguments = function.get("arguments") if isinstance(function, dict) else "{}"
        try:
            arguments = _parse_tool_arguments(raw_arguments)
        except ValueError as exc:
            return {"error": f"Invalid web_search arguments: {exc}", "results": []}

        query = str(arguments.get("query", "")).strip()
        if not query:
            return {"error": "web_search query is required.", "results": []}
        raw_count = arguments.get("count", web_cfg.count)
        try:
            count = min(12, max(1, int(raw_count)))
        except (TypeError, ValueError):
            count = min(12, max(1, int(web_cfg.count)))

        assert self._web_search is not None
        results = self._web_search.search(query, count=count)
        return {
            "query": query,
            "provider": web_cfg.provider,
            "results": [result.as_tool_payload() for result in results],
        }

    def _model_configs(self) -> tuple[LLMModelConfig, ...]:
        models = getattr(self.config, "models", ())
        if models:
            return tuple(models)
        return (
            LLMModelConfig(
                name=str(getattr(self.config, "model", "model")),
                endpoint=str(getattr(self.config, "endpoint", "")),
                model=str(getattr(self.config, "model", "")),
                enable_thinking=bool(getattr(self.config, "enable_thinking", False)),
                api_key=getattr(self.config, "api_key", None),
                api_key_env=getattr(self.config, "api_key_env", None),
                temperature=float(getattr(self.config, "temperature", 0.9)),
                max_tokens=int(getattr(self.config, "max_tokens", 120)),
                timeout_seconds=float(getattr(self.config, "timeout_seconds", 25.0)),
            ),
        )

    @staticmethod
    def _chat_completions_url(model_config: LLMModelConfig) -> str:
        endpoint = model_config.endpoint.strip()
        if endpoint.endswith("/chat/completions"):
            return endpoint
        return urllib.parse.urljoin(endpoint.rstrip("/") + "/", "chat/completions")

    def _headers(self, model_config: LLMModelConfig | None = None) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        source = model_config or self.config
        api_key = getattr(source, "api_key", None)
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        else:
            api_key_env = getattr(source, "api_key_env", None)
            if api_key_env:
                env_api_key = os.getenv(api_key_env)
                if env_api_key:
                    headers["Authorization"] = f"Bearer {env_api_key}"
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


def _with_attempts(
    result: LLMCallResult,
    attempts: tuple[dict[str, str], ...],
) -> LLMCallResult:
    return LLMCallResult(
        request_url=result.request_url,
        request_payload=result.request_payload,
        response_payload=result.response_payload,
        raw_text=result.raw_text,
        parsed_json=result.parsed_json,
        text=result.text,
        tag=result.tag,
        error=result.error,
        model_name=result.model_name,
        attempts=attempts,
    )


def _extract_response_message(body: dict[str, object]) -> dict[str, Any] | None:
    try:
        message = body["choices"][0]["message"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(message, dict):
        return message
    return None


def _assistant_tool_call_message(
    message: dict[str, Any],
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    assistant_message: dict[str, Any] = {
        "role": "assistant",
        "content": message.get("content") or "",
        "tool_calls": [
            {
                "id": str(tool_call.get("id") or f"tool-call-{index}"),
                "type": str(tool_call.get("type") or "function"),
                "function": tool_call.get("function") or {},
            }
            for index, tool_call in enumerate(tool_calls, start=1)
        ],
    }
    return assistant_message


def _parse_tool_arguments(raw_arguments: object) -> dict[str, Any]:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if not isinstance(raw_arguments, str):
        raise ValueError("arguments must be a JSON object string")
    try:
        parsed = json.loads(raw_arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(parsed, dict):
        raise ValueError("arguments root must be an object")
    return parsed


def _normalize_tool_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "_", name.strip())
    if not cleaned:
        return "web_search"
    return cleaned[:64]


def _compose_user_prompt(
    base_prompt: str,
    *,
    current_time_hint: str | None = None,
    variation_hint: str | None = None,
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
    web_sign: str | None = None,
    web_snippets: tuple[str, ...] = (),
    web_context_pre: str = "",
    web_context_post: str = "",
    web_sign_directive: str = "",
) -> str:
    del variation_hint
    sections = [base_prompt.strip()]

    if current_time_hint:
        sections.append(
            _compose_hint_block(
                current_time_hint_pre,
                f"- {current_time_hint.strip()}",
                current_time_hint_post,
            )
        )

    if web_sign:
        web_lines: list[str] = []
        directive = (
            web_sign_directive.replace("{sign}", web_sign).strip()
            if web_sign_directive
            else ""
        )
        if directive:
            web_lines.append(directive)
        if web_snippets:
            pre = (
                web_context_pre.replace("{sign}", web_sign).strip()
                if web_context_pre
                else ""
            )
            body = "\n".join(f"- {snippet}" for snippet in web_snippets)
            post = (
                web_context_post.replace("{sign}", web_sign).strip()
                if web_context_post
                else ""
            )
            data_block = _compose_hint_block(pre, body, post)
            if data_block:
                web_lines.append(data_block)
        web_section = "\n\n".join(web_lines)
        if web_section:
            sections.append(web_section)

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
