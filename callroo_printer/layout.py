from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from callroo_printer.config import LayoutConfig

FALLBACK_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def compose_ticket(
    asset_path: Path,
    fortune_text: str,
    printed_at: datetime,
    config: LayoutConfig,
    fortune_tag: str | None = None,
) -> Image.Image:
    title_font = load_font(config.font_path, config.title_font_size)
    body_font = load_font(config.font_path, config.body_font_size)
    timestamp_font = load_font(config.font_path, config.timestamp_font_size)
    tag_font = load_font(config.font_path, config.timestamp_font_size)

    content_width = config.paper_width_px - (config.side_margin_px * 2)
    title = "오늘의 콜!루"
    tag_text = f"[{fortune_tag}]" if fortune_tag else ""
    timestamp_text = printed_at.strftime("%Y-%m-%d %H:%M:%S")
    tag_gap_px = max(8, config.section_gap_px // 2)

    measuring_image = Image.new("L", (config.paper_width_px, 10), color=255)
    measuring_draw = ImageDraw.Draw(measuring_image)

    fortune_lines = wrap_text_by_width(fortune_text, body_font, content_width)
    title_bbox = measuring_draw.multiline_textbbox((0, 0), title, font=title_font)
    tag_bbox = measuring_draw.multiline_textbbox((0, 0), tag_text, font=tag_font)
    fortune_bbox = measuring_draw.multiline_textbbox(
        (0, 0), fortune_lines, font=body_font, spacing=6
    )
    timestamp_bbox = measuring_draw.multiline_textbbox(
        (0, 0), timestamp_text, font=timestamp_font
    )

    asset_image = _prepare_asset(asset_path, content_width, config.image_max_height_px)

    total_height = (
        config.side_margin_px
        + _bbox_height(title_bbox)
        + (tag_gap_px + _bbox_height(tag_bbox) if tag_text else 0)
        + config.section_gap_px
        + asset_image.height
        + config.section_gap_px
        + _bbox_height(fortune_bbox)
        + config.section_gap_px
        + _bbox_height(timestamp_bbox)
        + config.side_margin_px
    )

    canvas = Image.new("L", (config.paper_width_px, total_height), color=255)
    draw = ImageDraw.Draw(canvas)
    cursor_y = config.side_margin_px

    draw.text((config.side_margin_px, cursor_y), title, fill=0, font=title_font)
    cursor_y += _bbox_height(title_bbox)
    if tag_text:
        cursor_y += tag_gap_px
        draw.text((config.side_margin_px, cursor_y), tag_text, fill=0, font=tag_font)
        cursor_y += _bbox_height(tag_bbox)
    cursor_y += config.section_gap_px

    canvas.paste(asset_image, (config.side_margin_px, cursor_y))
    cursor_y += asset_image.height + config.section_gap_px

    draw.multiline_text(
        (config.side_margin_px, cursor_y),
        fortune_lines,
        fill=0,
        font=body_font,
        spacing=6,
    )
    cursor_y += _bbox_height(fortune_bbox) + config.section_gap_px

    draw.text(
        (config.side_margin_px, cursor_y),
        timestamp_text,
        fill=0,
        font=timestamp_font,
    )
    return canvas


def wrap_text_by_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    probe = Image.new("L", (max_width, 10), color=255)
    draw = ImageDraw.Draw(probe)

    wrapped_lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped_lines.extend(_wrap_single_paragraph(paragraph, font, max_width, draw))
    return "\n".join(wrapped_lines)


def load_font(font_path: Path | None, size: int) -> ImageFont.ImageFont:
    candidates: list[str] = []
    if font_path:
        candidates.append(str(font_path))
    candidates.extend(FALLBACK_FONT_CANDIDATES)

    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_single_paragraph(
    paragraph: str,
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    if not paragraph:
        return [""]

    lines: list[str] = []
    current = ""
    for char in paragraph:
        trial = current + char
        bbox = draw.textbbox((0, 0), trial, font=font)
        width = bbox[2] - bbox[0]
        if current and width > max_width:
            lines.append(current.rstrip())
            current = char.lstrip()
        else:
            current = trial

    if current:
        lines.append(current.rstrip())
    return lines


def _prepare_asset(asset_path: Path, max_width: int, max_height: int) -> Image.Image:
    with Image.open(asset_path) as image:
        grayscale = image.convert("L")
        fitted = ImageOps.contain(grayscale, (max_width, max_height))
        canvas = Image.new("L", (max_width, fitted.height), color=255)
        offset_x = (max_width - fitted.width) // 2
        canvas.paste(fitted, (offset_x, 0))
        return canvas


def _bbox_height(bbox: tuple[int, int, int, int]) -> int:
    return bbox[3] - bbox[1]
