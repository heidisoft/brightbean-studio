"""Configured media upload limits used before publishing to providers."""

from __future__ import annotations

from dataclasses import dataclass


MB = 1024 * 1024
GB = 1024 * MB


@dataclass(frozen=True)
class MediaUploadLimit:
    image: int | None = None
    video: int | None = None


# These are the app's configured preflight limits. Some upstream limits vary by
# account tier or instance; keep this table conservative and aligned with what
# our provider implementations can currently upload.
PLATFORM_MEDIA_UPLOAD_LIMITS: dict[str, MediaUploadLimit] = {
    "facebook": MediaUploadLimit(image=30 * MB, video=4 * GB),
    "instagram": MediaUploadLimit(image=8 * MB, video=4 * GB),
    "instagram_login": MediaUploadLimit(image=8 * MB, video=4 * GB),
    "linkedin_personal": MediaUploadLimit(image=5 * MB, video=5 * GB),
    "linkedin_company": MediaUploadLimit(image=5 * MB, video=5 * GB),
    "tiktok": MediaUploadLimit(video=64_000_000),
    "youtube": MediaUploadLimit(image=2 * MB, video=256 * GB),
    "pinterest": MediaUploadLimit(image=32 * MB, video=2 * GB),
    "threads": MediaUploadLimit(image=8 * MB, video=1 * GB),
    "bluesky": MediaUploadLimit(image=1 * MB, video=100 * MB),
    "google_business": MediaUploadLimit(image=5 * MB, video=75 * MB),
    "mastodon": MediaUploadLimit(image=40 * MB, video=40 * MB),
}


def get_media_upload_limit(platform: str, media_type: str) -> int | None:
    """Return the configured max bytes for platform/media_type, if known."""
    limits = PLATFORM_MEDIA_UPLOAD_LIMITS.get(platform)
    if not limits:
        return None
    if media_type == "image":
        return limits.image
    if media_type == "video":
        return limits.video
    return None


def format_bytes(size: int) -> str:
    """Format bytes for short validation messages."""
    if size >= 1_000_000 and size % 1_000_000 == 0:
        return f"{size // 1_000_000} MB"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            if unit == "B":
                return f"{int(value)} {unit}"
            if value >= 10:
                return f"{value:.0f} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
