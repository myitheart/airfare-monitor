"""Domain exceptions."""


class AirfareMonitorError(Exception):
    """Base exception for expected application failures."""


class ConfigError(AirfareMonitorError):
    """Raised when local configuration is missing or invalid."""


class CollectionError(AirfareMonitorError):
    """Raised when a route cannot be collected completely."""


class IncompleteResponseError(CollectionError):
    """Raised when collection times out before the completed response."""


class ManualAttentionRequired(CollectionError):
    """Raised when CAPTCHA or device verification requires a human."""


class ParseError(CollectionError):
    """Raised when a completed payload does not match the supported schema."""
