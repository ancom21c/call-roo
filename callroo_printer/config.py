from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class BluetoothConfig:
    backend: str
    mac_address: str
    channel: Optional[int]
    channel_candidates: tuple[int, ...]
    auto_detect_channel: bool
    connect_timeout_seconds: float
    reconnect_delay_seconds: float
    keepalive_interval_seconds: float
    keepalive_timeout_seconds: float
    keepalive_hex: str
    keepalive_response_bytes: int
    adapter_name: str
    adapter_reset_after_failures: int
    adapter_reset_cooldown_seconds: float
    timiniprint_repo: Optional[Path]
    timiniprint_python: Optional[Path]
    timiniprint_cli: Optional[Path]
    timiniprint_darkness: Optional[int]
    timiniprint_direct_y_scale: float


@dataclass(frozen=True)
class LLMProfileConfig:
    name: str
    weight: float
    endpoint: str
    model: str
    system_prompt: str
    prompt: str
    tags: dict[str, tuple[Path, ...]]
    variation_hints: tuple[str, ...]
    current_time_hint_format: str
    current_time_hint_pre: str
    current_time_hint_post: str
    cleaned_examples_pre: str
    cleaned_examples_post: str
    response_json_key: str
    response_tag_key: str
    enable_thinking: bool
    api_key_env: Optional[str]
    temperature: float
    max_tokens: int
    timeout_seconds: float
    fallback_text: str


@dataclass(frozen=True)
class LLMConfig:
    profiles: tuple[LLMProfileConfig, ...]


@dataclass(frozen=True)
class LayoutConfig:
    paper_width_px: int
    side_margin_px: int
    section_gap_px: int
    image_max_height_px: int
    title_font_size: int
    body_font_size: int
    timestamp_font_size: int
    font_path: Optional[Path]
    threshold: int
    max_fortune_chars: int


@dataclass(frozen=True)
class OutputConfig:
    logs_dir: Path
    outputs_dir: Path
    log_filename: str


@dataclass(frozen=True)
class InputConfig:
    stdin_enabled: bool
    linux_event_enabled: bool
    linux_event_paths: tuple[Path, ...]
    linux_event_keycodes: tuple[int, ...]


@dataclass(frozen=True)
class AudioConfig:
    clip_file: Path
    clip_volume: float
    event_volume: float
    aplay_device: Optional[str]
    printer_connected_file: Optional[Path]
    printer_failed_file: Optional[Path]
    print_completed_file: Optional[Path]


@dataclass(frozen=True)
class AppConfig:
    assets_dir: Path
    output: OutputConfig
    cooldown_seconds: int
    cooldown_on_trigger: bool
    trailing_feed_lines: int
    input: InputConfig
    audio: AudioConfig
    bluetooth: BluetoothConfig
    llm: LLMConfig
    layout: LayoutConfig


