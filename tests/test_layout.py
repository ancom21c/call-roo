from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from callroo_printer.config import LayoutConfig
from callroo_printer.layout import compose_ticket, wrap_text_by_width
from callroo_printer.llm_client import extract_json_object, sanitize_text


class LayoutTest(unittest.TestCase):
    def test_wrap_text_by_width_breaks_long_korean_string(self) -> None:
        text = "오늘의운세는햇살처럼조용히당신곁에머물러요"
        wrapped = wrap_text_by_width(text, font=_default_font(), max_width=80)
        self.assertIn("\n", wrapped)

    def test_compose_ticket_returns_expected_width(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "asset.png"
            Image.new("RGB", (300, 200), color="white").save(asset_path)

            image = compose_ticket(
                asset_path=asset_path,
                fortune_text="달빛 아래 작은 웃음이 오늘의 길을 비춥니다.",
                printed_at=datetime(2026, 4, 1, 19, 30, 0),
                config=LayoutConfig(
                    paper_width_px=384,
                    side_margin_px=20,
                    section_gap_px=16,
                    image_max_height_px=180,
                    title_font_size=28,
                    body_font_size=24,
                    timestamp_font_size=18,
                    font_path=None,
                    threshold=160,
                    max_fortune_chars=100,
                ),
                fortune_tag="형광등",
            )

        self.assertEqual(image.width, 384)
        self.assertGreater(image.height, 0)

    def test_sanitize_text_preserves_haiku_line_breaks(self) -> None:
        text = "  바람이 분다 \n\n 꽃잎이 눕는다 \n 웃음이 온다  "
        sanitized = sanitize_text(text, max_chars=100)
        self.assertEqual(sanitized, "바람이 분다\n꽃잎이 눕는다\n웃음이 온다")

    def test_sanitize_text_converts_escaped_newlines(self) -> None:
        text = r"비 온 뒤 하늘\n맑게 개어 길이 열려\n오늘은 행운"
        sanitized = sanitize_text(text, max_chars=100)
        self.assertEqual(sanitized, "비 온 뒤 하늘\n맑게 개어 길이 열려\n오늘은 행운")

    def test_extract_json_object_returns_first_object(self) -> None:
        text = 'Thinking... {"fortune":"바람 끝에 웃음이 핀다"}'
        payload = extract_json_object(text)
        self.assertEqual(payload, {"fortune": "바람 끝에 웃음이 핀다"})

    def test_extract_json_object_handles_fenced_json(self) -> None:
        text = '```json\n{"fortune":"달빛 아래 한 걸음"}\n```'
        payload = extract_json_object(text)
        self.assertEqual(payload, {"fortune": "달빛 아래 한 걸음"})


def _default_font():
    from callroo_printer.layout import load_font

    return load_font(None, 24)


if __name__ == "__main__":
    unittest.main()
