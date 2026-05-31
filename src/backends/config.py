"""Environment-driven provider resolution.

Implements docs/ARCHITECTURE.md s5.4 resolution order in the form available to a
CLI: explicit selection (CLI flag) > env override > system default. If the
resolved real provider has no credentials configured, we transparently fall
back to the offline mock adapter -- so the pipeline always runs, using real
providers only when they are actually set up.

Recognised environment variables
---------------------------------
Selection (optional):
    STUDIO_IMAGE_PROVIDER / STUDIO_VIDEO_PROVIDER /
    STUDIO_TEXT_PROVIDER  / STUDIO_TTS_PROVIDER
Volcengine Ark (Seedream image, Seedance video):
    ARK_API_KEY, ARK_BASE_URL, ARK_IMAGE_MODEL, ARK_VIDEO_MODEL
OpenAI-compatible (text + image):
    OPENAI_API_KEY, OPENAI_BASE_URL, OPENAI_TEXT_MODEL, OPENAI_IMAGE_MODEL
MiniMax (TTS + Hailuo video):
    MINIMAX_API_KEY, MINIMAX_GROUP_ID, MINIMAX_BASE_URL,
    MINIMAX_TTS_MODEL, MINIMAX_VIDEO_MODEL
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

from . import registry
from .base import AIConfig

DEFAULTS = {"image": "seedream", "video": "seedance",
            "text": "openai_compat", "tts": "minimax"}
MOCKS = {"image": "mock-image", "video": "mock-video",
         "text": "mock-text", "tts": "mock-tts"}

_SELECTION_ENV = {
    "image": "STUDIO_IMAGE_PROVIDER",
    "video": "STUDIO_VIDEO_PROVIDER",
    "text": "STUDIO_TEXT_PROVIDER",
    "tts": "STUDIO_TTS_PROVIDER",
}


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def build_config(provider: str, media: str) -> AIConfig:
    """Build an AIConfig for a known real provider from environment variables."""
    name = provider.lower()
    if name in ("seedream", "seedance", "volcengine", "volcengine_seedance"):
        model = _env("ARK_IMAGE_MODEL") if media == "image" else _env("ARK_VIDEO_MODEL")
        return AIConfig(provider=name, api_key=_env("ARK_API_KEY"),
                        base_url=_env("ARK_BASE_URL"), model=model,
                        extras={"watermark": False})
    if name in ("openai", "openai_compat"):
        model = _env("OPENAI_IMAGE_MODEL") if media == "image" else _env("OPENAI_TEXT_MODEL")
        return AIConfig(provider=name, api_key=_env("OPENAI_API_KEY"),
                        base_url=_env("OPENAI_BASE_URL"), model=model)
    if name in ("minimax", "minimax_video"):
        model = _env("MINIMAX_TTS_MODEL") if media == "tts" else _env("MINIMAX_VIDEO_MODEL")
        extras = {}
        if _env("MINIMAX_GROUP_ID"):
            extras["group_id"] = _env("MINIMAX_GROUP_ID")
        return AIConfig(provider=name, api_key=_env("MINIMAX_API_KEY"),
                        base_url=_env("MINIMAX_BASE_URL"), model=model, extras=extras)
    return AIConfig(provider=name)


_GETTERS = {
    "image": registry.get_image,
    "video": registry.get_video,
    "text": registry.get_text,
    "tts": registry.get_tts,
}
_TABLES = {
    "image": registry.image_adapters,
    "video": registry.video_adapters,
    "text": registry.text_adapters,
    "tts": registry.tts_adapters,
}


def _resolve(media: str, explicit: Optional[str]) -> Tuple[object, AIConfig]:
    name = (explicit or _env(_SELECTION_ENV[media]) or DEFAULTS[media]).lower()
    mock_name = MOCKS[media]

    # explicit mock or unknown provider -> mock
    if name.startswith("mock") or name not in _TABLES[media]:
        return _GETTERS[media](mock_name), AIConfig(provider=mock_name)

    config = build_config(name, media)
    if not config.api_key:
        # real provider selected but not configured -> stay offline on mock
        return _GETTERS[media](mock_name), AIConfig(provider=mock_name)
    return _TABLES[media][name], config


def resolve_image(explicit: Optional[str] = None) -> Tuple[object, AIConfig]:
    return _resolve("image", explicit)


def resolve_video(explicit: Optional[str] = None) -> Tuple[object, AIConfig]:
    return _resolve("video", explicit)


def resolve_text(explicit: Optional[str] = None) -> Tuple[object, AIConfig]:
    return _resolve("text", explicit)


def resolve_tts(explicit: Optional[str] = None) -> Tuple[object, AIConfig]:
    return _resolve("tts", explicit)


def active_providers() -> dict:
    """Report which provider each media type will actually use right now."""
    out = {}
    for media in ("image", "video", "text", "tts"):
        adapter, config = _resolve(media, None)
        out[media] = config.provider
    return out