def load_config(config_path: Path) -> AppConfig:
    config_file = config_path.expanduser().resolve()
    payload = json.loads(config_file.read_text(encoding="utf-8"))
    base_dir = config_file.parent

    assets_dir = _resolve_path(base_dir, payload.get("assets_dir", "assets"))
    output_section = payload.get("output", {})
    input_section = payload.get("input", {})
    audio_section = payload.get("audio", {})
    bluetooth_section = payload.get("bluetooth", {})
    llm_section = payload.get("llm", {})
    layout_section = payload.get("layout", {})

    output = OutputConfig(
        logs_dir=_resolve_path(base_dir, output_section.get("logs_dir", "logs")),
        outputs_dir=_resolve_path(
            base_dir, output_section.get("outputs_dir", "outputs")
        ),
        log_filename=str(output_section.get("log_filename", "callroo-printer.log")),
    )

    trigger_input = InputConfig(
        stdin_enabled=bool(input_section.get("stdin_enabled", True)),
        linux_event_enabled=bool(input_section.get("linux_event_enabled", False)),
        linux_event_paths=tuple(
            _resolve_path(base_dir, value)
            for value in input_section.get("linux_event_paths", [])
        ),
        linux_event_keycodes=tuple(
            int(value) for value in input_section.get("linux_event_keycodes", [28, 96])
        ),
    )

    audio = AudioConfig(
        clip_file=_resolve_audio_clip_path(
            assets_dir,
            audio_section.get("clip_file", "clip.wav"),
        ),
        clip_volume=_clip_volume(audio_section.get("clip_volume", 1.0)),
        event_volume=_clip_volume(audio_section.get("event_volume", 1.0)),
        aplay_device=_optional_str(audio_section.get("aplay_device")),
        printer_connected_file=_optional_audio_clip_path(
            assets_dir,
            audio_section.get("printer_connected_file"),
        ),
        printer_failed_file=_optional_audio_clip_path(
            assets_dir,
            audio_section.get("printer_failed_file"),
        ),
        print_completed_file=_optional_audio_clip_path(
            assets_dir,
            audio_section.get("print_completed_file"),
        ),
    )

    bluetooth = BluetoothConfig(
        backend=str(bluetooth_section.get("backend", "rfcomm")),
        mac_address=bluetooth_section.get("mac_address", "REPLACE_WITH_PRINTER_MAC"),
        channel=_optional_int(bluetooth_section.get("channel")),
        channel_candidates=tuple(
            int(value) for value in bluetooth_section.get("channel_candidates", [1, 6])
        ),
        auto_detect_channel=bool(bluetooth_section.get("auto_detect_channel", True)),
        connect_timeout_seconds=float(
            bluetooth_section.get("connect_timeout_seconds", 5.0)
        ),
        reconnect_delay_seconds=float(
            bluetooth_section.get("reconnect_delay_seconds", 2.0)
        ),
        keepalive_interval_seconds=float(
            bluetooth_section.get("keepalive_interval_seconds", 5.0)
        ),
        keepalive_timeout_seconds=float(
            bluetooth_section.get("keepalive_timeout_seconds", 3.0)
        ),
        keepalive_hex=str(bluetooth_section.get("keepalive_hex", "1f1111")),
        keepalive_response_bytes=int(
            bluetooth_section.get("keepalive_response_bytes", 3)
        ),
        adapter_name=str(bluetooth_section.get("adapter_name", "hci0")),
        adapter_reset_after_failures=max(
            1,
            int(bluetooth_section.get("adapter_reset_after_failures", 3)),
        ),
        adapter_reset_cooldown_seconds=float(
            bluetooth_section.get("adapter_reset_cooldown_seconds", 30.0)
        ),
        timiniprint_repo=_optional_path(
            base_dir, bluetooth_section.get("timiniprint_repo", "vendor/TiMini-Print")
        ),
        timiniprint_python=_optional_path(
            base_dir, bluetooth_section.get("timiniprint_python")
        ),
        timiniprint_cli=_optional_path(base_dir, bluetooth_section.get("timiniprint_cli")),
        timiniprint_darkness=_optional_int(
            bluetooth_section.get("timiniprint_darkness")
        ),
        timiniprint_direct_y_scale=float(
            bluetooth_section.get("timiniprint_direct_y_scale", 1.0)
        ),
    )

    llm = LLMConfig(
        profiles=_load_llm_profiles(assets_dir, llm_section),
    )

    font_path = layout_section.get("font_path")
    layout = LayoutConfig(
        paper_width_px=int(layout_section.get("paper_width_px", 384)),
        side_margin_px=int(layout_section.get("side_margin_px", 20)),
        section_gap_px=int(layout_section.get("section_gap_px", 16)),
        image_max_height_px=int(layout_section.get("image_max_height_px", 260)),
        title_font_size=int(layout_section.get("title_font_size", 28)),
        body_font_size=int(layout_section.get("body_font_size", 24)),
        timestamp_font_size=int(layout_section.get("timestamp_font_size", 18)),
        font_path=_resolve_path(base_dir, font_path) if font_path else None,
        threshold=int(layout_section.get("threshold", 160)),
        max_fortune_chars=int(layout_section.get("max_fortune_chars", 100)),
    )

    return AppConfig(
        assets_dir=assets_dir,
        output=output,
        cooldown_seconds=int(payload.get("cooldown_seconds", 60)),
        cooldown_on_trigger=bool(payload.get("cooldown_on_trigger", True)),
        trailing_feed_lines=int(payload.get("trailing_feed_lines", 4)),
        input=trigger_input,
        audio=audio,
        bluetooth=bluetooth,
        llm=llm,
        layout=layout,
    )


