"""OpenAI-compatible adapters (chat completions + image generations).

Works against any OpenAI-compatible endpoint: OpenAI, Azure OpenAI, Qwen,
DeepSeek, Moonshot, vLLM, etc. -- only ``base_url`` / ``model`` change. This is
the ``openai_compat`` provider referenced in packages/adapters/registry.ts.

Endpoints (relative to config.base_url, default https://api.openai.com/v1):
    POST /chat/completions     -> { choices:[{message:{content}}], usage:{...} }
    POST /images/generations   -> { data:[{url}|{b64_json}] }
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import AIConfig, ImageRequest, MediaResult, ProviderError, ProviderRequest, TextRequest
from .http import ImageHttpAdapter, TextHttpAdapter

DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _base(config: AIConfig) -> str:
    return (config.base_url or DEFAULT_BASE_URL).rstrip("/")


class OpenAICompatTextAdapter(TextHttpAdapter):
    provider = "openai_compat"
    supports_vision = True
    supports_structured_output = True

    def build_request(self, config: AIConfig, req: TextRequest) -> ProviderRequest:
        messages: List[Dict[str, str]] = []
        if req.system_prompt:
            messages.append({"role": "system", "content": req.system_prompt})
        messages.extend(req.messages)
        body: Dict[str, Any] = {
            "model": config.model or "gpt-4o-mini",
            "messages": messages,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        }
        return ProviderRequest(
            url=f"{_base(config)}/chat/completions",
            headers=self._auth_headers(config),
            body=body,
        )

    def parse_response(self, raw: Any) -> MediaResult:
        try:
            text = raw["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected chat response: {raw!r:.200}",
                                provider=self.provider) from exc
        usage = raw.get("usage", {}) if isinstance(raw, dict) else {}
        prompt_tok = usage.get("prompt_tokens", 0)
        completion_tok = usage.get("completion_tokens", 0)
        # rough blended estimate: $0.5 / 1M tokens (mock-grade accounting)
        cost = round((prompt_tok + completion_tok) * 0.0000005, 6)
        return MediaResult(data=text, data_encoding="", cost=cost,
                           meta={"usage": usage})


class OpenAICompatImageAdapter(ImageHttpAdapter):
    provider = "openai"  # registry fallback name for image

    def build_generate_request(self, config: AIConfig, req: ImageRequest) -> ProviderRequest:
        body: Dict[str, Any] = {
            "model": config.model or "gpt-image-1",
            "prompt": req.prompt,
            "n": 1,
            "size": config.extras.get("size", req.size),
            "response_format": config.extras.get("response_format", "url"),
        }
        return ProviderRequest(
            url=f"{_base(config)}/images/generations",
            headers=self._auth_headers(config),
            body=body,
        )

    def parse_generate_response(self, raw: Any) -> MediaResult:
        try:
            item = raw["data"][0]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected image response: {raw!r:.200}",
                                provider=self.provider) from exc
        if item.get("url"):
            return MediaResult(url=item["url"], cost=0.04, meta={"provider": self.provider})
        if item.get("b64_json"):
            return MediaResult(data=item["b64_json"], data_encoding="base64", cost=0.04,
                               meta={"provider": self.provider})
        raise ProviderError("image response had neither url nor b64_json",
                            provider=self.provider)
