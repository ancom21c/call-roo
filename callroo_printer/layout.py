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
TEXT_BOX_PADDING_X = 14
TEXT_BOX_PADDING_Y = 12
TEXT_BOX_RADIUS = 10
TEXT_BOX_INSET = 4
TITLE_ICON_GAP_RATIO = 0.32
NO_LINE_START_CHARS = tuple(".,!?;:)]}）］｝〉》」』”’、。，！？：；…")
MANUAL_CONTENT_MARGIN_DEFAULT = 16
MANUAL_CONTENT_MARGIN_MIN = 0
MANUAL_CONTENT_MARGIN_MAX = 96
MANUAL_BOX_PADDING_X = MANUAL_CONTENT_MARGIN_DEFAULT
MANUAL_BOX_PADDING_Y = MANUAL_CONTENT_MARGIN_DEFAULT
MANUAL_BOX_RADIUS = 12
MANUAL_BORDER_STYLES = {"none", "thin", "thick", "double"}
MANUAL_TEXT_ALIGNS = {"left", "center", "right"}
MANUAL_TEXT_VERTICAL_ALIGNS = {"top", "center", "bottom"}
MANUAL_LABEL_WIDTH_MIN = 80
MANUAL_LABEL_HEIGHT_MIN = 56
MANUAL_LABEL_HEIGHT_MAX = 1200
MANUAL_IMAGE_SCALE_MIN = 25
MANUAL_IMAGE_SCALE_MAX = 300


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
    text_width = max(1, content_width - (TEXT_BOX_PADDING_X * 2))
    title = "오늘의 콜!루"
    tag_text = f"[{fortune_tag}]" if fortune_tag else ""
    timestamp_text = printed_at.strftime("%Y-%m-%d %H:%M:%S")
    tag_gap_px = max(8, config.section_gap_px // 2)

    measuring_image = Image.new("L", (config.paper_width_px, 10), color=255)
    measuring_draw = ImageDraw.Draw(measuring_image)

    fortune_lines = wrap_text_by_width(fortune_text, body_font, text_width)
    title_bbox = measuring_draw.multiline_textbbox((0, 0), title, font=title_font)
    title_height = _bbox_height(title_bbox)
    title_icon = _prepare_title_icon(config.title_icon_file, title_height)
    title_row_height = max(title_height, title_icon.height if title_icon else 0)
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
        + title_row_height
        + config.section_gap_px
        + asset_image.height
        + (tag_gap_px + _bbox_height(tag_bbox) if tag_text else 0)
        + config.section_gap_px
        + _bbox_height(fortune_bbox)
        + (TEXT_BOX_PADDING_Y * 2)
        + config.section_gap_px
        + _bbox_height(timestamp_bbox)
        + config.side_margin_px
    )

    canvas = Image.new("L", (config.paper_width_px, total_height), color=255)
    draw = ImageDraw.Draw(canvas)
    cursor_y = config.side_margin_px

    _draw_title_row(
        draw,
        title,
        title_font,
        title_bbox,
        title_icon,
        cursor_y,
        config.paper_width_px,
        title_row_height,
    )
    cursor_y += title_row_height
    cursor_y += config.section_gap_px

    canvas.paste(asset_image, (config.side_margin_px, cursor_y))
    cursor_y += asset_image.height
    if tag_text:
        cursor_y += tag_gap_px
        _draw_centered_text(draw, tag_text, tag_font, cursor_y, config.paper_width_px)
        cursor_y += _bbox_height(tag_bbox)
    cursor_y += config.section_gap_px

    text_box = (
        config.side_margin_px,
        cursor_y,
        config.side_margin_px + content_width - 1,
        cursor_y + _bbox_height(fortune_bbox) + (TEXT_BOX_PADDING_Y * 2),
    )
    _draw_text_box(draw, text_box)
    draw.multiline_text(
        (
            config.side_margin_px + TEXT_BOX_PADDING_X,
            cursor_y + TEXT_BOX_PADDING_Y,
        ),
        fortune_lines,
        fill=0,
        font=body_font,
        spacing=6,
    )
    cursor_y = text_box[3] + config.section_gap_px

    draw.text(
        (config.side_margin_px, cursor_y),
        timestamp_text,
        fill=0,
        font=timestamp_font,
    )
    return canvas


