from __future__ import annotations

import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from callroo_printer.llm_client import OpenAICompatClient, _compose_user_prompt


class LLMClientTest(unittest.TestCase):
    def test_compose_user_prompt_returns_base_prompt_without_hint(self) -> None:
        self.assertEqual(
            _compose_user_prompt("기본 프롬프트", current_time_hint=None, variation_hint=None),
            "기본 프롬프트",
        )

    def test_compose_user_prompt_ignores_variation_hint(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint=None,
            variation_hint="자판기 버튼, 짧은 리듬",
        )
        self.assertEqual(prompt, "기본 프롬프트")
        self.assertNotIn("이번 출력 변주 지시", prompt)
        self.assertNotIn("자판기 버튼", prompt)

    def test_compose_user_prompt_appends_current_time_hint(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint="2026-04-03 14:30:45 KST",
            variation_hint=None,
        )
        self.assertIn("이번 운세 기준 시각", prompt)
        self.assertIn("2026-04-03 14:30:45 KST", prompt)

    def test_compose_user_prompt_includes_recent_fortunes_to_avoid(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint=None,
            variation_hint=None,
            recent_fortunes=(
                "바람이 지나가\n새싹이 고개를 들고\n오늘은 희망의 날",
                "아침 이슬 맺히어\n가장 작은 꽃도 빛나네\n오늘은 작은 기적",
            ),
        )
        self.assertIn("최근 출력 예시와 겹치지 말 것", prompt)
        self.assertIn(
            "1. 바람이 지나가 / 새싹이 고개를 들고 / 오늘은 희망의 날",
            prompt,
        )
        self.assertIn(
            "2. 아침 이슬 맺히어 / 가장 작은 꽃도 빛나네 / 오늘은 작은 기적",
            prompt,
        )
        self.assertIn("결말 어미를 반복하지 마", prompt)

    def test_compose_user_prompt_uses_custom_hint_wrappers(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint="2026-04-08 16:30:00 KST",
            variation_hint=None,
            recent_fortunes=("첫 줄\n둘째 줄\n셋째 줄",),
            current_time_hint_pre="시각 메모:",
            current_time_hint_post="이 공기를 반영해.",
            cleaned_examples_pre="이전 예시:",
            cleaned_examples_post="이 톤은 피할 것.",
        )
        self.assertIn("시각 메모:", prompt)
        self.assertIn("- 2026-04-08 16:30:00 KST", prompt)
        self.assertIn("이 공기를 반영해.", prompt)
        self.assertIn("이전 예시:", prompt)
        self.assertIn("1. 첫 줄 / 둘째 줄 / 셋째 줄", prompt)
        self.assertIn("이 톤은 피할 것.", prompt)

    def test_compose_user_prompt_includes_allowed_tags(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint=None,
            variation_hint=None,
            allowed_tags=("형광등", "자판기", "주전자"),
            response_tag_key="selected_tag",
        )
        self.assertIn("이번 출력 태그 후보", prompt)
        self.assertIn("- 형광등", prompt)
        self.assertIn("- 자판기", prompt)
        self.assertIn("- 주전자", prompt)
        self.assertIn("JSON의 selected_tag 필드", prompt)

    def test_headers_use_config_api_key_before_env(self) -> None:
        client = OpenAICompatClient(
            SimpleNamespace(api_key="config-token", api_key_env="ENV_TOKEN")
        )

        with patch.dict("os.environ", {"ENV_TOKEN": "env-token"}):
            headers = client._headers()

        self.assertEqual(headers["Authorization"], "Bearer config-token")

    def test_headers_fall_back_to_api_key_env(self) -> None:
        client = OpenAICompatClient(
            SimpleNamespace(api_key=None, api_key_env="ENV_TOKEN")
        )

        with patch.dict("os.environ", {"ENV_TOKEN": "env-token"}):
            headers = client._headers()

        self.assertEqual(headers["Authorization"], "Bearer env-token")

    def test_generate_fortune_tries_next_model_after_failure(self) -> None:
        client = OpenAICompatClient(
            SimpleNamespace(
                prompt="운세를 JSON으로 써줘.",
                system_prompt="JSON 객체 하나만 반환한다.",
                response_tag_key="tag",
                response_json_key="fortune",
                current_time_hint_pre="시각:",
                current_time_hint_post="시각 반영.",
                cleaned_examples_pre="최근:",
                cleaned_examples_post="새 조합.",
                models=(
                    SimpleNamespace(
                        name="primary",
                        endpoint="https://primary.invalid/v1/",
                        model="primary-model",
                        enable_thinking=False,
                        api_key="primary-key",
                        api_key_env=None,
                        temperature=0.7,
                        max_tokens=80,
                        timeout_seconds=1.0,
                    ),
                    SimpleNamespace(
                        name="fallback",
                        endpoint="https://fallback.invalid/v1/",
                        model="fallback-model",
                        enable_thinking=False,
                        api_key="fallback-key",
                        api_key_env=None,
                        temperature=0.7,
                        max_tokens=80,
                        timeout_seconds=1.0,
                    ),
                ),
            )
        )
        response = _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"fortune": "대길: 테스트\nfallback 성공\n끝", "tag": "행운"},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=(urllib.error.URLError("down"), response),
        ) as urlopen:
            result = client.generate_fortune(max_chars=100, allowed_tags=("행운",))

        self.assertIsNone(result.error)
        self.assertEqual(result.model_name, "fallback")
        self.assertEqual(result.text, "대길: 테스트\nfallback 성공\n끝")
        self.assertEqual(result.tag, "행운")
        self.assertEqual([attempt["status"] for attempt in result.attempts], ["error", "ok"])
        self.assertEqual(urlopen.call_count, 2)

    def test_generate_fortune_tries_next_model_after_invalid_json_response(self) -> None:
        client = OpenAICompatClient(
            SimpleNamespace(
                prompt="운세를 JSON으로 써줘.",
                system_prompt="JSON 객체 하나만 반환한다.",
                response_tag_key="tag",
                response_json_key="fortune",
                current_time_hint_pre="시각:",
                current_time_hint_post="시각 반영.",
                cleaned_examples_pre="최근:",
                cleaned_examples_post="새 조합.",
                models=(
                    SimpleNamespace(
                        name="primary",
                        endpoint="https://primary.invalid/v1/",
                        model="primary-model",
                        enable_thinking=False,
                        api_key="primary-key",
                        api_key_env=None,
                        temperature=0.7,
                        max_tokens=80,
                        timeout_seconds=1.0,
                    ),
                    SimpleNamespace(
                        name="fallback",
                        endpoint="https://fallback.invalid/v1/",
                        model="fallback-model",
                        enable_thinking=False,
                        api_key="fallback-key",
                        api_key_env=None,
                        temperature=0.7,
                        max_tokens=80,
                        timeout_seconds=1.0,
                    ),
                ),
            )
        )
        response = _FakeHTTPResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"fortune": "복구됨\n두 번째 모델\n성공", "tag": "행운"},
                                ensure_ascii=False,
                            )
                        }
                    }
                ]
            }
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=(_FakeRawHTTPResponse(b"not-json"), response),
        ) as urlopen:
            result = client.generate_fortune(max_chars=100, allowed_tags=("행운",))

        self.assertIsNone(result.error)
        self.assertEqual(result.model_name, "fallback")
        self.assertEqual(result.text, "복구됨\n두 번째 모델\n성공")
        self.assertEqual([attempt["status"] for attempt in result.attempts], ["error", "ok"])
        self.assertIn("Invalid LLM response JSON", result.attempts[0]["error"])
        self.assertEqual(urlopen.call_count, 2)


class _FakeRawHTTPResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeRawHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "_FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
