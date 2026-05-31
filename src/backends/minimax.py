"""MiniMax adapters: T2A v2 text-to-speech + Hailuo video generation.

Base URL (international): https://api.minimax.io/v1  (China: api.minimaxi.com).
T2A v2 and file retrieval take ``GroupId`` as a query parameter; store it in
config.extras["group_id"].

- TTS (synchronous), returns hex-encoded audio inline:
      POST /t2a_v2?GroupId=...  -> { data:{audio:<hex>}, extra_info:{audio_length} }
- Video (asynchronous), 3 steps:
      POST /video_generation                     -> { task_id }
      GET  /query/video_generation?task_id=...    -> { status, file_id }
      GET  /files/retrieve?GroupId=&file_id=...   -> { file:{download_url} }
"""

from __future__ import annotations

from typing import Any, Dict

from .base import (
    AIConfig,
    MediaResult,
    ProviderError,
    ProviderRequest,
    TTSRequest,
    VideoRequest,
)
from .http import TTSHttpAdapter, VideoHttpAdapter

MINIMAX_BASE_URL = "https://api.minimax.io/v1"


def _base(config: AIConfig) -> str:
    return (config.base_url or MINIMAX_BASE_URL).rstrip("/")


def _check_base_resp(raw: Any, provider: str) -> None:
    base_resp = raw.get("base_resp") if isinstance(raw, dict) else None
    if base_resp and base_resp.get("status_code") not in (0, None):
        raise ProviderError(
            f"{provider}: {base_resp.get('status_msg')}",
            status=base_resp.get("status_code"), provider=provider,
        )


class MiniMaxTTSAdapter(TTSHttpAdapter):
    provider = "minimax"
    supports_voice_cloning = True
    supports_emotion = True
    default_model = "speech-02-hd"

    def build_generate_request(self, config: AIConfig, req: TTSRequest) -> ProviderRequest:
        voice_setting: Dict[str, Any] = {
            "voice_id": req.voice_id,
            "speed": req.speed,
            "vol": config.extras.get("vol", 1.0),
            "pitch": config.extras.get("pitch", 0),
        }
        if req.emotion and req.emotion != "neutral":
            voice_setting["emotion"] = req.emotion
        body = {
            "model": config.model or self.default_model,
            "text": req.text,
            "stream": False,
            "voice_setting": voice_setting,
            "audio_setting": {
                "sample_rate": config.extras.get("sample_rate", 32000),
                "bitrate": config.extras.get("bitrate", 128000),
                "format": req.fmt if req.fmt in ("mp3", "pcm", "flac") else "mp3",
                "channel": 1,
            },
        }
        query = {}
        if config.extras.get("group_id"):
            query["GroupId"] = str(config.extras["group_id"])
        return ProviderRequest(
            url=f"{_base(config)}/t2a_v2",
            headers=self._auth_headers(config),
            body=body,
            query=query,
        )

    def parse_response(self, raw: Any) -> MediaResult:
        _check_base_resp(raw, self.provider)
        data = raw.get("data") or {} if isinstance(raw, dict) else {}
        audio_hex = data.get("audio")
        if not audio_hex:
            raise ProviderError(f"minimax tts: no audio in {raw!r:.200}", provider=self.provider)
        extra = raw.get("extra_info") or {}
        duration = round(extra.get("audio_length", 0) / 1000.0, 2)  # ms -> s
        return MediaResult(
            data=audio_hex, data_encoding="hex", duration_sec=duration, cost=0.1,
            meta={"format": extra.get("audio_format"), "size": extra.get("audio_size")},
        )


class MiniMaxVideoAdapter(VideoHttpAdapter):
    """Hailuo image/text-to-video. 3-step async: submit -> query -> retrieve."""

    provider = "minimax_video"
    supported_modes = ["image2video", "first_last"]
    default_model = "MiniMax-Hailuo-02"

    def build_generate_request(self, config: AIConfig, req: VideoRequest) -> ProviderRequest:
        body: Dict[str, Any] = {
            "model": config.model or self.default_model,
            "prompt": self.compose_prompt(req),
        }
        frame = req.first_frame_url or req.image_url
        if frame:
            body["first_frame_image"] = frame
        return ProviderRequest(
            url=f"{_base(config)}/video_generation",
            headers=self._auth_headers(config),
            body=body,
        )

    def parse_generate_response(self, raw: Any) -> MediaResult:
        _check_base_resp(raw, self.provider)
        task_id = raw.get("task_id") if isinstance(raw, dict) else None
        if not task_id:
            raise ProviderError(f"minimax video: no task_id in {raw!r:.200}",
                                provider=self.provider)
        return MediaResult(is_async=True, task_id=task_id, cost=5.0, meta={"status": "queueing"})

    def build_poll_request(self, config: AIConfig, task_id: str) -> ProviderRequest:
        return ProviderRequest(
            url=f"{_base(config)}/query/video_generation",
            method="GET",
            headers=self._auth_headers(config),
            query={"task_id": task_id},
        )

    def parse_poll_response(self, raw: Any) -> MediaResult:
        status = (raw.get("status") or "").lower() if isinstance(raw, dict) else "fail"
        return MediaResult(is_async=True, meta={"status": status, "file_id": raw.get("file_id")})

    def build_retrieve_request(self, config: AIConfig, file_id: str) -> ProviderRequest:
        query = {"file_id": str(file_id)}
        if config.extras.get("group_id"):
            query["GroupId"] = str(config.extras["group_id"])
        return ProviderRequest(
            url=f"{_base(config)}/files/retrieve",
            method="GET",
            headers=self._auth_headers(config),
            query=query,
        )

    def parse_retrieve_response(self, raw: Any) -> MediaResult:
        file_info = raw.get("file") or {} if isinstance(raw, dict) else {}
        url = file_info.get("download_url")
        if not url:
            raise ProviderError(f"minimax video: no download_url in {raw!r:.200}",
                                provider=self.provider)
        return MediaResult(url=url, meta={"status": "success"})

    def generate(self, config: AIConfig, request: VideoRequest) -> MediaResult:
        submitted = self.parse_generate_response(
            self.transport.send_json(self.build_generate_request(config, request))
        )
        for _ in range(self.max_polls):
            polled = self.parse_poll_response(
                self.transport.send_json(self.build_poll_request(config, submitted.task_id))
            )
            status = polled.meta.get("status")
            if status in ("success", "succeeded"):
                retrieved = self.parse_retrieve_response(
                    self.transport.send_json(
                        self.build_retrieve_request(config, polled.meta.get("file_id"))
                    )
                )
                retrieved.task_id = submitted.task_id
                retrieved.cost = submitted.cost
                return retrieved
            if status in ("fail", "failed", "error"):
                raise ProviderError(f"minimax video task {submitted.task_id} failed",
                                    provider=self.provider)
            self._sleep(self.poll_interval)
        raise ProviderError(f"minimax video task {submitted.task_id} timed out",
                            provider=self.provider)
