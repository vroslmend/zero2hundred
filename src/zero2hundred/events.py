from __future__ import annotations

from dataclasses import dataclass

from zero2hundred.errors import MediaError


@dataclass(frozen=True, slots=True)
class EventWindow:
    launch: float
    reached_100: float
    launch_confidence: float | None = None
    end_confidence: float | None = None

    @property
    def elapsed(self) -> float:
        return self.reached_100 - self.launch

    def validate(self, duration: float) -> "EventWindow":
        if self.launch < 0:
            raise MediaError("launch timestamp cannot be negative")
        if self.reached_100 <= self.launch:
            raise MediaError("100 km/h timestamp must be after the launch")
        if self.reached_100 >= duration:
            raise MediaError(
                f"100 km/h timestamp ({self.reached_100:.3f}s) must be before "
                f"the video ends ({duration:.3f}s)"
            )
        return self

