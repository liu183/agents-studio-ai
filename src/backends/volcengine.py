"""Volcengine Ark adapters: Seedream (image) + Seedance (video).

Ark base URL (China): https://ark.cn-beijing.volces.com/api/v3
(BytePlus international uses a different host; set it via config.base_url.)

- Seedream image is synchronous and OpenAI-compatible:
      POST {base}/images/generations  -> { data:[{url}|{b64_json}] }
- Seedance video is asynchronous (submit task, then poll):
      POST {base}/contents/generations/tasks      -> { id }
      GET  {base}/contents/generations/tasks/{id} -> { status, content:{video_url} }

Seedance takes generation params as ``--key value`` suffixes appended to the
text prompt (resolution / ratio / duration), and reference frames as typed
``image_url`` content items carrying a ``role`` (first_frame / last_frame).
References: Volcengine Ark Seedream/Seedance API docs (shapes evolve; the
build/parse split keeps this easy to adjust).
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AIConfig, ImageRequest, MediaResult, ProviderError, ProviderRequest, VideoRequest
from .http import VideoHttpAdapter
from .openai_compat import OpenAICompatImageAdapter

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"


def _ark_base(config: AIConfig) -> str:
    return (config.base_url or ARK_BASE_URL).rstrip("/")


class SeedreamImageAdapter(OpenAICompatImageAdapter):
    """Seedream image generation/editing (OpenAI-compatible images endpoint)."""

    provider = "seedream"
    default_model = "doubao-seedream-4-0-250828"

    def build_generate_request(self, config: AIConfig, req: ImageRequest) -> ProviderRequest:
        body: Dict[str, Any] = {
            "model": config.model or self.default_model,
            "prompt": req.prompt,
            "size": config.extras.get("size", req.size),
            "response_format": config.extras.get("response_format", "url"),
            "watermark": config.extras.get("watermark", False),
        }
        if req.reference_images:
            # Seedream 4 image-to-image / multi-image fusion.
            body["image"] = (
                req.reference_images[0]
                if len(req.reference_images) == 1
                else req.reference_images
            )
        if "seed" in config.extras:
            body["seed"] = config.extras["seed"]
        return ProviderRequest(
            url=f"{_ark_base(config)}/images/generations",
            headers=self._auth_headers(config),
            body=body,
        )


class SeedanceVideoAdapter(VideoHttpAdapter):
    """Seedance text/image-to-video with first/last-frame control (async)."""

    provider = "seedance"
    supported_modes = ["image2video", "first_last", "multi_shot", "reference_video"]
    default_model = "doubao-seedance-1-0-pro-250528"

    def compose_prompt(self, req: VideoRequest) -> str:
        suffix = (
            f" --resolution {req.resolution}"
            f" --ratio {req.aspect_ratio}"
            f" --duration {int(req.duration)}"
        )
        return f"{req.prompt}{suffix}"

    def build_generate_request(self, config: AIConfig, req: VideoRequest) -> ProviderRequest:
        content: List[Dict[str, Any]] = [{"type": "text", "text": self.compose_prompt(req)}]
        if req.generation_mode == "first_last":
            if req.first_frame_url:
                content.append({"type": "image_url", "role": "first_frame",
                                "image_url": {"url": req.first_frame_url}})
            if req.last_frame_url:
                content.append({"type": "image_url", "role": "last_frame",
                                "image_url": {"url": req.last_frame_url}})
        else:
            frame = req.first_frame_url or req.image_url
            if frame:
                content.append({"type": "image_url", "role": "first_frame",
                                "image_url": {"url": frame}})
            for ref in req.reference_image_urls:
                content.append({"type": "image_url", "role": "reference_image",
                                "image_url": {"url": ref}})
        body = {"model": config.model or self.default_model, "content": content}
        return ProviderRequest(
            url=f"{_ark_base(config)}/contents/generations/tasks",
            headers=self._auth_headers(config),
            body=body,
        )

    def parse_generate_response(self, raw: Any) -> MediaResult:
        task_id = raw.get("id") if isinstance(raw, dict) else None
        if not task_id:
            raise ProviderError(f"seedance: no task id in {raw!r:.200}", provider=self.provider)
        # cost is billed at completion; rough placeholder for accounting.
        return MediaResult(is_async=True, task_id=task_id, cost=7.5,
                           meta={"status": "queued"})

    def build_poll_request(self, config: AIConfig, task_id: str) -> ProviderRequest:
        return ProviderRequest(
            url=f"{_ark_base(config)}/contents/generations/tasks/{task_id}",
            method="GET",
            headers=self._auth_headers(config),
        )

    def parse_poll_response(self, raw: Any) -> MediaResult:
        status = (raw.get("status") or "").lower() if isinstance(raw, dict) else "failed"
        content = raw.get("content") or {} if isinstance(raw, dict) else {}
        video_url = content.get("video_url")
        usage = raw.get("usage") or {}
        return MediaResult(
            is_async=True,
            url=video_url,
            meta={"status": status, "error": raw.get("error"),
                  "tokens": usage.get("total_tokens")},
        )
