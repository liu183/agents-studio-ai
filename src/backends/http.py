"""Transports + abstract HTTP adapter bases.

Design goal: every real adapter is split into pure ``build_*_request`` /
``parse_*_response`` methods (no I/O) plus a ``Transport`` that performs the
actual HTTP. That seam makes adapters fully unit-testable offline -- tests feed
a ``FakeTransport`` with canned provider JSON and assert on the parsed result,
never touching the network. The sandbox is offline, so this is the only way to
verify real adapters here.
"""

from __future__ import annotations

import abc
import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, List, Optional

from .base import (
    AIConfig,
    ImageRequest,
    MediaResult,
    ProviderError,
    ProviderRequest,
    TextRequest,
    TTSRequest,
    VideoRequest,
)


# --------------------------------------------------------------------------- #
# transports
# --------------------------------------------------------------------------- #
class UrllibTransport:
    """Real transport built on the stdlib ``urllib`` (no third-party deps)."""

    def send_json(self, request: ProviderRequest, timeout: float = 60.0) -> Any:
        url = request.url
        if request.query:
            sep = "&" if "?" in url else "?"
            url = url + sep + urllib.parse.urlencode(request.query)
        headers = dict(request.headers)
        data: Optional[bytes] = None
        if request.body is not None:
            headers.setdefault("Content-Type", "application/json")
            data = json.dumps(request.body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers,
                                     method=request.method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:  # pragma: no cover - needs network
            body = exc.read().decode("utf-8", "replace")
            raise ProviderError(f"HTTP {exc.code}: {body[:500]}", status=exc.code,
                                payload=body) from exc
        except urllib.error.URLError as exc:  # pragma: no cover - needs network
            raise ProviderError(f"network error: {exc.reason}") from exc
        text = raw.decode("utf-8", "replace").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"non-JSON response: {text[:200]}", payload=text) from exc

    def download(self, url: str, timeout: float = 120.0) -> bytes:  # pragma: no cover
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read()


class FakeTransport:
    """Deterministic in-memory transport for tests.

    Feed it a queue of canned JSON responses (or a router callable). Records
    every request it receives so tests can assert on URL / headers / body.
    """

    def __init__(self, responses: Optional[List[Any]] = None,
                 router: Optional[Callable[[ProviderRequest], Any]] = None,
                 downloads: Optional[dict] = None):
        self._responses = list(responses or [])
        self._router = router
        self._downloads = downloads or {}
        self.requests: List[ProviderRequest] = []

    def send_json(self, request: ProviderRequest, timeout: float = 60.0) -> Any:
        self.requests.append(request)
        if self._router is not None:
            return self._router(request)
        if not self._responses:
            raise ProviderError("FakeTransport: no canned response left")
        return self._responses.pop(0)

    def download(self, url: str, timeout: float = 120.0) -> bytes:
        return self._downloads.get(url, b"FAKE-BYTES:" + url.encode("utf-8"))


# --------------------------------------------------------------------------- #
# media bytes helper
# --------------------------------------------------------------------------- #
def fetch_media_bytes(result: MediaResult, transport: "Transport") -> Optional[bytes]:
    """Resolve a MediaResult to raw bytes (inline-decoded or downloaded).

    Returns None when there is nothing real to fetch (e.g. a ``mock://`` URL),
    so callers can fall back to writing a placeholder.
    """
    if result.data:
        if result.data_encoding == "hex":
            return bytes.fromhex(result.data)
        if result.data_encoding == "base64":
            return base64.b64decode(result.data)
        return result.data.encode("utf-8")
    if result.url and result.url.startswith(("http://", "https://")):
        return transport.download(result.url)
    return None


# --------------------------------------------------------------------------- #
# abstract HTTP adapter bases
# --------------------------------------------------------------------------- #
class _BaseHttpAdapter:
    provider: str = "http"

    def __init__(self, transport: Optional["Transport"] = None):
        self.transport: "Transport" = transport or UrllibTransport()

    def _auth_headers(self, config: AIConfig) -> dict:
        headers = {"Content-Type": "application/json"}
        if config.api_key:
            headers["Authorization"] = f"Bearer {config.api_key}"
        return headers


