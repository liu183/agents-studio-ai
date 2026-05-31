"""Integration test: drive the real pipeline with FakeTransport injected.

This proves the executors actually call the real adapters, download/decode the
returned media, and write real bytes to disk -- all without touching the
network. We point STUDIO_* at real providers, give them credentials, then swap
each registered adapter's transport for a FakeTransport router.
"""

from __future__ import annotations

from agent_runtime.runner import TargetSpec, run_loop
from backends import registry
from backends.http import FakeTransport
from core.project import ProjectManager

NOVEL = "重生之凤归来\n第一章\n苏婉清睁开眼。\n第二章\n陆沉舟出现了。\n"


def _router(req):
    url = req.url
    if url.endswith("/images/generations"):
        return {"data": [{"url": "https://ark/img.png"}]}
    if url.endswith("/contents/generations/tasks"):  # seedance submit (POST)
        return {"id": "vtask-1"}
    if "/contents/generations/tasks/" in url:  # seedance poll (GET)
        return {"status": "succeeded", "content": {"video_url": "https://ark/clip.mp4"}}
    if url.endswith("/t2a_v2"):
        return {"data": {"audio": "68656c6c6f"},  # "hello"
                "extra_info": {"audio_length": 1500},
                "base_resp": {"status_code": 0}}
    raise AssertionError(f"unexpected url: {url}")


def test_real_pipeline_writes_real_bytes(tmp_path, monkeypatch):
    # configure real providers
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")
    monkeypatch.setenv("MINIMAX_GROUP_ID", "g1")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "seedream")
    monkeypatch.setenv("STUDIO_VIDEO_PROVIDER", "seedance")
    monkeypatch.setenv("STUDIO_TTS_PROVIDER", "minimax")
    # disable per-channel RPM limits so the test runs in milliseconds
    monkeypatch.setenv("STUDIO_IMAGE_RPM", "0")
    monkeypatch.setenv("STUDIO_VIDEO_RPM", "0")
    monkeypatch.setenv("STUDIO_TTS_RPM", "0")

    # inject FakeTransport into the registered real adapters
    fake = FakeTransport(router=_router)
    seedance = registry.video_adapters["seedance"]
    monkeypatch.setattr(registry.image_adapters["seedream"], "transport", fake)
    monkeypatch.setattr(seedance, "transport", fake)
    monkeypatch.setattr(seedance, "_sleep", lambda _s: None)
    monkeypatch.setattr(seedance, "poll_interval", 0)
    monkeypatch.setattr(registry.tts_adapters["minimax"], "transport", fake)

    mgr = ProjectManager(str(tmp_path))
    project = mgr.create("real-demo", novel_text=NOVEL)
    run_loop(project, targets=TargetSpec(episode_ids=(1,)), allow_cost=True)

    project = mgr.load("real-demo")
    ep = project.episode(1)
    assert ep["output"], "episode 1 should be composed"

    sb = project.path("storyboards/episode_001")
    shot0 = ep["shots"][0]["id"]
    # video clip: downloaded bytes
    with open(f"{sb}/{shot0}/clip.mp4", "rb") as fh:
        assert fh.read().startswith(b"FAKE-BYTES:https://ark/clip.mp4")
    # keyframe: downloaded bytes
    with open(f"{sb}/{shot0}/start_frame.png", "rb") as fh:
        assert fh.read().startswith(b"FAKE-BYTES:https://ark/img.png")
    # audio: decoded from hex -> "hello"
    with open(f"{sb}/{shot0}/audio.wav", "rb") as fh:
        assert fh.read() == b"hello"

    # a character reference image was also fetched as real bytes
    with open(project.path("assets/characters/char_01/reference.png"), "rb") as fh:
        assert fh.read().startswith(b"FAKE-BYTES:")
