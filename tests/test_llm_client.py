from __future__ import annotations

import unittest

from callroo_printer.llm_client import _compose_user_prompt


class LLMClientTest(unittest.TestCase):
    def test_compose_user_prompt_returns_base_prompt_without_hint(self) -> None:
        self.assertEqual(
            _compose_user_prompt("기본 프롬프트", current_time_hint=None, variation_hint=None),
            "기본 프롬프트",
        )

    def test_compose_user_prompt_appends_variation_hint(self) -> None:
        prompt = _compose_user_prompt(
            "기본 프롬프트",
            current_time_hint=None,
            variation_hint="자판기 버튼, 짧은 리듬",
        )
        self.assertIn("기본 프롬프트", prompt)
        self.assertIn("이번 출력 변주 지시", prompt)
        self.assertIn("자판기 버튼, 짧은 리듬", prompt)

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


if __name__ == "__main__":
    unittest.main()
