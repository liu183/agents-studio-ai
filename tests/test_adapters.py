"""Offline unit tests for the real HTTP adapters.

Every adapter is exercised with a FakeTransport fed canned provider JSON, so
these run fully offline (the sandbox has no network). We assert both the
request the adapter *builds* and the result it *parses*.
"""

from __future__ import annotations

from backends.base import AIConfig, ImageRequest, TextRequest, TTSRequest, VideoRequest
from backends.http import FakeTransport
from backends.minimax import MiniMaxTTSAdapter, MiniMaxVideoAdapter
from backends.openai_compat import OpenAICompatImageAdapter, OpenAICompatTextAdapter
from backends.volcengine import SeedanceVideoAdapter, SeedreamImageAdapter


def test_openai_text_build_and_parse():
    fake = FakeTransport(responses=[{
        "choices": [{"message": {"content": "hello world"}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }])
    adapter = OpenAICompatTextAdapter(transport=fake)
    cfg = AIConfig(provider="openai_compat", api_key="k", model="gpt-4o-mini")
    result = adapter.complete(cfg, TextRequest(system_prompt="sys",
                                               messages=[{"role": "user", "content": "hi"}]))
    req = fake.requests[0]
    assert req.url.endswith("/chat/completions")
    assert req.headers["Authorization"] == "Bearer k"
    assert req.body["messages"][0] == {"role": "system", "content": "sys"}
    assert result.data == "hello world"
    assert result.meta["usage"]["prompt_tokens"] == 10


def test_openai_image_url_and_b64():
    fake = FakeTransport(responses=[{"data": [{"url": "https://img/a.png"}]}])
    adapter = OpenAICompatImageAdapter(transport=fake)
    cfg = AIConfig(provider="openai", api_key="k")
    res = adapter.generate(cfg, ImageRequest(prompt="cat", size="1024x1024"))
    assert res.url == "https://img/a.png"
    assert fake.requests[0].body["size"] == "1024x1024"

    fake2 = FakeTransport(responses=[{"data": [{"b64_json": "QUJD"}]}])
    res2 = OpenAICompatImageAdapter(transport=fake2).generate(cfg, ImageRequest(prompt="cat"))
    assert res2.data == "QUJD" and res2.data_encoding == "base64"


def test_seedream_build_request():
    fake = FakeTransport(responses=[{"data": [{"url": "https://ark/img.png"}]}])
    adapter = SeedreamImageAdapter(transport=fake)
    cfg = AIConfig(provider="seedream", api_key="ark", model="doubao-seedream-4-0-250828")
    res = adapter.generate(cfg, ImageRequest(prompt="美少女", size="1080x1920",
                                             reference_images=["ref/a.png"]))
    body = fake.requests[0].body
    assert body["model"] == "doubao-seedream-4-0-250828"
    assert body["watermark"] is False
    assert body["image"] == "ref/a.png"  # single ref -> scalar
    assert res.url == "https://ark/img.png"


def test_seedance_submit_poll_flow():
    # POST submit -> {id}; first poll running; second poll succeeded.
    def router(req):
        if req.method == "POST":
            return {"id": "task-9"}
        # GET poll
        if router.calls == 0:
            router.calls += 1
            return {"status": "running"}
        return {"status": "succeeded", "content": {"video_url": "https://ark/v.mp4"}}
    router.calls = 0

    fake = FakeTransport(router=router)
    adapter = SeedanceVideoAdapter(transport=fake, poll_interval=0, sleep=lambda _s: None)
    cfg = AIConfig(provider="seedance", api_key="ark")
    res = adapter.generate(cfg, VideoRequest(prompt="run", generation_mode="first_last",
                                             first_frame_url="a.png", last_frame_url="b.png",
                                             duration=5, aspect_ratio="9:16", resolution="1080p"))
    assert res.url == "https://ark/v.mp4"
    assert res.task_id == "task-9"
    # submit body carries prompt suffix + both frame roles
    submit = fake.requests[0].body
    text_item = submit["content"][0]["text"]
    assert "--resolution 1080p" in text_item and "--ratio 9:16" in text_item
    roles = [c.get("role") for c in submit["content"] if c["type"] == "image_url"]
    assert roles == ["first_frame", "last_frame"]


def test_minimax_tts_hex_audio():
    fake = FakeTransport(responses=[{
        "data": {"audio": "68656c6c6f"},  # "hello"
        "extra_info": {"audio_length": 2000, "audio_size": 123},
        "base_resp": {"status_code": 0, "status_msg": "success"},
    }])
    adapter = MiniMaxTTSAdapter(transport=fake)
    cfg = AIConfig(provider="minimax", api_key="mk", extras={"group_id": "g1"})
    res = adapter.synthesize(cfg, TTSRequest(voice_id="zh_female", text="你好", emotion="happy"))
    assert res.data == "68656c6c6f" and res.data_encoding == "hex"
    assert res.duration_sec == 2.0
    req = fake.requests[0]
    assert req.query["GroupId"] == "g1"
    assert req.body["voice_setting"]["voice_id"] == "zh_female"
    assert req.body["voice_setting"]["emotion"] == "happy"


def test_minimax_video_three_step():
    def router(req):
        if "/query/video_generation" in req.url:
            return {"status": "Success", "file_id": "f1", "base_resp": {"status_code": 0}}
        if "/files/retrieve" in req.url:
            return {"file": {"download_url": "https://mm/v.mp4"}}
        if req.url.endswith("/video_generation"):
            return {"task_id": "t1", "base_resp": {"status_code": 0}}
        raise AssertionError(req.url)

    fake = FakeTransport(router=router)
    adapter = MiniMaxVideoAdapter(transport=fake, poll_interval=0, sleep=lambda _s: None)
    cfg = AIConfig(provider="minimax_video", api_key="mk", extras={"group_id": "g1"})
    res = adapter.generate(cfg, VideoRequest(prompt="x", image_url="a.png"))
    assert res.url == "https://mm/v.mp4"
    assert res.task_id == "t1"
