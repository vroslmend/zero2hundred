from __future__ import annotations

from dataclasses import dataclass, fields, replace
from pathlib import Path
import tomllib

from zero2hundred.errors import ConfigurationError


POSITIONS = (
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
)
TIMER_STYLES = ("stopwatch", "hms")
TIMER_FORMATS = ("seconds", "stopwatch")
OVERLAY_STYLES = ("type-only", "quiet-plate", "compact")


@dataclass(frozen=True, slots=True)
class RenderSettings:
    freeze_duration: float = 2.0
    continue_after_freeze: bool = True
    position: str = "bottom-center"
    overlay_style: str = "type-only"
    bottom_clearance_ratio: float = 0.16
    overlay_scale: float = 1.0
    timer_format: str = "seconds"
    timer_style: str | None = None
    timer_label: str = "0–100 km/h"
    panel_color: str = "black@0.58"
    accent_color: str = "white@0.22"
    font: str = "Manrope"
    font_file: str | None = None
    font_size_ratio: float = 0.065
    margin_ratio: float = 0.04
    text_color: str = "white"
    border_color: str = "black"
    border_width: int = 1
    video_encoder: str = "libx264"
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"

    def validated(self) -> "RenderSettings":
        if self.freeze_duration < 0:
            raise ConfigurationError("freeze_duration cannot be negative")
        if not isinstance(self.continue_after_freeze, bool):
            raise ConfigurationError("continue_after_freeze must be true or false")
        if self.position not in POSITIONS:
            allowed = ", ".join(POSITIONS)
            raise ConfigurationError(f"position must be one of: {allowed}")
        if self.overlay_style not in OVERLAY_STYLES:
            allowed = ", ".join(OVERLAY_STYLES)
            raise ConfigurationError(f"overlay_style must be one of: {allowed}")
        if self.timer_format not in TIMER_FORMATS:
            allowed = ", ".join(TIMER_FORMATS)
            raise ConfigurationError(f"timer_format must be one of: {allowed}")
        if self.timer_style is not None and self.timer_style not in TIMER_STYLES:
            allowed = ", ".join(TIMER_STYLES)
            raise ConfigurationError(f"timer_style must be one of: {allowed}")
        if not 0 <= self.bottom_clearance_ratio <= 0.5:
            raise ConfigurationError("bottom_clearance_ratio must be between 0 and 0.5")
        if not 0.5 <= self.overlay_scale <= 2.0:
            raise ConfigurationError("overlay_scale must be between 0.5 and 2.0")
        if not isinstance(self.timer_label, str) or not self.timer_label.strip():
            raise ConfigurationError("timer_label cannot be empty")
        if not isinstance(self.panel_color, str) or not self.panel_color.strip():
            raise ConfigurationError("panel_color cannot be empty")
        if not isinstance(self.accent_color, str) or not self.accent_color.strip():
            raise ConfigurationError("accent_color cannot be empty")
        if not 0.01 <= self.font_size_ratio <= 0.5:
            raise ConfigurationError("font_size_ratio must be between 0.01 and 0.5")
        if not 0 <= self.margin_ratio <= 0.5:
            raise ConfigurationError("margin_ratio must be between 0 and 0.5")
        if self.border_width < 0:
            raise ConfigurationError("border_width cannot be negative")
        if not 0 <= self.crf <= 51:
            raise ConfigurationError("crf must be between 0 and 51")
        return self


def load_settings(path: Path | None) -> RenderSettings:
    settings = RenderSettings()
    if path is None:
        return settings.validated()

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigurationError(f"could not read config file: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError(f"invalid TOML in {path}: {exc}") from exc

    if "render" in data:
        data = data["render"]
    if not isinstance(data, dict):
        raise ConfigurationError("configuration must contain a TOML table")

    valid_names = {field.name for field in fields(RenderSettings)}
    unknown = sorted(set(data) - valid_names)
    if unknown:
        raise ConfigurationError(f"unknown configuration option: {unknown[0]}")

    try:
        return replace(settings, **data).validated()
    except TypeError as exc:
        raise ConfigurationError(f"invalid configuration value: {exc}") from exc
