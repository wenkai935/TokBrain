from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class PlatformVideoItem:
    """Normalized video pulled from a platform.

    Ported conceptually from zhicui-desktop ``platform-account.js``
    ``normalizeDouyinRecord`` output shape, generalized to all three platforms.
    """

    platform: str
    platform_work_id: str
    source_url: str
    title: Optional[str] = None
    author_name: Optional[str] = None
    cover_url: Optional[str] = None
    published_at: Optional[str] = None
    duration_seconds: Optional[float] = None
    kind: str = "video"
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
