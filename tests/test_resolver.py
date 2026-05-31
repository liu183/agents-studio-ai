"""Tests for env-driven provider resolution + mock fallback."""

from __future__ import annotations

from backends import config as bk
from backends.minimax import MiniMaxTTSAdapter
from backends.volcengine import SeedanceVideoAdapter, SeedreamImageAdapter


def _clear_env(monkeypatch):
    for key in list(bk._SELECTION_ENV.values()) + [
        "ARK_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY", "MINIMAX_GROUP_ID",
        "ARK_IMAGE_MODEL", "ARK_VIDEO_MODEL",
    ]:
        monkeypatch.delenv(key, raising=False)


def test_defaults_to_mock_without_credentials(monkeypatch):
    _clear_env(monkeypatch)
    adapter, cfg = bk.resolve_image()
    assert cfg.provider == "mock-image"
    assert bk.active_providers() == {
        "image": "mock-image", "video": "mock-video",
        "text": "mock-text", "tts": "mock-tts",
    }


def test_seedream_selected_when_configured(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("ARK_IMAGE_MODEL", "doubao-seedream-4-0-250828")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "seedream")
    adapter, cfg = bk.resolve_image()
    assert isinstance(adapter, SeedreamImageAdapter)
    assert cfg.api_key == "ark-key"
    assert cfg.model == "doubao-seedream-4-0-250828"


def test_video_and_tts_resolution(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ARK_API_KEY", "ark-key")  # seedance default
    monkeypatch.setenv("MINIMAX_API_KEY", "mm-key")  # minimax default tts
    monkeypatch.setenv("MINIMAX_GROUP_ID", "g1")
    v_adapter, v_cfg = bk.resolve_video()
    t_adapter, t_cfg = bk.resolve_tts()
    assert isinstance(v_adapter, SeedanceVideoAdapter) and v_cfg.api_key == "ark-key"
    assert isinstance(t_adapter, MiniMaxTTSAdapter) and t_cfg.extras["group_id"] == "g1"


def test_explicit_mock_overrides_env(monkeypatch):
    _clear_env(monkeypatch)
    monkeypatch.setenv("ARK_API_KEY", "ark-key")
    monkeypatch.setenv("STUDIO_IMAGE_PROVIDER", "seedream")
    adapter, cfg = bk.resolve_image(explicit="mock-image")
    assert cfg.provider == "mock-image"


def test_unknown_provider_falls_back_to_mock(monkeypatch):
    _clear_env(monkeypatch)
    adapter, cfg = bk.resolve_video(explicit="does-not-exist")
    assert cfg.provider == "mock-video"
