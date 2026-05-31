"""Provider adapter protocols (Python mirror of packages/adapters/types.ts).

Each media kind (image / video / text / tts) has a small adapter protocol.
Adding a provider == implementing one adapter + registering one line, exactly
like the TypeScript design in packages/adapters/. M1 ships mock adapters only;
real HTTP-backed adapters arrive in later milestones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@dataclass
class AIConfig:
    provider: str
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageRequest:
    prompt: str
    size: str = "1024x1024"
    frame_type: str = "standalone"  # start / end / grid_4 / grid_6 / grid_9 / standalone
    reference_images: List[str] = field(default_factory=list)
    identity_anchors: Dict[str, str] = field(default_factory=dict)
    negative_prompt: str = ""


@dataclass
class VideoRequest:
    prompt: str
    generation_mode: str = "image2video"  # image2video/first_last/grid/reference_video/multi_shot
    image_url: Optional[str] = None
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    reference_image_urls: List[str] = field(default_factory=list)
    duration: float = 5.0
    aspect_ratio: str = "9:16"
    resolution: str = "1080p"


@dataclass
class TextRequest:
    system_prompt: str = ""
    messages: List[Dict[str, str]] = field(default_factory=list)
    temperature: float = 0.7
    max_tokens: int = 2048


@dataclass
class TTSRequest:
    voice_id: str
    text: str
    emotion: str = "neutral"
    speed: float = 1.0
    fmt: str = "wav"


@dataclass
class MediaResult:
    """Unified async-or-sync result envelope."""
    is_async: bool = False
    task_id: Optional[str] = None
    url: Optional[str] = None
    data: Optional[str] = None  # base64 / hex inline payload
    duration_sec: float = 0.0
    cost: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class ImageAdapter(Protocol):
    provider: str

    def generate(self, config: AIConfig, request: ImageRequest) -> MediaResult: ...


@runtime_checkable
class VideoAdapter(Protocol):
    provider: str
    supported_modes: List[str]

    def generate(self, config: AIConfig, request: VideoRequest) -> MediaResult: ...


@runtime_checkable
class TextAdapter(Protocol):
    provider: str

    def complete(self, config: AIConfig, request: TextRequest) -> MediaResult: ...


@runtime_checkable
class TTSAdapter(Protocol):
    provider: str

    def synthesize(self, config: AIConfig, request: TTSRequest) -> MediaResult: ...