def _resolve_path(base_dir: Path, value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return (base_dir / path).resolve()


def _optional_int(value: Any) -> Optional[int]:
    if value in (None, ""):
        return None
    return int(value)


def _optional_str(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    return str(value)


def _optional_path(base_dir: Path, value: Any) -> Optional[Path]:
    if value in (None, ""):
        return None
    return _resolve_path(base_dir, value)


def _resolve_audio_clip_path(assets_dir: Path, value: Any) -> Path:
    path = Path(str(value)).expanduser()
    if path.is_absolute():
        return path
    return (assets_dir / path).resolve()


def _optional_audio_clip_path(assets_dir: Path, value: Any) -> Optional[Path]:
    if value in (None, ""):
        return None
    return _resolve_audio_clip_path(assets_dir, value)


def _clip_volume(value: Any) -> float:
    volume = float(value)
    if volume < 0.0:
        return 0.0
    if volume > 1.0:
        return 1.0
    return volume


def _load_llm_profiles(
    assets_dir: Path,
    llm_section: Any,
) -> tuple[LLMProfileConfig, ...]:
    if isinstance(llm_section, list) and llm_section:
        return tuple(
            _load_llm_profile(
                assets_dir,
                profile_section if isinstance(profile_section, dict) else {},
                index=index,
                default_name=f"profile-{index}",
            )
            for index, profile_section in enumerate(llm_section, start=1)
        )
    if isinstance(llm_section, dict):
        return (
            _load_llm_profile(
                assets_dir,
                llm_section,
                index=1,
                default_name="default",
            ),
        )
    return (
        _load_llm_profile(
            assets_dir,
            {},
            index=1,
            default_name="default",
        ),
    )


def _load_llm_profile(
    assets_dir: Path,
    profile_section: dict[str, Any],
    *,
    index: int,
    default_name: str,
) -> LLMProfileConfig:
    return LLMProfileConfig(
        name=str(profile_section.get("name", default_name)),
        weight=_profile_weight(profile_section.get("weight", 1.0)),
        endpoint=str(
            profile_section.get(
                "endpoint",
                "https://your-llm-endpoint.example/v1/",
            )
        ),
        model=str(profile_section.get("model", "gpt-4.1-mini")),
        system_prompt=str(
            profile_section.get(
                "system_prompt",
                "당신은 짧고 절제된 한국어 하이쿠 운세를 쓰는 조용한 시인이다.",
            )
        ),
        prompt=str(
            profile_section.get(
                "prompt",
                '오늘의 운세를 한국어 하이쿠 형태로 작성해줘. 반드시 JSON 객체 하나만 반환해. 스키마는 {"fortune":"...","tag":"..."} 이고, fortune 값에는 본문만 넣어. 정확히 3행으로, 각 행은 짧고 응축되게 쓰고, 설명문 대신 장면과 전환과 잔향이 남게 작성해.',
            )
        ),
        tags=_tag_asset_map(
            assets_dir,
            profile_section.get("tags", profile_section.get("asset_pools", {})),
        ),
        variation_hints=_string_tuple(profile_section.get("variation_hints", [])),
        current_time_hint_format=str(
            profile_section.get(
                "current_time_hint_format",
                "%Y-%m-%d %H:%M:%S %Z",
            )
        ),
        current_time_hint_pre=str(
            profile_section.get(
                "current_time_hint_pre",
                "이번 운세 기준 시각:",
            )
        ),
        current_time_hint_post=str(
            profile_section.get(
                "current_time_hint_post",
                "위 시각의 공기와 타이밍을 반영하되, 문장을 숫자 나열처럼 쓰지는 마.",
            )
        ),
        cleaned_examples_pre=str(
            profile_section.get(
                "cleaned_examples_pre",
                "최근 출력 예시와 겹치지 말 것:",
            )
        ),
        cleaned_examples_post=str(
            profile_section.get(
                "cleaned_examples_post",
                "위 예시와 첫 행 시작어, 핵심 명사, 계절어, 분위기, 결말 어미를 반복하지 마.",
            )
        ),
        response_json_key=str(profile_section.get("response_json_key", "fortune")),
        response_tag_key=str(profile_section.get("response_tag_key", "tag")),
        enable_thinking=bool(profile_section.get("enable_thinking", False)),
        api_key_env=_optional_str(
            profile_section.get("api_key_env", "SPARK_LLM_API_KEY")
        ),
        temperature=float(profile_section.get("temperature", 0.9)),
        max_tokens=int(profile_section.get("max_tokens", 120)),
        timeout_seconds=float(profile_section.get("timeout_seconds", 25.0)),
        fallback_text=str(
            profile_section.get(
                "fallback_text",
                "잠시 운세를 불러오지 못했어요. 다시 한 번 마음속으로 숨을 고르세요.",
            )
        ),
    )

def _tag_asset_map(
    assets_dir: Path,
    value: Any,
) -> dict[str, tuple[Path, ...]]:
    if not isinstance(value, dict):
        return {}
    tag_asset_map: dict[str, tuple[Path, ...]] = {}
    for raw_tag, raw_paths in value.items():
        tag = str(raw_tag).strip()
        if not tag or not isinstance(raw_paths, list):
            continue
        resolved_paths = []
        for raw_path in raw_paths:
            resolved = _resolve_audio_clip_path(assets_dir, raw_path)
            if resolved.suffix.lower() in {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}:
                resolved_paths.append(resolved)
        tag_asset_map[tag] = tuple(resolved_paths)
    return tag_asset_map


def _string_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    items = []
    for raw_item in value:
        item = str(raw_item).strip()
        if item:
            items.append(item)
    return tuple(items)


def _profile_weight(value: Any) -> float:
    weight = float(value)
    if weight < 0.0:
        return 0.0
    return weight
