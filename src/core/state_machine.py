"""Orchestrator state machine (code mirror of skills/00-orchestrator/SKILL.md).

``detect_next(project)`` inspects ``project.json`` and returns the single next
``Step`` to run -- the first matching row of the routing table. This is the
deterministic core that the CLI ``run``/``next``/``status`` commands drive.

The orchestrator never does creative work itself; each Step names the skill a
focused subagent (here: a mock executor) should run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import constants as C
from .constants import readiness_lt
from .project import Project

# Step.action values
DISPATCH = "dispatch"  # run the named skill
WAIT = "wait"  # blocked on the user (e.g. candidate selection)
INIT = "init"  # project not ready to start (no source)
DONE = "done"  # nothing left to do


@dataclass
class Step:
    action: str
    phase: str
    skill: Optional[str] = None
    episode: Optional[int] = None
    target: Optional[str] = None  # for 06-character-designer: character/scene/prop/clue
    reason: str = ""
    needs_cost_confirmation: bool = False
    params: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_actionable(self) -> bool:
        return self.action == DISPATCH


# --------------------------------------------------------------------------- #
# Production targeting: which episodes are we actually producing right now?
# Paywall hooks get priority (orchestrator: "付费集必须优先级最高").
# --------------------------------------------------------------------------- #
def production_episodes(project: Project) -> List[Dict[str, Any]]:
    targets = [ep for ep in project.episodes if ep.get("produce")]
    targets.sort(key=lambda ep: (not ep.get("is_paywall_hook"), ep.get("id", 0)))
    return targets


# --------------------------------------------------------------------------- #
# Per-asset completeness helpers
# --------------------------------------------------------------------------- #
def _character_incomplete(project: Project) -> Optional[Dict[str, Any]]:
    assets = project.data.get("assets") or {}
    for ch in assets.get("characters", []):
        tier = ch.get("tier") or C.weight_tier(ch.get("weight", 1))
        required = C.TIER_REQUIRED_ASSETS.get(tier, ("reference", "avatar"))
        visual = ch.get("visual_assets") or {}
        if any(not visual.get(asset) for asset in required):
            return ch
    return None


def _scene_missing(project: Project) -> Optional[Dict[str, Any]]:
    assets = project.data.get("assets") or {}
    for sc in assets.get("scenes", []):
        if sc.get("is_main") and not sc.get("reference_image"):
            return sc
    return None


def _prop_missing(project: Project) -> Optional[Dict[str, Any]]:
    assets = project.data.get("assets") or {}
    for prop in assets.get("props", []):
        if prop.get("importance") in ("high", "medium") and not prop.get("reference_image"):
            return prop
    return None


def _clue_missing(project: Project) -> Optional[Dict[str, Any]]:
    assets = project.data.get("assets") or {}
    for clue in assets.get("clues", []):
        if not clue.get("reference_image"):
            return clue
    return None


def _speaking_without_voice(project: Project) -> bool:
    assets = project.data.get("assets") or {}
    for ch in assets.get("characters", []):
        if ch.get("role_type") in ("protagonist", "supporting") and not ch.get("voice_id"):
            return True
    return False


def _shots_below(ep: Dict[str, Any], readiness: str) -> bool:
    shots = ep.get("shots") or []
    if not shots:
        return False
    return any(readiness_lt(s.get("readiness", "draft"), readiness) for s in shots)


def _shots_at(ep: Dict[str, Any], readiness: str) -> bool:
    shots = ep.get("shots") or []
    return any(s.get("readiness") == readiness for s in shots)


def _shots_all(ep: Dict[str, Any], readiness: str) -> bool:
    shots = ep.get("shots") or []
    return bool(shots) and all(s.get("readiness") == readiness for s in shots)


# --------------------------------------------------------------------------- #
# The routing table
# --------------------------------------------------------------------------- #
def detect_next(project: Project) -> Step:
    data = project.data

    # 1. no source -> cannot start
    source = data.get("source") or {}
    if not source.get("novel_path"):
        return Step(INIT, C.PHASE_INIT, reason="upload a novel or paste an outline to begin")

    # 2. source present, not analysed
    if not data.get("analysis"):
        return Step(DISPATCH, C.PHASE_CONTENT, C.SKILL_NOVEL_ANALYST,
                    reason="source uploaded, whole-novel analysis missing")

    # 3. analysed, plan not locked
    plan = data.get("production_plan") or {}
    if not plan.get("locked"):
        return Step(DISPATCH, C.PHASE_CONTENT, C.SKILL_SHOW_PLANNER,
                    reason="analysis ready, production plan not locked")

    # 4. plan locked, scripts missing
    if not data.get("scripts"):
        return Step(DISPATCH, C.PHASE_CONTENT, C.SKILL_SCRIPT_WRITER,
                    reason="plan locked, full-series script not generated")

    # 5. scripts ready, assets not extracted
    if not data.get("assets"):
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_ASSET_EXTRACTOR,
                    reason="scripts ready, assets not extracted")

    # 6. assets extracted, art style undecided
    if not data.get("art_style_id"):
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_ART_DIRECTOR,
                    reason="assets extracted, art style undecided")

    # 7-10. art style set, visual assets incomplete
    ch = _character_incomplete(project)
    if ch:
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_CHARACTER_DESIGNER, target="character",
                    reason=f"character visuals incomplete: {ch.get('name')}")
    sc = _scene_missing(project)
    if sc:
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_CHARACTER_DESIGNER, target="scene",
                    reason=f"main scene missing reference: {sc.get('name')}")
    prop = _prop_missing(project)
    if prop:
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_CHARACTER_DESIGNER, target="prop",
                    reason=f"prop missing reference: {prop.get('name')}")
    clue = _clue_missing(project)
    if clue:
        return Step(DISPATCH, C.PHASE_ASSETS, C.SKILL_CHARACTER_DESIGNER, target="clue",
                    reason=f"clue missing reference: {clue.get('name')}")

    # 11-16. per-episode production loop (paywall hooks first)
    for ep in production_episodes(project):
        n = ep.get("id")

        # 11. script present, storyboard not locked
        if not ep.get("shots"):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_STORYBOARD_BREAKER, episode=n,
                        reason=f"episode {n}: storyboard not broken down")
        if _shots_below(ep, "storyboard_locked"):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_STORYBOARD_BREAKER, episode=n,
                        reason=f"episode {n}: shots below storyboard_locked")

        # 11.5. storyboard locked, keyframe plan not locked
        if not ep.get("keyframe_plan_locked"):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_KEYFRAME_PLANNER, episode=n,
                        reason=f"episode {n}: keyframe plan not reviewed")

        # 12. keyframe plan locked, keyframes not locked
        if _shots_below(ep, "keyframes_locked"):
            # 12.5 wait state when batch>1 produced candidates
            if _shots_at(ep, "keyframes_candidates"):
                return Step(WAIT, C.PHASE_EPISODE, episode=n,
                            reason=f"episode {n}: waiting for keyframe candidate selection")
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_KEYFRAME_GENERATOR, episode=n,
                        reason=f"episode {n}: keyframes missing")

        # 13. keyframes locked, video not locked  (cost gate)
        if _shots_below(ep, "video_locked"):
            if _shots_at(ep, "video_candidates"):
                return Step(WAIT, C.PHASE_EPISODE, episode=n,
                            reason=f"episode {n}: waiting for video candidate selection")
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_VIDEO_GENERATOR, episode=n,
                        needs_cost_confirmation=True,
                        reason=f"episode {n}: video clips missing (high cost)")

        # 14. speaking characters without voice (first time only)
        if _speaking_without_voice(project):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_VOICE_ASSIGNER,
                        reason="speaking characters missing voice assignment")

        # 15. video locked, audio not locked
        if _shots_below(ep, "audio_locked"):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_TTS_SYNTHESIZER, episode=n,
                        reason=f"episode {n}: dialogue not synthesized")

        # 16. all shots ready, episode not composed
        if not ep.get("output") and _shots_all(ep, "shot_ready"):
            return Step(DISPATCH, C.PHASE_EPISODE, C.SKILL_VIDEO_COMPOSER, episode=n,
                        reason=f"episode {n}: ready to compose final cut")

    # 17. everything targeted is done
    return Step(DONE, C.PHASE_DONE, reason="all targeted episodes composed")
