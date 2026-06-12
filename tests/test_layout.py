from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image

from callroo_printer.config import LayoutConfig
from callroo_printer.layout import compose_manual_print, compose_ticket, wrap_text_by_width
from callroo_printer.llm_client import extract_json_object, sanitize_text


class LayoutTest(unittest.TestCase):
    def test_wrap_text_by_width_breaks_long_korean_string(self) -> None:
        text = "오늘의운세는햇살처럼조용히당신곁에머물러요"
        wrapped = wrap_text_by_width(text, font=_default_font(), max_width=80)
        self.assertIn("\n", wrapped)

    def test_wrap_text_by_width_avoids_punctuation_only_line(self) -> None:
        font = _default_font()
        max_width = _text_width("가나다라마바", font)

        wrapped = wrap_text_by_width("가나다라마바.", font=font, max_width=max_width)

        self.assertEqual(wrapped, "가나다라마바.")
        self.assertFalse(any(line.strip() == "." for line in wrapped.splitlines()))

        explicit = wrap_text_by_width("가나다라마바\n.", font=font, max_width=max_width)
        self.assertEqual(explicit, "가나다라마바.")

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
                    title_icon_file=None,
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

    def test_compose_manual_print_combines_text_image_and_border(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "manual.png"
            Image.new("RGB", (120, 80), color="black").save(asset_path)

            image = compose_manual_print(
                text="원하는 문구\n바로 출력",
                image_path=asset_path,
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="double",
                text_align="center",
                font_size=26,
            )

        self.assertEqual(image.width, 384)
        self.assertGreater(image.height, 120)
        self.assertLess(image.getpixel((192, 20)), 245)

    def test_compose_manual_print_allows_no_border_text_only(self) -> None:
        image = compose_manual_print(
            text="테두리 없이 출력",
            image_path=None,
            printed_at=datetime(2026, 6, 12, 12, 0, 0),
            config=_layout_config(),
            border_style="none",
            text_align="left",
            font_size=24,
        )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.getpixel((192, 20)), 255)

    def test_compose_manual_print_respects_label_size(self) -> None:
        image = compose_manual_print(
            text="작은 라벨",
            image_path=None,
            printed_at=datetime(2026, 6, 12, 12, 0, 0),
            config=_layout_config(),
            border_style="thin",
            label_width_px=180,
            label_height_px=90,
        )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.height, 130)
        self.assertLess(image.getpixel((192, 20)), 245)
        self.assertEqual(image.getpixel((20, 20)), 255)

    def test_compose_manual_print_applies_image_transform_options(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "manual.png"
            Image.new("RGB", (160, 60), color="black").save(asset_path)

            image = compose_manual_print(
                text="",
                image_path=asset_path,
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="none",
                label_width_px=180,
                label_height_px=120,
                image_scale_percent=180,
                image_crop=True,
                image_rotation_degrees=90,
            )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.height, 160)
        self.assertLess(image.getpixel((192, 80)), 245)

    def test_compose_manual_print_positions_multiple_images(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_path = Path(tmp) / "first.png"
            second_path = Path(tmp) / "second.png"
            Image.new("RGB", (30, 30), color="black").save(first_path)
            Image.new("RGB", (30, 30), color="black").save(second_path)

            image = compose_manual_print(
                text="",
                image_path=None,
                image_items=[
                    {
                        "path": first_path,
                        "x": 0,
                        "y": 0,
                        "width": 30,
                        "height": 30,
                    },
                    {
                        "path": second_path,
                        "x": 80,
                        "y": 30,
                        "width": 30,
                        "height": 30,
                    },
                ],
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="thin",
                label_width_px=160,
                label_height_px=100,
            )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.height, 140)
        self.assertLess(image.getpixel((129, 40)), 245)
        self.assertLess(image.getpixel((209, 70)), 245)

    def test_compose_manual_print_preserves_oversized_positioned_image_crop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "wide.png"
            source = Image.new("L", (624, 100), color=255)
            for x in range(312, 624):
                for y in range(100):
                    source.putpixel((x, y), 0)
            source.save(asset_path)

            image = compose_manual_print(
                text="",
                image_path=None,
                image_items=[
                    {
                        "path": asset_path,
                        "x": -312,
                        "y": 20,
                        "width": 624,
                        "height": 100,
                    },
                ],
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="thin",
                label_width_px=344,
                label_height_px=180,
            )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.height, 220)
        self.assertLess(image.getpixel((192, 106)), 245)

    def test_compose_manual_print_matches_contain_preview_for_positioned_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "tall.png"
            Image.new("L", (300, 600), color=0).save(asset_path)

            image = compose_manual_print(
                text="",
                image_path=None,
                image_items=[
                    {
                        "path": asset_path,
                        "x": -225,
                        "y": 0,
                        "width": 600,
                        "height": 300,
                    },
                ],
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="thin",
                label_width_px=344,
                label_height_px=180,
            )

        self.assertEqual(image.width, 384)
        self.assertEqual(image.height, 220)
        self.assertLess(image.getpixel((111, 90)), 245)

    def test_compose_manual_print_text_overlay_does_not_blank_positioned_image(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "black.png"
            Image.new("L", (312, 148), color=0).save(asset_path)

            image = compose_manual_print(
                text="A",
                image_path=None,
                image_items=[
                    {
                        "path": asset_path,
                        "x": 0,
                        "y": 0,
                        "width": 312,
                        "height": 148,
                    },
                ],
                printed_at=datetime(2026, 6, 12, 12, 0, 0),
                config=_layout_config(),
                border_style="thin",
                label_width_px=344,
                label_height_px=180,
            )

        self.assertLess(image.getpixel((42, 110)), 245)

    def test_compose_manual_print_positions_multiple_text_boxes(self) -> None:
        image = compose_manual_print(
            text="",
            image_path=None,
            text_items=[
                {
                    "text": "LEFT",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 60,
                    "font_size": 24,
                    "text_align": "left",
                    "vertical_align": "top",
                },
                {
                    "text": "RIGHT",
                    "x": 180,
                    "y": 80,
                    "width": 120,
                    "height": 60,
                    "font_size": 24,
                    "text_align": "right",
                    "vertical_align": "bottom",
                },
            ],
            printed_at=datetime(2026, 6, 12, 12, 0, 0),
            config=_layout_config(),
            border_style="none",
            label_width_px=344,
            label_height_px=220,
        )

        self.assertIsNotNone(_dark_bounds(image, (36, 36, 136, 96)))
        self.assertIsNotNone(_dark_bounds(image, (216, 116, 336, 176)))

    def test_compose_manual_print_applies_text_vertical_alignment(self) -> None:
        base_item = {
            "text": "A",
            "x": 0,
            "y": 0,
            "width": 120,
            "height": 120,
            "font_size": 32,
            "text_align": "center",
        }

        top_image = compose_manual_print(
            text="",
            image_path=None,
            text_items=[{**base_item, "vertical_align": "top"}],
            printed_at=datetime(2026, 6, 12, 12, 0, 0),
            config=_layout_config(),
            border_style="none",
            label_width_px=344,
            label_height_px=180,
        )
        bottom_image = compose_manual_print(
            text="",
            image_path=None,
            text_items=[{**base_item, "vertical_align": "bottom"}],
            printed_at=datetime(2026, 6, 12, 12, 0, 0),
            config=_layout_config(),
            border_style="none",
            label_width_px=344,
            label_height_px=180,
        )

        top_bounds = _dark_bounds(top_image, (36, 36, 156, 156))
        bottom_bounds = _dark_bounds(bottom_image, (36, 36, 156, 156))
        self.assertIsNotNone(top_bounds)
        self.assertIsNotNone(bottom_bounds)
        assert top_bounds is not None
        assert bottom_bounds is not None
        self.assertLess(top_bounds[1], bottom_bounds[1])

    def test_compose_ticket_centers_title_and_tag_below_image_with_text_box(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            asset_path = Path(tmp) / "asset.png"
            Image.new("RGB", (80, 40), color="black").save(asset_path)

            image = compose_ticket(
                asset_path=asset_path,
                fortune_text="오늘의 한 가지\n물 한 컵 먼저\n바로 시작",
                printed_at=datetime(2026, 4, 1, 19, 30, 0),
                config=LayoutConfig(
                    paper_width_px=384,
                    side_margin_px=20,
                    section_gap_px=16,
                    image_max_height_px=120,
                    title_icon_file=None,
                    title_font_size=28,
                    body_font_size=24,
                    timestamp_font_size=18,
                    font_path=None,
                    threshold=160,
                    max_fortune_chars=100,
                ),
                fortune_tag="번뜩",
            )

        groups = _row_groups(image)
        self.assertGreaterEqual(len(groups), 5)
        title_bbox = _bbox_for_rows(image, groups[0])
        asset_bbox = _bbox_for_rows(image, groups[1])
        tag_bbox = _bbox_for_rows(image, groups[2])
        later_bboxes = [_bbox_for_rows(image, group) for group in groups[3:]]

        self.assertAlmostEqual(_bbox_center_x(title_bbox), image.width / 2, delta=8)
        self.assertAlmostEqual(_bbox_center_x(asset_bbox), image.width / 2, delta=1)
        self.assertGreater(asset_bbox[2] - asset_bbox[0], 200)
        self.assertGreater(tag_bbox[1], asset_bbox[3])
        self.assertAlmostEqual(_bbox_center_x(tag_bbox), image.width / 2, delta=8)
        self.assertTrue(
            any(bbox[0] <= 21 and bbox[2] >= 362 for bbox in later_bboxes),
            "main text border should span the content width",
        )

    def test_compose_ticket_draws_title_icon_next_to_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            asset_path = root / "asset.png"
            icon_path = root / "title-icon.png"
            Image.new("RGB", (80, 40), color="black").save(asset_path)
            Image.new("RGBA", (48, 48), color=(0, 0, 0, 255)).save(icon_path)

            image = compose_ticket(
                asset_path=asset_path,
                fortune_text="오늘의 한 가지\n물 한 컵 먼저\n바로 시작",
                printed_at=datetime(2026, 4, 1, 19, 30, 0),
                config=LayoutConfig(
                    paper_width_px=384,
                    side_margin_px=20,
                    section_gap_px=16,
                    image_max_height_px=120,
                    title_icon_file=icon_path,
                    title_font_size=28,
                    body_font_size=24,
                    timestamp_font_size=18,
                    font_path=None,
                    threshold=160,
                    max_fortune_chars=100,
                ),
                fortune_tag="번뜩",
            )

        title_bbox = _bbox_for_rows(image, _row_groups(image)[0])

        self.assertAlmostEqual(_bbox_center_x(title_bbox), image.width / 2, delta=8)
        self.assertGreater(title_bbox[2] - title_bbox[0], _text_width("오늘의 콜!루", _default_font()))

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


def _layout_config() -> LayoutConfig:
    return LayoutConfig(
        paper_width_px=384,
        side_margin_px=20,
        section_gap_px=16,
        image_max_height_px=180,
        title_icon_file=None,
        title_font_size=28,
        body_font_size=24,
        timestamp_font_size=18,
        font_path=None,
        threshold=160,
        max_fortune_chars=100,
    )


def _text_width(text: str, font) -> int:
    from PIL import ImageDraw

    probe = Image.new("L", (400, 10), color=255)
    draw = ImageDraw.Draw(probe)
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _row_groups(image: Image.Image) -> list[tuple[int, int]]:
    rows = []
    pixels = image.load()
    for y in range(image.height):
        if any(pixels[x, y] < 245 for x in range(image.width)):
            rows.append(y)
    groups: list[tuple[int, int]] = []
    if not rows:
        return groups
    start = rows[0]
    previous = rows[0]
    for row in rows[1:]:
        if row > previous + 1:
            groups.append((start, previous))
            start = row
        previous = row
    groups.append((start, previous))
    return groups


def _bbox_for_rows(image: Image.Image, rows: tuple[int, int]) -> tuple[int, int, int, int]:
    pixels = image.load()
    xs = [
        x
        for y in range(rows[0], rows[1] + 1)
        for x in range(image.width)
        if pixels[x, y] < 245
    ]
    return min(xs), rows[0], max(xs), rows[1]


def _bbox_center_x(bbox: tuple[int, int, int, int]) -> float:
    return (bbox[0] + bbox[2]) / 2


def _dark_bounds(
    image: Image.Image,
    region: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = region
    pixels = image.load()
    points = [
        (x, y)
        for y in range(top, min(bottom, image.height))
        for x in range(left, min(right, image.width))
        if pixels[x, y] < 245
    ]
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


if __name__ == "__main__":
    unittest.main()
