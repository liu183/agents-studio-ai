"""Shared constants for the Agents Studio runtime.

These mirror the contracts documented in:
    - skills/00-orchestrator/SKILL.md  (state machine)
    - packages/asset-spec/shot.yaml    (readiness state machine)
    - packages/asset-spec/character.yaml (weight tiers)
"""

from __future__ import annotations

PROJECT_FILE = "project.json"
SCHEMA_VERSION = 1

# ---- content / generation modes (orchestration-level, hidden from the LLM) --
CONTENT_MODES = ("drama", "narration")

# ---- shot readiness state machine (ordered low -> high) ---------------------
# Mirrors the readiness levels referenced by the orchestrator routing table.
READINESS_ORDER = (
    "draft",
    "storyboard_locked",
    "keyframes_candidates",
    "keyframes_locked",
    "video_candidates",
    "video_locked",
    "audio_locked",
    "shot_ready",
)


def readiness_rank(state: str) -> int:
    """Return the ordinal of a readiness state (-1 if unknown)."""
    try:
        return READINESS_ORDER.index(state)
    except ValueError:
        return -1


def readiness_lt(a: str, b: str) -> bool:
    """True when readiness *a* is strictly earlier than *b*."""
    return readiness_rank(a) < readiness_rank(b)


# ---- character weight tiers (drive 06-character-designer asset depth) -------
def weight_tier(weight: int) -> str:
    if weight >= 7:
        return "protagonist"  # Tier-A
    if weight >= 4:
        return "supporting"  # Tier-B
    return "extra"  # Tier-C


# Required visual assets per tier (see packages/asset-spec/character.yaml).
TIER_REQUIRED_ASSETS = {
    "protagonist": ("reference", "three_views", "avatar", "wardrobe"),
    "supporting": ("reference", "three_views", "avatar", "wardrobe"),
    "extra": ("reference", "avatar"),
}

# ---- pipeline phases --------------------------------------------------------
PHASE_CONTENT = "M1-content"
PHASE_ASSETS = "M2-assets"
PHASE_EPISODE = "M3-episode-loop"
PHASE_INIT = "init"
PHASE_DONE = "done"

# ---- skill ids (directory names under skills/) ------------------------------
SKILL_ORCHESTRATOR = "00-orchestrator"
SKILL_NOVEL_ANALYST = "01-novel-analyst"
SKILL_SHOW_PLANNER = "02-show-planner"
SKILL_SCRIPT_WRITER = "03-script-writer"
SKILL_ASSET_EXTRACTOR = "04-asset-extractor"
SKILL_ART_DIRECTOR = "05-art-director"
SKILL_CHARACTER_DESIGNER = "06-character-designer"
SKILL_STORYBOARD_BREAKER = "07-storyboard-breaker"
SKILL_KEYFRAME_PLANNER = "08a-keyframe-planner"
SKILL_KEYFRAME_GENERATOR = "08-keyframe-generator"
SKILL_VIDEO_GENERATOR = "09-video-generator"
SKILL_VOICE_ASSIGNER = "10-voice-assigner"
SKILL_TTS_SYNTHESIZER = "11-tts-synthesizer"
SKILL_VIDEO_COMPOSER = "12-video-composer"
