"""Skill runner: turn orchestrator Steps into executor calls.

This is the minimal stand-in for a full Agent SDK loop. ``run_loop`` repeatedly
asks the state machine for the next Step and executes it, saving the project
after every step (so the run is resumable from any point -- a core requirement
of the orchestrator design).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Sequence, Tuple

from core.project import Project
from core.state_machine import Step, detect_next

from .executors import EXECUTORS, ExecResult


@dataclass
class TargetSpec:
    """Which episodes the per-episode loop (skills 07-12) should produce."""
    episode_ids: Sequence[int] = field(default_factory=tuple)
    all_episodes: bool = False
    include_paywall: bool = False


def apply_targets(project: Project, spec: TargetSpec) -> None:
    """Flag the episodes to produce. Idempotent; safe to call every loop tick."""
    eps = project.episodes
    if not eps:
        return
    if spec.all_episodes:
        for ep in eps:
            ep["produce"] = True
    elif spec.episode_ids:
        wanted = set(spec.episode_ids)
        for ep in eps:
            if ep["id"] in wanted:
                ep["produce"] = True
    else:
        for ep in eps:
            if ep["id"] == 1:
                ep["produce"] = True
            if spec.include_paywall and ep.get("is_paywall_hook"):
                ep["produce"] = True
    if not any(ep.get("produce") for ep in eps):
        eps[0]["produce"] = True


def execute(project: Project, step: Step) -> ExecResult:
    fn = EXECUTORS.get(step.skill or "")
    if fn is None:
        return ExecResult(status="error", summary=f"no executor for skill {step.skill}")
    params = {"episode": step.episode, "target": step.target}
    result = fn(project, params)
    project.save()
    return result


def run_loop(
    project: Project,
    targets: Optional[TargetSpec] = None,
    allow_cost: bool = True,
    max_steps: int = 500,
    on_step: Optional[Callable[[Step, ExecResult], None]] = None,
) -> Tuple[List[Tuple[Step, ExecResult]], Step]:
    """Drive the pipeline until it blocks (WAIT/INIT/DONE), errors, or hits a
    cost gate that the caller has not pre-approved.

    Returns (executed steps, terminal step).
    """
    targets = targets or TargetSpec()
    executed: List[Tuple[Step, ExecResult]] = []
    for _ in range(max_steps):
        apply_targets(project, targets)
        step = detect_next(project)
        if not step.is_actionable:
            return executed, step
        if step.needs_cost_confirmation and not allow_cost:
            return executed, step
        result = execute(project, step)
        executed.append((step, result))
        if on_step:
            on_step(step, result)
        if result.status == "error":
            return executed, step
    return executed, detect_next(project)
