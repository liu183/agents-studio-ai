"""Mock provider adapters.

These produce deterministic placeholder results without any network access, so
the whole pipeline can be exercised end-to-end offline. They estimate a small
cost per call so the orchestrator's cost gate has realistic numbers to report.
"""

from __future__ import annotations

import hashlib

from .base import (
    AIConfig,
    ImageRequest,
    MediaResult,
    TextRequest,
    TTSRequest,
    VideoRequest,
)


def _hash(*parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return digest[:12]


class MockImageAdapter:
    provider = "mock-image"

    def generate(self, config: AIConfig, request: ImageRequest) -> MediaResult:
        token = _hash(request.prompt, request.size, request.frame_type)
        return MediaResult(
            is_async=False,
            url=f"mock://image/{token}.png",
            cost=0.5,
            meta={"size": request.size, "frame_type": request.frame_type},
        )


class MockVideoAdapter:
    provider = "mock-video"
    supported_modes = ["image2video", "first_last", "grid", "reference_video", "multi_shot"]

    def generate(self, config: AIConfig, request: VideoRequest) -> MediaResult:
        token = _hash(request.prompt, request.generation_mode, request.resolution)
        return MediaResult(
            is_async=False,
            url=f"mock://video/{token}.mp4",
            duration_sec=request.duration,
            cost=round(7.5 * (request.duration / 5.0), 2),
            meta={"mode": request.generation_mode, "aspect_ratio": request.aspect_ratio},
        )


class MockTextAdapter:
    provider = "mock-text"

    def complete(self, config: AIConfig, request: TextRequest) -> MediaResult:
        last = request.messages[-1]["content"] if request.messages else ""
        return MediaResult(
            is_async=False,
            data=f"[mock-text:{_hash(last)}]",
            cost=0.05,
            meta={"chars": len(last)},
        )


class MockTTSAdapter:
    provider = "mock-tts"

    def synthesize(self, config: AIConfig, request: TTSRequest) -> MediaResult:
        token = _hash(request.voice_id, request.text)
        # ~3.3 chars per second of Mandarin speech is a reasonable rough rate.
        duration = max(1.0, round(len(request.text) / 3.3, 1))
        return MediaResult(
            is_async=False,
            url=f"mock://audio/{token}.wav",
            duration_sec=duration,
            cost=0.1,
            meta={"voice_id": request.voice_id, "emotion": request.emotion},
        )
