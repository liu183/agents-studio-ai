"""Adapter registry (Python mirror of packages/adapters/registry.ts).

Resolution order (per docs/ARCHITECTURE.md s5.4):
    explicit selection > project default > global default > system fallback

M1 pre-registers the mock adapters so the pipeline runs offline. Real adapters
(and custom providers discovered via ``/v1/models``) register the same way.
"""

from __future__ import annotations

from typing import Dict, List

from .base import ImageAdapter, TextAdapter, TTSAdapter, VideoAdapter
from .mock import MockImageAdapter, MockTextAdapter, MockTTSAdapter, MockVideoAdapter

image_adapters: Dict[str, ImageAdapter] = {}
video_adapters: Dict[str, VideoAdapter] = {}
text_adapters: Dict[str, TextAdapter] = {}
tts_adapters: Dict[str, TTSAdapter] = {}

_FALLBACK = {
    "image": "mock-image",
    "video": "mock-video",
    "text": "mock-text",
    "tts": "mock-tts",
}


def register_image(name: str, adapter: ImageAdapter) -> None:
    image_adapters[name.lower()] = adapter


def register_video(name: str, adapter: VideoAdapter) -> None:
    video_adapters[name.lower()] = adapter


def register_text(name: str, adapter: TextAdapter) -> None:
    text_adapters[name.lower()] = adapter


def register_tts(name: str, adapter: TTSAdapter) -> None:
    tts_adapters[name.lower()] = adapter


def register_mocks() -> None:
    register_image(MockImageAdapter.provider, MockImageAdapter())
    register_video(MockVideoAdapter.provider, MockVideoAdapter())
    register_text(MockTextAdapter.provider, MockTextAdapter())
    register_tts(MockTTSAdapter.provider, MockTTSAdapter())


def register_real() -> None:
    """Register the real HTTP adapters (constructed with the default urllib
    transport). Importing them here keeps the mock-only path import-light."""
    from .minimax import MiniMaxTTSAdapter, MiniMaxVideoAdapter
    from .openai_compat import OpenAICompatImageAdapter, OpenAICompatTextAdapter
    from .volcengine import SeedanceVideoAdapter, SeedreamImageAdapter

    register_image("seedream", SeedreamImageAdapter())
    register_image("openai", OpenAICompatImageAdapter())
    register_video("seedance", SeedanceVideoAdapter())
    register_video("minimax_video", MiniMaxVideoAdapter())
    register_text("openai_compat", OpenAICompatTextAdapter())
    register_tts("minimax", MiniMaxTTSAdapter())


def get_image(provider: str = "") -> ImageAdapter:
    return image_adapters.get(provider.lower()) or image_adapters[_FALLBACK["image"]]


def get_video(provider: str = "") -> VideoAdapter:
    return video_adapters.get(provider.lower()) or video_adapters[_FALLBACK["video"]]


def get_text(provider: str = "") -> TextAdapter:
    return text_adapters.get(provider.lower()) or text_adapters[_FALLBACK["text"]]


def get_tts(provider: str = "") -> TTSAdapter:
    return tts_adapters.get(provider.lower()) or tts_adapters[_FALLBACK["tts"]]


def list_providers() -> Dict[str, List[str]]:
    return {
        "image": sorted(image_adapters),
        "video": sorted(video_adapters),
        "text": sorted(text_adapters),
        "tts": sorted(tts_adapters),
    }


# Pre-register mocks + real adapters on import so the M1 CLI works out of the
# box offline (mock) and uses real providers automatically when configured.
register_mocks()
register_real()