def compose_manual_print(
    *,
    text: str,
    image_path: Path | None,
    image_items: list[dict[str, object]] | None = None,
    text_items: list[dict[str, object]] | None = None,
    printed_at: datetime,
    config: LayoutConfig,
    border_style: str = "thin",
    text_align: str = "center",
    text_vertical_align: str = "center",
    font_size: int | None = None,
    label_width_px: int | None = None,
    label_height_px: int | None = None,
    content_margin_px: int | None = None,
    image_scale_percent: int = 100,
    image_crop: bool = False,
    image_rotation_degrees: int = 0,
) -> Image.Image:
    del printed_at
    clean_text = text.strip()
    positioned_images = _normalize_manual_image_items(image_items, image_path)
    positioned_texts = _normalize_manual_text_items(text_items)
    if not clean_text and not positioned_images and not positioned_texts:
        raise ValueError("manual print requires text or image")

    normalized_border = (
        border_style.strip().lower()
        if border_style.strip().lower() in MANUAL_BORDER_STYLES
        else "thin"
    )
    normalized_align = (
        text_align.strip().lower()
        if text_align.strip().lower() in MANUAL_TEXT_ALIGNS
        else "center"
    )
    normalized_vertical_align = (
        text_vertical_align.strip().lower()
        if text_vertical_align.strip().lower() in MANUAL_TEXT_VERTICAL_ALIGNS
        else "center"
    )
    text_size = _clamp_int(font_size or config.body_font_size, 16, 56)
    body_font = load_font(config.font_path, text_size)
    content_margin = _clamp_int(
        (
            content_margin_px
            if content_margin_px is not None
            else MANUAL_CONTENT_MARGIN_DEFAULT
        ),
        MANUAL_CONTENT_MARGIN_MIN,
        MANUAL_CONTENT_MARGIN_MAX,
    )

    max_label_width = config.paper_width_px - (config.side_margin_px * 2)
    label_width = _clamp_int(
        label_width_px or max_label_width,
        min(MANUAL_LABEL_WIDTH_MIN, max_label_width),
        max_label_width,
    )
    fixed_label_height = (
        _clamp_int(label_height_px, MANUAL_LABEL_HEIGHT_MIN, MANUAL_LABEL_HEIGHT_MAX)
        if label_height_px is not None
        else None
    )
    inner_width = max(1, label_width - (content_margin * 2))
    inner_height = (
        max(1, fixed_label_height - (content_margin * 2))
        if fixed_label_height is not None
        else None
    )
    image_scale = _clamp_int(
        image_scale_percent,
        MANUAL_IMAGE_SCALE_MIN,
        MANUAL_IMAGE_SCALE_MAX,
    )
    sections: list[tuple[str, Image.Image | str]] = []

    text_lines = ""
    text_height = 0
    if clean_text and not positioned_texts:
        measuring_image = Image.new("L", (config.paper_width_px, 10), color=255)
        measuring_draw = ImageDraw.Draw(measuring_image)
        text_lines = wrap_text_by_width(clean_text, body_font, inner_width)
        text_bbox = measuring_draw.multiline_textbbox(
            (0, 0),
            text_lines,
            font=body_font,
            spacing=6,
        )
        text_height = _bbox_height(text_bbox)

    image_slot_height = max(80, config.image_max_height_px)
    if positioned_images and inner_height is not None:
        image_slot_height = max(
            1,
            inner_height
            - (text_height if clean_text else 0)
            - (config.section_gap_px if clean_text else 0),
        )

    legacy_flow = len(positioned_images) == 1 and not positioned_images[0].get("positioned")
    if legacy_flow:
        legacy_image_path = positioned_images[0]["path"]
        assert isinstance(legacy_image_path, Path)
        sections.append(
            (
                "image",
                _prepare_manual_asset(
                    legacy_image_path,
                    inner_width,
                    image_slot_height,
                    scale_percent=image_scale,
                    crop=image_crop,
                    rotation_degrees=image_rotation_degrees,
                ),
            )
        )
    if clean_text and not positioned_texts:
        sections.append(("text", text_lines))

    gap_total = config.section_gap_px * max(0, len(sections) - 1)
    content_height = gap_total
    for kind, value in sections:
        if kind == "image":
            assert isinstance(value, Image.Image)
            content_height += value.height
        else:
            content_height += text_height

    box_height = fixed_label_height or content_height + (content_margin * 2)
    total_height = config.side_margin_px + box_height + config.side_margin_px
    canvas = Image.new("L", (config.paper_width_px, total_height), color=255)
    draw = ImageDraw.Draw(canvas)

    box_left = (config.paper_width_px - label_width) // 2
    box = (
        box_left,
        config.side_margin_px,
        box_left + label_width - 1,
        config.side_margin_px + box_height - 1,
    )
    _draw_manual_border(draw, box, normalized_border)

    if positioned_images and not legacy_flow:
        positioned_inner_width = max(1, label_width - (content_margin * 2))
        positioned_inner_height = max(1, box_height - (content_margin * 2))
        for item in positioned_images:
            item_path = item["path"]
            assert isinstance(item_path, Path)
            item_width = _clamp_int(
                int(item.get("width", inner_width)),
                1,
                positioned_inner_width * 2,
            )
            item_height = _clamp_int(
                int(item.get("height", image_slot_height)),
                1,
                positioned_inner_height * 2,
            )
            item_x = _clamp_int(
                int(item.get("x", 0)),
                min(0, -item_width + 24),
                max(0, positioned_inner_width - 24),
            )
            item_y = _clamp_int(
                int(item.get("y", 0)),
                min(0, -item_height + 24),
                max(0, positioned_inner_height - 24),
            )
            item_rotation = _clamp_int(int(item.get("rotation_degrees", 0)), -180, 180)
            item_crop = bool(item.get("crop", False))
            prepared = _prepare_manual_asset(
                item_path,
                item_width,
                item_height,
                scale_percent=100,
                crop=item_crop,
                rotation_degrees=item_rotation,
                preserve_canvas=True,
            )
            _paste_clipped(
                canvas,
                prepared,
                box[0] + content_margin + item_x,
                box[1] + content_margin + item_y,
                box,
                content_margin=content_margin,
            )

    if positioned_texts:
        positioned_inner_width = max(1, label_width - (content_margin * 2))
        positioned_inner_height = max(1, box_height - (content_margin * 2))
        for item in positioned_texts:
            item_text = str(item.get("text", "")).strip()
            if not item_text:
                continue
            item_width = _clamp_int(
                int(item.get("width", positioned_inner_width)),
                1,
                positioned_inner_width * 2,
            )
            item_height = _clamp_int(
                int(item.get("height", positioned_inner_height)),
                1,
                positioned_inner_height * 2,
            )
            item_x = _clamp_int(
                int(item.get("x", 0)),
                min(0, -item_width + 24),
                max(0, positioned_inner_width - 24),
            )
            item_y = _clamp_int(
                int(item.get("y", 0)),
                min(0, -item_height + 24),
                max(0, positioned_inner_height - 24),
            )
            item_font_size = _clamp_int(
                int(item.get("font_size", text_size)),
                16,
                56,
            )
            item_align = str(item.get("text_align", normalized_align)).strip().lower()
            if item_align not in MANUAL_TEXT_ALIGNS:
                item_align = normalized_align
            item_vertical_align = (
                str(item.get("vertical_align", normalized_vertical_align)).strip().lower()
            )
            if item_vertical_align not in MANUAL_TEXT_VERTICAL_ALIGNS:
                item_vertical_align = normalized_vertical_align
            item_font = load_font(config.font_path, item_font_size)
            _draw_positioned_text_box(
                canvas,
                item_text,
                item_font,
                box[0] + content_margin + item_x,
                box[1] + content_margin + item_y,
                item_width,
                item_height,
                item_align,
                item_vertical_align,
                box,
                content_margin=content_margin,
            )

    spare_height = max(0, box_height - (content_margin * 2) - content_height)
    cursor_y = (
        box[1]
        + content_margin
        + _vertical_offset(spare_height, normalized_vertical_align)
    )
    for index, (kind, value) in enumerate(sections):
        if index:
            cursor_y += config.section_gap_px
        if kind == "image":
            assert isinstance(value, Image.Image)
            x = box[0] + content_margin + ((inner_width - value.width) // 2)
            _paste_clipped(canvas, value, x, cursor_y, box, content_margin=content_margin)
            cursor_y += value.height
            continue

        assert isinstance(value, str)
        _draw_aligned_multiline_text(
            draw,
            value,
            body_font,
            box[0] + content_margin,
            cursor_y,
            inner_width,
            normalized_align,
        )
        cursor_y += text_height

    return canvas


def wrap_text_by_width(text: str, font: ImageFont.ImageFont, max_width: int) -> str:
    probe = Image.new("L", (max_width, 10), color=255)
    draw = ImageDraw.Draw(probe)

    wrapped_lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        wrapped_lines.extend(_wrap_single_paragraph(paragraph, font, max_width, draw))
    wrapped_lines = _avoid_orphan_punctuation(wrapped_lines, font, max_width, draw)
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
    return _avoid_orphan_punctuation(lines, font, max_width, draw)


def _prepare_asset(asset_path: Path, max_width: int, max_height: int) -> Image.Image:
    with Image.open(asset_path) as image:
        grayscale = image.convert("L")
        fitted = ImageOps.contain(grayscale, (max_width, max_height))
        canvas = Image.new("L", (max_width, fitted.height), color=255)
        offset_x = (max_width - fitted.width) // 2
        canvas.paste(fitted, (offset_x, 0))
        return canvas


def _normalize_manual_image_items(
    image_items: list[dict[str, object]] | None,
    fallback_image_path: Path | None,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    if image_items:
        for item in image_items:
            raw_path = item.get("path")
            if raw_path is None:
                continue
            path = raw_path if isinstance(raw_path, Path) else Path(str(raw_path))
            normalized.append(
                {
                    "path": path,
                    "x": int(item.get("x", 0)),
                    "y": int(item.get("y", 0)),
                    "width": int(item.get("width", 1)),
                    "height": int(item.get("height", 1)),
                    "rotation_degrees": int(item.get("rotation_degrees", 0)),
                    "crop": bool(item.get("crop", False)),
                    "positioned": True,
                }
            )
    if normalized:
        return normalized
    if fallback_image_path is None:
        return []
    return [{"path": fallback_image_path, "positioned": False}]


def _normalize_manual_text_items(
    text_items: list[dict[str, object]] | None,
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    if not text_items:
        return normalized
    for item in text_items:
        raw_text = str(item.get("text", "")).strip()
        if not raw_text:
            continue
        normalized.append(
            {
                "text": raw_text,
                "x": int(item.get("x", 0)),
                "y": int(item.get("y", 0)),
                "width": int(item.get("width", 1)),
                "height": int(item.get("height", 1)),
                "font_size": int(item.get("font_size", 28)),
                "text_align": str(item.get("text_align", "center")),
                "vertical_align": str(item.get("vertical_align", "center")),
            }
        )
    return normalized


def _prepare_manual_asset(
    asset_path: Path,
    max_width: int,
    max_height: int,
    *,
    scale_percent: int,
    crop: bool,
    rotation_degrees: int,
    preserve_canvas: bool = False,
) -> Image.Image:
    with Image.open(asset_path) as image:
        grayscale = image.convert("L")
        if rotation_degrees % 360:
            grayscale = grayscale.rotate(
                -rotation_degrees,
                expand=True,
                fillcolor=255,
            )
        base_scale = (
            max(max_width / grayscale.width, max_height / grayscale.height)
            if crop
            else min(max_width / grayscale.width, max_height / grayscale.height)
        )
        scale = max(0.01, base_scale * (scale_percent / 100))
        target_size = (
            max(1, round(grayscale.width * scale)),
            max(1, round(grayscale.height * scale)),
        )
        resized = grayscale.resize(target_size, Image.Resampling.LANCZOS)
        if crop:
            return _center_on_canvas(resized, max_width, max_height)
        if preserve_canvas:
            return _center_on_canvas(resized, max_width, max_height)
        if resized.width <= max_width and resized.height <= max_height:
            return resized
        return _center_crop(resized, max_width, max_height)


def _center_on_canvas(image: Image.Image, width: int, height: int) -> Image.Image:
    canvas = Image.new("L", (width, height), color=255)
    crop = _center_crop(image, width, height)
    offset = (
        (width - crop.width) // 2,
        (height - crop.height) // 2,
    )
    canvas.paste(crop, offset)
    return canvas


def _center_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    crop_width = min(width, image.width)
    crop_height = min(height, image.height)
    left = max(0, (image.width - crop_width) // 2)
    top = max(0, (image.height - crop_height) // 2)
    return image.crop((left, top, left + crop_width, top + crop_height))


def _paste_clipped(
    canvas: Image.Image,
    image: Image.Image,
    x: int,
    y: int,
    clip_box: tuple[int, int, int, int],
    *,
    content_margin: int = MANUAL_CONTENT_MARGIN_DEFAULT,
) -> None:
    left = max(x, clip_box[0] + content_margin)
    top = max(y, clip_box[1] + content_margin)
    right = min(x + image.width, clip_box[2] - content_margin + 1)
    bottom = min(y + image.height, clip_box[3] - content_margin + 1)
    if right <= left or bottom <= top:
        return
    crop = image.crop((left - x, top - y, right - x, bottom - y))
    canvas.paste(crop, (left, top))


def _draw_positioned_text_box(
    canvas: Image.Image,
    text: str,
    font: ImageFont.ImageFont,
    x: int,
    y: int,
    width: int,
    height: int,
    align: str,
    vertical_align: str,
    clip_box: tuple[int, int, int, int],
    *,
    content_margin: int = MANUAL_CONTENT_MARGIN_DEFAULT,
) -> None:
    left = max(x, clip_box[0] + content_margin)
    top = max(y, clip_box[1] + content_margin)
    right = min(x + width, clip_box[2] - content_margin + 1)
    bottom = min(y + height, clip_box[3] - content_margin + 1)
    if right <= left or bottom <= top:
        return

    mask = Image.new("L", (width, height), color=0)
    mask_draw = ImageDraw.Draw(mask)
    lines = wrap_text_by_width(text, font, max(1, width))
    text_height = _multiline_text_height(mask_draw, lines, font)
    text_y = _vertical_offset(max(0, height - text_height), vertical_align)
    _draw_aligned_multiline_text(
        mask_draw,
        lines,
        font,
        0,
        text_y,
        width,
        align,
        fill=255,
    )
    cropped_mask = mask.crop((left - x, top - y, right - x, bottom - y))
    canvas.paste(0, (left, top), cropped_mask)


def _prepare_title_icon(icon_path: Path | None, target_size: int) -> Image.Image | None:
    if icon_path is None or target_size <= 0:
        return None
    try:
        with Image.open(icon_path) as image:
            rgba = image.convert("RGBA")
    except OSError:
        return None

    fitted = ImageOps.contain(rgba, (target_size, target_size))
    canvas = Image.new("L", (target_size, target_size), color=255)
    grayscale = fitted.convert("L")
    alpha = fitted.getchannel("A")
    offset = (
        (target_size - fitted.width) // 2,
        (target_size - fitted.height) // 2,
    )
    canvas.paste(grayscale, offset, alpha)
    return canvas


def _draw_title_row(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    text_bbox: tuple[int, int, int, int],
    icon: Image.Image | None,
    y: int,
    canvas_width: int,
    row_height: int,
) -> None:
    text_width = _bbox_width(text_bbox)
    text_height = _bbox_height(text_bbox)
    icon_gap = max(4, round(text_height * TITLE_ICON_GAP_RATIO)) if icon else 0
    group_width = text_width + (icon_gap + icon.width if icon else 0)
    start_x = (canvas_width - group_width) // 2
    text_x = start_x - text_bbox[0]
    text_y = y + ((row_height - text_height) // 2) - text_bbox[1]
    draw.text((text_x, text_y), text, fill=0, font=font)
    if icon:
        icon_x = start_x + text_width + icon_gap
        icon_y = y + ((row_height - icon.height) // 2)
        draw.bitmap((icon_x, icon_y), ImageOps.invert(icon), fill=0)


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    y: int,
    canvas_width: int,
) -> None:
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    x = ((canvas_width - width) // 2) - bbox[0]
    draw.text((x, y), text, fill=0, font=font)


def _draw_text_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
) -> None:
    draw.rounded_rectangle(box, radius=TEXT_BOX_RADIUS, fill=255, outline=0, width=2)
    inner = (
        box[0] + TEXT_BOX_INSET,
        box[1] + TEXT_BOX_INSET,
        box[2] - TEXT_BOX_INSET,
        box[3] - TEXT_BOX_INSET,
    )
    draw.rounded_rectangle(inner, radius=max(1, TEXT_BOX_RADIUS - 4), outline=165, width=1)
    accent_top = box[1] + 6
    accent_bottom = box[3] - 6
    draw.line((box[0] + 6, accent_top, box[0] + 6, accent_bottom), fill=0, width=1)
    draw.line((box[2] - 6, accent_top, box[2] - 6, accent_bottom), fill=0, width=1)


def _draw_manual_border(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    border_style: str,
) -> None:
    if border_style == "none":
        return
    if border_style == "thick":
        draw.rounded_rectangle(box, radius=MANUAL_BOX_RADIUS, outline=0, width=4)
        return
    if border_style == "double":
        draw.rounded_rectangle(box, radius=MANUAL_BOX_RADIUS, outline=0, width=2)
        inner = (
            box[0] + 6,
            box[1] + 6,
            box[2] - 6,
            box[3] - 6,
        )
        draw.rounded_rectangle(inner, radius=max(1, MANUAL_BOX_RADIUS - 4), outline=145, width=1)
        return
    draw.rounded_rectangle(box, radius=MANUAL_BOX_RADIUS, outline=0, width=2)


def _draw_aligned_multiline_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    x: int,
    y: int,
    width: int,
    align: str,
    *,
    fill: int = 0,
) -> None:
    line_y = y
    for line in text.splitlines() or [""]:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = _bbox_width(bbox)
        if align == "left":
            line_x = x
        elif align == "right":
            line_x = x + width - line_width
        else:
            line_x = x + ((width - line_width) // 2)
        draw.text((line_x - bbox[0], line_y - bbox[1]), line, fill=fill, font=font)
        line_y += _bbox_height(bbox) + 6


def _vertical_offset(spare_height: int, align: str) -> int:
    if align == "top":
        return 0
    if align == "bottom":
        return spare_height
    return spare_height // 2


def _multiline_text_height(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
) -> int:
    total = 0
    lines = text.splitlines() or [""]
    for index, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        total += _bbox_height(bbox)
        if index < len(lines) - 1:
            total += 6
    return total


def _avoid_orphan_punctuation(
    lines: list[str],
    font: ImageFont.ImageFont,
    max_width: int,
    draw: ImageDraw.ImageDraw,
) -> list[str]:
    adjusted: list[str] = []
    for line in lines:
        if not line:
            adjusted.append(line)
            continue
        if _is_punctuation_only(line) and adjusted:
            adjusted[-1] = adjusted[-1].rstrip() + line
            continue
        prefix = _leading_no_line_start_chars(line)
        if prefix and adjusted:
            previous = adjusted[-1].rstrip()
            candidate = previous + prefix
            if _text_width(candidate, font, draw) <= max_width or _is_punctuation_only(prefix):
                adjusted[-1] = candidate
                remainder = line[len(prefix) :].lstrip()
                if remainder:
                    adjusted.append(remainder)
                continue
        adjusted.append(line)
    return adjusted


def _is_punctuation_only(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and all(char in NO_LINE_START_CHARS for char in stripped)


def _leading_no_line_start_chars(value: str) -> str:
    chars = []
    for char in value:
        if char not in NO_LINE_START_CHARS:
            break
        chars.append(char)
    return "".join(chars)


def _text_width(
    text: str,
    font: ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
) -> int:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


def _bbox_height(bbox: tuple[int, int, int, int]) -> int:
    return bbox[3] - bbox[1]


def _bbox_width(bbox: tuple[int, int, int, int]) -> int:
    return bbox[2] - bbox[0]


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))