class ImageHttpAdapter(_BaseHttpAdapter, abc.ABC):
    @abc.abstractmethod
    def build_generate_request(self, config: AIConfig, req: ImageRequest) -> ProviderRequest: ...

    @abc.abstractmethod
    def parse_generate_response(self, raw: Any) -> MediaResult: ...

    def generate(self, config: AIConfig, request: ImageRequest) -> MediaResult:
        raw = self.transport.send_json(self.build_generate_request(config, request))
        return self.parse_generate_response(raw)


class TextHttpAdapter(_BaseHttpAdapter, abc.ABC):
    @abc.abstractmethod
    def build_request(self, config: AIConfig, req: TextRequest) -> ProviderRequest: ...

    @abc.abstractmethod
    def parse_response(self, raw: Any) -> MediaResult: ...

    def complete(self, config: AIConfig, request: TextRequest) -> MediaResult:
        raw = self.transport.send_json(self.build_request(config, request))
        return self.parse_response(raw)


class TTSHttpAdapter(_BaseHttpAdapter, abc.ABC):
    @abc.abstractmethod
    def build_generate_request(self, config: AIConfig, req: TTSRequest) -> ProviderRequest: ...

    @abc.abstractmethod
    def parse_response(self, raw: Any) -> MediaResult: ...

    def synthesize(self, config: AIConfig, request: TTSRequest) -> MediaResult:
        raw = self.transport.send_json(self.build_generate_request(config, request))
        return self.parse_response(raw)


class VideoHttpAdapter(_BaseHttpAdapter, abc.ABC):
    """Video is usually async: submit a task, then poll until terminal."""

    supported_modes: List[str] = ["image2video", "first_last"]

    def __init__(self, transport: Optional["Transport"] = None,
                 poll_interval: float = 5.0, max_polls: int = 120,
                 sleep: Optional[Callable[[float], None]] = None):
        super().__init__(transport)
        self.poll_interval = poll_interval
        self.max_polls = max_polls
        self._sleep = sleep or time.sleep

    @abc.abstractmethod
    def build_generate_request(self, config: AIConfig, req: VideoRequest) -> ProviderRequest: ...

    @abc.abstractmethod
    def parse_generate_response(self, raw: Any) -> MediaResult: ...

    @abc.abstractmethod
    def build_poll_request(self, config: AIConfig, task_id: str) -> ProviderRequest: ...

    @abc.abstractmethod
    def parse_poll_response(self, raw: Any) -> MediaResult: ...

    def compose_prompt(self, req: VideoRequest) -> str:
        return req.prompt

    def generate(self, config: AIConfig, request: VideoRequest) -> MediaResult:
        submitted = self.parse_generate_response(
            self.transport.send_json(self.build_generate_request(config, request))
        )
        if not submitted.is_async or not submitted.task_id:
            return submitted
        # poll until terminal
        for _ in range(self.max_polls):
            polled = self.parse_poll_response(
                self.transport.send_json(self.build_poll_request(config, submitted.task_id))
            )
            status = polled.meta.get("status")
            if status in ("succeeded", "completed", "success"):
                polled.task_id = submitted.task_id
                polled.cost = polled.cost or submitted.cost
                return polled
            if status in ("failed", "error", "cancelled"):
                raise ProviderError(
                    f"{self.provider} task {submitted.task_id} failed: "
                    f"{polled.meta.get('error')}",
                    provider=self.provider,
                )
            self._sleep(self.poll_interval)
        raise ProviderError(
            f"{self.provider} task {submitted.task_id} timed out after "
            f"{self.max_polls} polls", provider=self.provider,
        )


# late import for typing only (avoids a cycle at runtime)
from .base import Transport  # noqa: E402
