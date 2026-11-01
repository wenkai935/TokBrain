from __future__ import annotations


class PlatformPullError(Exception):
    """Raised when a platform collect call fails (auth, risk control, network)."""

    def __init__(self, code: str, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
