class Zero2HundredError(Exception):
    """Base exception for expected application errors."""


class DependencyError(Zero2HundredError):
    """Raised when an external runtime dependency is unavailable."""


class MediaError(Zero2HundredError):
    """Raised when input media cannot be inspected or processed."""


class ConfigurationError(Zero2HundredError):
    """Raised when configuration is invalid."""

