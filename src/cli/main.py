"""``studio`` command-line interface.

The CLI is a thin shell over the orchestrator state machine and the mock
pipeline runner. It mirrors the conversational flow described in
skills/00-orchestrator/SKILL.md but in a non-interactive, scriptable form.

Commands:
    studio new <name> [--novel FILE]   create a project (optionally ingest a novel)
    studio status <name>               show pipeline state + next step
    studio next <name> [--yes]         run exactly the next step
    studio run <name> [--auto] ...     drive the pipeline (stops at the cost gate)
    studio compose <name> --episode N  produce a single episode end-to-end
    studio skills                      list the loaded SKILL.md files
    studio providers                   list registered provider adapters
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from agent_runtime.runner import TargetSpec, run_loop
from agent_runtime.skill_loader import find_skills_root, load_all_skills
from backends import registry
from core import constants as C
from core.project import ProjectManager
from core.state_machine import (
    DONE,
    INIT,
    WAIT,
    Step,
    detect_next,
    production_episodes,
)

OK = "\u2705"      # ✅
PENDING = "\u23f3"  # ⏳
FAIL = "\u274c"     # ❌
WARN = "\u26a0\ufe0f"  # ⚠️
ARROW = "\u2192"   # →


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _manager(args: argparse.Namespace) -> ProjectManager:
    return ProjectManager(getattr(args, "workspace", None))


def _phase_checklist(project) -> List[str]:
    d = project.data
    rows = []

    def mark(done: bool) -> str:
        return OK if done else PENDING

    rows.append(f"{mark(bool(d['source'].get('novel_path')))} 源文件上传")
    rows.append(f"{mark(bool(d.get('analysis')))} 01 全本理解")
    rows.append(f"{mark(bool((d.get('production_plan') or {}).get('locked')))} 02 制作参数锁定")
    rows.append(f"{mark(bool(d.get('scripts')))} 03 全集剧本")
    rows.append(f"{mark(bool(d.get('assets')))} 04 资产提取")
    rows.append(f"{mark(bool(d.get('art_style_id')))} 05 画风定调")

    assets = d.get("assets") or {}
    chars = assets.get("characters", [])
    chars_done = bool(chars) and all(
        all((c.get("visual_assets") or {}).get(a)
            for a in C.TIER_REQUIRED_ASSETS.get(c.get("tier", "extra"), ("reference", "avatar")))
        for c in chars
    )
    rows.append(f"{mark(chars_done)} 06 角色/场景/道具定妆")
    return rows


def _episode_summary(project) -> List[str]:
    rows = []
    for ep in production_episodes(project):
        shots = ep.get("shots") or []
        if not shots:
            state = "未拆分镜"
        else:
            if ep.get("output"):
                state = f"{OK} 成片 {ep['output']}"
            else:
                lowest = min(shots, key=lambda s: C.readiness_rank(s.get("readiness", "draft")))
                state = f"{len(shots)} 镜 / 最低 {lowest.get('readiness')}"
        hook = " [付费集]" if ep.get("is_paywall_hook") else ""
        rows.append(f"  - 第 {ep['id']} 集{hook}: {state}")
    return rows


def _describe_step(step: Step) -> str:
    if step.action == INIT:
        return f"{WARN} 初始化：{step.reason}"
    if step.action == DONE:
        return f"{OK} 全部目标已完成：{step.reason}"
    if step.action == WAIT:
        return f"{PENDING} 等待用户：{step.reason}"
    tgt = f" target={step.target}" if step.target else ""
    ep = f" episode={step.episode}" if step.episode else ""
    gate = f" {WARN}成本关卡" if step.needs_cost_confirmation else ""
    return f"{ARROW} 下一步：{step.skill}{tgt}{ep}{gate}\n  原因：{step.reason}"


def _cost_line(project) -> str:
    cost = project.data.get("cost") or {"total": 0.0}
    return f"累计成本（mock）：¥{cost.get('total', 0.0)}"


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #
def cmd_new(args: argparse.Namespace) -> int:
    mgr = _manager(args)
    novel_path = os.path.abspath(args.novel) if args.novel else None
    if novel_path and not os.path.exists(novel_path):
        print(f"{FAIL} novel file not found: {novel_path}")
        return 1
    try:
        project = mgr.create(args.name, content_mode=args.content_mode, novel_path=novel_path)
    except FileExistsError as exc:
        print(f"{FAIL} {exc}")
        return 1
    print(f"{OK} 已创建项目: {project.root}")
    src = project.data["source"]
    if src.get("novel_path"):
        print(f"   小说: {src.get('title')} ({src.get('char_count')} 字 / {src.get('chapter_count')} 章)")
    print(_describe_step(detect_next(project)))
    print(f"\n提示: studio run {project.name}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    project = _load_or_fail(args)
    if project is None:
        return 1
    d = project.data
    print(f"项目: {project.name}   content_mode={d['content_mode']}")
    print(f"目录: {project.root}")
    print("-" * 60)
    for row in _phase_checklist(project):
        print(row)
    ep_rows = _episode_summary(project)
    if ep_rows:
        print("单集制作:")
        for row in ep_rows:
            print(row)
    print("-" * 60)
    print(_cost_line(project))
    print(_describe_step(detect_next(project)))
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    project = _load_or_fail(args)
    if project is None:
        return 1
    spec = _target_spec(args)
    executed, terminal = run_loop(
        project, targets=spec, allow_cost=args.yes, max_steps=1,
        on_step=_print_step,
    )
    if not executed:
        print(_describe_step(terminal))
        if terminal.needs_cost_confirmation:
            print("   使用 --yes 确认成本后继续。")
    print(_cost_line(project))
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    project = _load_or_fail(args)
    if project is None:
        return 1
    spec = _target_spec(args)
    allow_cost = args.yes or args.auto
    print(f"{PENDING} 开始驱动流水线 (auto={args.auto}, allow_cost={allow_cost}) ...\n")
    executed, terminal = run_loop(project, targets=spec, allow_cost=allow_cost,
                                  on_step=_print_step)
    print()
    if terminal.action == DONE:
        print(f"{OK} 流水线完成。{terminal.reason}")
    elif terminal.action == WAIT:
        print(f"{PENDING} 暂停（等待用户）：{terminal.reason}")
    elif terminal.action == INIT:
        print(f"{WARN} 无法开始：{terminal.reason}")
    elif terminal.needs_cost_confirmation:
        print(f"{WARN} 已到成本关卡：{terminal.reason}")
        print(_estimate_gate(project, terminal))
        print(f"   继续请执行: studio run {project.name} --yes"
              + (f" --episode {terminal.episode}" if terminal.episode else ""))
    else:
        print(f"{FAIL} 停止：{terminal.reason}")
    print(_cost_line(project))
    print("\n产物:")
    for ep in production_episodes(project):
        if ep.get("output"):
            print(f"  {OK} {project.path(ep['output'])}")
    return 0


def cmd_compose(args: argparse.Namespace) -> int:
    project = _load_or_fail(args)
    if project is None:
        return 1
    spec = TargetSpec(episode_ids=(args.episode,))
    print(f"{PENDING} 制作第 {args.episode} 集（end-to-end, allow_cost=True）...\n")
    executed, terminal = run_loop(project, targets=spec, allow_cost=True, on_step=_print_step)
    print()
    ep = project.episode(args.episode)
    if ep and ep.get("output"):
        print(f"{OK} 第 {args.episode} 集成片: {project.path(ep['output'])}")
    else:
        print(f"{WARN} 第 {args.episode} 集尚未完成：{terminal.reason}")
    print(_cost_line(project))
    return 0


def cmd_skills(args: argparse.Namespace) -> int:
    root = args.skills_dir or find_skills_root(os.getcwd())
    if not root:
        print(f"{FAIL} 找不到 skills/ 目录（用 --skills-dir 指定）")
        return 1
    skills = load_all_skills(root)
    print(f"skills 目录: {root}  (共 {len(skills)} 个)")
    print("-" * 60)
    for sk in skills:
        kind = sk.agent_type or "?"
        print(f"{sk.skill_id:26s} [{kind}]")
        if sk.description:
            desc = sk.description if len(sk.description) <= 70 else sk.description[:67] + "..."
            print(f"  {desc}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    from backends import config as bk

    providers = registry.list_providers()
    print("已注册的 Provider Adapter:")
    for kind, names in providers.items():
        print(f"  {kind:6s}: {', '.join(names) or '(none)'}")
    print("-" * 60)
    print("当前生效（按环境变量解析，无凭证则回退 mock）:")
    for kind, name in bk.active_providers().items():
        tag = "" if name.startswith("mock") else "  (real)"
        print(f"  {kind:6s}: {name}{tag}")
    print("\n配置真实供应商示例: ARK_API_KEY=... STUDIO_IMAGE_PROVIDER=seedream")
    return 0


# --------------------------------------------------------------------------- #
# shared command plumbing
# --------------------------------------------------------------------------- #
def _load_or_fail(args: argparse.Namespace):
    mgr = _manager(args)
    try:
        return mgr.load(args.name)
    except FileNotFoundError as exc:
        print(f"{FAIL} {exc}")
        print("   先创建项目: studio new <name> --novel <file>")
        return None


def _target_spec(args: argparse.Namespace) -> TargetSpec:
    episodes = tuple(getattr(args, "episode", None) or ())
    return TargetSpec(
        episode_ids=episodes,
        all_episodes=getattr(args, "all_episodes", False),
        include_paywall=getattr(args, "include_paywall", False),
    )


def _print_step(step: Step, result) -> None:
    icon = OK if result.status == "success" else FAIL
    cost = f"  (¥{result.cost})" if result.cost else ""
    print(f"{icon} {step.skill}{cost}: {result.summary}")
    for warning in result.warnings:
        print(f"   {WARN} {warning}")


def _estimate_gate(project, step: Step) -> str:
    ep = project.episode(step.episode) if step.episode else None
    shots = len(ep.get("shots", [])) if ep else 0
    est = round(shots * 7.5, 2)
    return f"   预估第 {step.episode} 集视频成本约 ¥{est}（{shots} 镜 × ¥7.5/镜，mock）"


# --------------------------------------------------------------------------- #
# argument parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio",
        description="Agents Studio AI - M1 CLI MVP (offline mock pipeline)",
    )
    parser.add_argument("--workspace", help="workspace root (default: cwd)")
    sub = parser.add_subparsers(dest="command")

    p_new = sub.add_parser("new", help="create a project")
    p_new.add_argument("name")
    p_new.add_argument("--novel", help="path to a novel .txt to ingest")
    p_new.add_argument("--content-mode", default="drama", choices=list(C.CONTENT_MODES))
    p_new.set_defaults(func=cmd_new)

    p_status = sub.add_parser("status", help="show pipeline state")
    p_status.add_argument("name")
    p_status.set_defaults(func=cmd_status)

    p_next = sub.add_parser("next", help="run exactly the next step")
    p_next.add_argument("name")
    p_next.add_argument("--yes", action="store_true", help="approve cost gates")
    _add_target_args(p_next)
    p_next.set_defaults(func=cmd_next)

    p_run = sub.add_parser("run", help="drive the pipeline")
    p_run.add_argument("name")
    p_run.add_argument("--auto", action="store_true", help="skip confirmations (implies --yes)")
    p_run.add_argument("--yes", action="store_true", help="approve cost gates")
    _add_target_args(p_run)
    p_run.set_defaults(func=cmd_run)

    p_compose = sub.add_parser("compose", help="produce a single episode end-to-end")
    p_compose.add_argument("name")
    p_compose.add_argument("--episode", type=int, required=True)
    p_compose.set_defaults(func=cmd_compose)

    p_skills = sub.add_parser("skills", help="list loaded SKILL.md files")
    p_skills.add_argument("--skills-dir", help="path to skills/ (default: auto-discover)")
    p_skills.set_defaults(func=cmd_skills)

    p_providers = sub.add_parser("providers", help="list provider adapters")
    p_providers.set_defaults(func=cmd_providers)

    return parser


def _add_target_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--episode", type=int, action="append",
                   help="episode id to produce (repeatable)")
    p.add_argument("--all-episodes", action="store_true", help="produce every episode")
    p.add_argument("--include-paywall", action="store_true",
                   help="also produce paywall-hook episodes by default")


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
