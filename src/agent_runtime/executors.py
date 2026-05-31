"""Mock skill executors.

Each function plays the role of a focused subagent for one skill: it reads the
relevant project state, calls the (mock) provider adapters, writes the
documented on-disk artifacts (analysis/, scripts/, assets/, storyboards/,
output/), advances ``project.json`` and returns a concise summary -- exactly
the摘要 contract the orchestrator expects.

In M1 every executor is deterministic and offline. Swapping a mock adapter for
a real one (later milestone) does not change these orchestration steps.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backends import config as bk
from backends.base import ImageRequest, TTSRequest, VideoRequest
from backends.http import UrllibTransport, fetch_media_bytes

from core import constants as C
from core import miniyaml
from core.project import Project

_DEFAULT_TRANSPORT = UrllibTransport()


@dataclass
class ExecResult:
    status: str = "success"
    summary: str = ""
    artifacts: List[str] = field(default_factory=list)
    cost: float = 0.0
    warnings: List[str] = field(default_factory=list)
    next_actions: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
_CJK = r"[\u4e00-\u9fff]"


def _candidate_names(text: str, top_n: int) -> List[str]:
    """Light heuristic: frequent 2-3 char CJK tokens look like character names."""
    tokens = Counter()
    for match in re.findall(rf"{_CJK}{{2,3}}", text):
        tokens[match] += 1
    stop = {"什么", "我们", "你们", "他们", "自己", "这个", "那个", "可以", "没有", "知道", "现在"}
    ranked = [name for name, count in tokens.most_common(60) if count >= 2 and name not in stop]
    return ranked[:top_n] or [f"角色{i + 1}" for i in range(top_n)]


def _add_cost(project: Project, skill: str, amount: float) -> None:
    cost = project.data.setdefault("cost", {"total": 0.0, "by_skill": {}})
    cost["total"] = round(cost["total"] + amount, 2)
    cost["by_skill"][skill] = round(cost["by_skill"].get(skill, 0.0) + amount, 2)


def _placeholder(project: Project, rel_path: str, note: str) -> str:
    """Write a small placeholder media file so the documented layout exists."""
    return project.write_text(rel_path, f"# MOCK ARTIFACT\n# {note}\n")


def _save_media(project: Project, rel_path: str, result, adapter, note: str) -> str:
    """Persist a generated asset.

    For real providers this downloads the URL (or decodes inline hex/base64
    bytes) and writes the actual binary. For mock providers (``mock://`` URL,
    no inline data) ``fetch_media_bytes`` returns None and we fall back to a
    text placeholder -- keeping the offline pipeline working unchanged.
    """
    transport = getattr(adapter, "transport", None) or _DEFAULT_TRANSPORT
    try:
        blob = fetch_media_bytes(result, transport)
    except Exception as exc:  # network/decoding issue -> degrade to placeholder
        return _placeholder(project, rel_path, f"{note} (fetch failed: {exc})")
    if blob is not None:
        return project.write_bytes(rel_path, blob)
    return _placeholder(project, rel_path, f"{note} <- {result.url}")


def _aspect_size(plan: Dict[str, Any]) -> str:
    return "1080x1920" if plan.get("aspect_ratio", "9:16") == "9:16" else "1920x1080"


# --------------------------------------------------------------------------- #
# 01 novel-analyst
# --------------------------------------------------------------------------- #
def run_novel_analyst(project: Project, params: Dict[str, Any]) -> ExecResult:
    src = project.data["source"]
    novel_text = ""
    if src.get("novel_path"):
        with open(project.path(src["novel_path"]), "r", encoding="utf-8") as handle:
            novel_text = handle.read()

    cast = _candidate_names(novel_text, 8)
    chapters = src.get("chapter_count", 1)
    analysis = {
        "title": src.get("title"),
        "char_count": src.get("char_count", 0),
        "chapter_count": chapters,
        "era": "古代",
        "genre_candidates": ["言情", "仙侠"],
        "tone_candidates": ["爽剧", "甜宠"],
        "micro_drama_tags": ["重生", "打脸", "扮猪吃虎"],
        "main_characters": [
            {"name": name, "weight": max(1, 10 - i)} for i, name in enumerate(cast)
        ],
        "storyline": "主线脉络（mock）：重生开局 → 打脸高潮 → 身份反转 → 大结局。",
        "baokuan_elements": [
            {"element": "逆袭起点", "suggested_chapter": 1},
            {"element": "打脸高潮", "suggested_chapter": max(2, chapters // 10)},
            {"element": "身份反转", "suggested_chapter": max(3, chapters // 4)},
        ],
        "paywall_candidates": [8, 20, 40],
        "adaptation_recommendation": {
            "format": "vertical_micro",
            "episodes": 12,
            "per_episode_sec": 90,
            "coverage_chapters": [1, min(chapters, 100)],
        },
        "ai_unfriendly_scenes": ["大规模战争场面", "复杂群戏"],
        "note": "MOCK analysis produced offline by the M1 CLI.",
    }
    rel = project.write_json("analysis/novel.json", analysis)
    project.data["analysis"] = {
        "path": rel,
        "genre_candidates": analysis["genre_candidates"],
        "main_character_count": len(cast),
        "recommended_episodes": analysis["adaptation_recommendation"]["episodes"],
    }
    _add_cost(project, C.SKILL_NOVEL_ANALYST, 0.2)
    project.log_event(C.SKILL_NOVEL_ANALYST, f"analysed novel: {len(cast)} core characters")
    return ExecResult(
        summary=(
            f"全本理解完成：{src.get('char_count', 0)} 字 / {chapters} 章，"
            f"识别 {len(cast)} 个核心角色，推荐竖屏 12 集×90s"
        ),
        artifacts=[rel],
        cost=0.2,
        next_actions=[C.SKILL_SHOW_PLANNER],
    )


# --------------------------------------------------------------------------- #
# 02 show-planner
# --------------------------------------------------------------------------- #
def run_show_planner(project: Project, params: Dict[str, Any]) -> ExecResult:
    analysis_doc = _read_json(project, "analysis/novel.json")
    rec = (analysis_doc or {}).get("adaptation_recommendation", {})
    total_eps = int(params.get("total_episodes") or rec.get("episodes") or 12)
    plan = {
        "coverage": {"chapters": rec.get("coverage_chapters", [1, 100])},
        "episode_format": {
            "type": params.get("format", "vertical_micro"),
            "total_episodes": total_eps,
            "per_episode_sec": int(params.get("per_episode_sec") or rec.get("per_episode_sec") or 90),
        },
        "aspect_ratio": params.get("aspect_ratio", "9:16"),
        "genre": params.get("genre", "仙侠"),
        "era": params.get("era", "古代"),
        "tone": params.get("tone", "爽剧"),
        "micro_drama_tags": (analysis_doc or {}).get("micro_drama_tags", ["重生", "打脸"]),
        "paywall": {
            "free_episodes": int(params.get("free_episodes", 7)),
            "paywall_at_episode": int(params.get("paywall_at", 8)),
            "hook_episodes": (analysis_doc or {}).get("paywall_candidates", [8, 20, 40]),
        },
        "target_platform": params.get("platform", "douyin"),
        "budget_constraints": {
            "total_budget_cny": int(params.get("budget", 5000)),
            "asset_quality_tier": params.get("quality_tier", "standard"),
        },
        "can_merge_characters": [],
        "locked": True,
    }
    project.data["production_plan"] = plan
    rel = "production_plan.yaml"
    miniyaml.dump_to(plan, project.path(rel))
    # cost comparison table (mirror of the orchestrator's A/B/C options)
    comparison = [
        {"option": "A", "desc": "竖屏 12 集×90s", "est_cost_cny": 700},
        {"option": "B", "desc": "横屏 24 集×8min", "est_cost_cny": 12000},
        {"option": "C", "desc": "试播 6 集×90s", "est_cost_cny": 350},
    ]
    project.write_json("plan_comparison.json", comparison)
    _add_cost(project, C.SKILL_SHOW_PLANNER, 0.1)
    project.log_event(C.SKILL_SHOW_PLANNER, f"plan locked: {total_eps} eps {plan['aspect_ratio']}")
    return ExecResult(
        summary=(
            f"制作参数已锁定：{plan['aspect_ratio']} {total_eps} 集 / "
            f"paywall@ep{plan['paywall']['paywall_at_episode']} / {plan['target_platform']}"
        ),
        artifacts=[rel, "plan_comparison.json"],
        cost=0.1,
        next_actions=[C.SKILL_SCRIPT_WRITER],
    )


# --------------------------------------------------------------------------- #
# 03 script-writer
# --------------------------------------------------------------------------- #
def run_script_writer(project: Project, params: Dict[str, Any]) -> ExecResult:
    plan = project.data["production_plan"]
    total = plan["episode_format"]["total_episodes"]
    paywall_at = plan["paywall"]["paywall_at_episode"]
    hooks = set(plan["paywall"].get("hook_episodes", []))

    outline = []
    episodes_meta: List[Dict[str, Any]] = []
    for n in range(1, total + 1):
        is_hook = n in hooks or n == paywall_at
        scene_count = 4 + (n % 3)  # 4-6 scenes per episode (deterministic)
        dialogue_count = scene_count * 3
        title = f"第{n}集"
        outline.append({
            "episode": n,
            "title": title,
            "logline": f"（mock）第 {n} 集剧情大纲，{'超级钩子收尾' if is_hook else '常规推进'}",
            "is_paywall_hook": is_hook,
        })
        ep_script = {
            "episode": n,
            "title": title,
            "is_paywall_hook": is_hook,
            "chapter_mapping": [n],
            "scenes": [
                {
                    "scene_id": f"sc_{i + 1:02d}",
                    "location": f"场景{i + 1}",
                    "summary": f"第{n}集 第{i + 1}场（mock）",
                    "dialogue": [
                        {"character": "角色1", "line": f"第{n}集台词 {i + 1}-{j + 1}（mock）"}
                        for j in range(3)
                    ],
                }
                for i in range(scene_count)
            ],
            "cliffhanger": "超级反转（付费钩子）" if is_hook else "常规悬念",
        }
        rel = project.write_json(f"scripts/episode_{n:03d}.json", ep_script)
        episodes_meta.append({
            "id": n,
            "title": title,
            "is_paywall_hook": is_hook,
            "script_path": rel,
            "scene_count": scene_count,
            "dialogue_count": dialogue_count,
            "shots": [],
            "keyframe_plan_locked": False,
            "output": None,
            "produce": False,
        })

    project.write_json("scripts/outline.json", outline)
    index = {
        "total_episodes": total,
        "paywall_at_episode": paywall_at,
        "hook_episodes": sorted(hooks | {paywall_at}),
        "episodes": [e["id"] for e in episodes_meta],
    }
    project.write_json("scripts/index.json", index)
    project.data["scripts"] = {"index_path": "scripts/index.json", "total_episodes": total}
    project.data["episodes"] = episodes_meta
    _add_cost(project, C.SKILL_SCRIPT_WRITER, total * 0.3)
    project.log_event(C.SKILL_SCRIPT_WRITER, f"generated {total} episode scripts")
    return ExecResult(
        summary=f"全集 {total} 集剧本已就绪（先大纲后扩写），付费集 {sorted(hooks | {paywall_at})}",
        artifacts=["scripts/index.json", "scripts/outline.json"],
        cost=round(total * 0.3, 2),
        next_actions=[C.SKILL_ASSET_EXTRACTOR],
    )


# --------------------------------------------------------------------------- #
# 04 asset-extractor
# --------------------------------------------------------------------------- #
def run_asset_extractor(project: Project, params: Dict[str, Any]) -> ExecResult:
    analysis_doc = _read_json(project, "analysis/novel.json") or {}
    main_chars = analysis_doc.get("main_characters", [])
    characters = []
    for i, ch in enumerate(main_chars):
        weight = int(ch.get("weight", max(1, 8 - i)))
        tier = C.weight_tier(weight)
        role = (
            "protagonist" if weight >= 7 else "supporting" if weight >= 4 else "extra"
        )
        cid = f"char_{i + 1:02d}"
        characters.append({
            "id": cid,
            "name": ch.get("name", cid),
            "role_type": role,
            "weight": weight,
            "tier": tier,
            "voice_id": None,
            "visual_assets": {"reference": False, "three_views": False,
                              "avatar": False, "wardrobe": False},
        })
        project.write_json(
            f"assets/characters/{cid}/meta.json",
            {"id": cid, "name": ch.get("name", cid), "weight": weight, "tier": tier,
             "identity_anchors": {"face_shape": "mock", "hair_signature": "mock",
                                  "color_palette": "#222,#c33", "silhouette": "mock"}},
        )

    scenes = [
        {"id": f"scene_{i + 1:02d}", "name": name, "is_main": i < 3, "reference_image": False}
        for i, name in enumerate(["主角居所", "宗门大殿", "赏花宴", "后山密林"])
    ]
    props = [
        {"id": "prop_01", "name": "玉佩", "importance": "high", "reference_image": False},
        {"id": "prop_02", "name": "古剑", "importance": "medium", "reference_image": False},
        {"id": "prop_03", "name": "茶盏", "importance": "low", "reference_image": False},
    ]
    clues = [
        {"id": "clue_01", "name": "胎记", "reference_image": False,
         "state_changes": ["ep1 隐藏", "ep8 揭示"]},
    ]
    assets = {
        "index_path": "assets/index.json",
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "clues": clues,
    }
    project.write_json("assets/index.json", {k: v for k, v in assets.items() if k != "index_path"})
    project.data["assets"] = assets
    _add_cost(project, C.SKILL_ASSET_EXTRACTOR, 0.5)
    project.log_event(C.SKILL_ASSET_EXTRACTOR,
                      f"{len(characters)} chars / {len(scenes)} scenes / {len(props)} props")
    return ExecResult(
        summary=(
            f"全集资产提取完成：{len(characters)} 角色 / {len(scenes)} 场景 / "
            f"{len(props)} 道具 / {len(clues)} 线索"
        ),
        artifacts=["assets/index.json"],
        cost=0.5,
        next_actions=[C.SKILL_ART_DIRECTOR],
    )


# --------------------------------------------------------------------------- #
# 05 art-director
# --------------------------------------------------------------------------- #
def run_art_director(project: Project, params: Dict[str, Any]) -> ExecResult:
    style_id = params.get("art_style_id") or "2D-chinese-anime"
    project.data["art_style_id"] = style_id
    art_direction = {
        "art_style_id": style_id,
        "rationale": "古装言情/仙侠题材，推荐 2D 国风画风（mock）。",
        "color_grading": "暖金 + 青碧",
        "lighting": "柔光 + 高光氛围",
    }
    rel = project.write_json("assets/art_direction.json", art_direction)
    _add_cost(project, C.SKILL_ART_DIRECTOR, 0.1)
    project.log_event(C.SKILL_ART_DIRECTOR, f"art style = {style_id}")
    return ExecResult(
        summary=f"画风已定调：{style_id}",
        artifacts=[rel],
        cost=0.1,
        next_actions=[C.SKILL_CHARACTER_DESIGNER],
    )


# --------------------------------------------------------------------------- #
# 06 character-designer (target = character / scene / prop / clue)
# --------------------------------------------------------------------------- #
def run_character_designer(project: Project, params: Dict[str, Any]) -> ExecResult:
    target = params.get("target", "character")
    plan = project.data.get("production_plan") or {}
    size = _aspect_size(plan)
    img, config = bk.resolve_image(params.get("image_provider"))
    assets = project.data["assets"]
    total_cost = 0.0
    produced: List[str] = []

    if target == "character":
        for ch in assets["characters"]:
            tier = ch.get("tier", "extra")
            required = C.TIER_REQUIRED_ASSETS.get(tier, ("reference", "avatar"))
            visual = ch["visual_assets"]
            cid = ch["id"]
            # enforce serial generation order: reference first
            order = ["reference", "three_views", "avatar", "wardrobe"]
            for asset in order:
                if asset not in required or visual.get(asset):
                    continue
                res = img.generate(config, ImageRequest(
                    prompt=f"{ch['name']} {asset}", size=size, frame_type="standalone",
                    reference_images=[] if asset == "reference" else [f"{cid}/reference.png"],
                    identity_anchors={"name": ch["name"]},
                ))
                total_cost += res.cost
                fname = "wardrobe/default.png" if asset == "wardrobe" else f"{asset}.png"
                if asset == "three_views":
                    fname = "three_views.png"
                _save_media(project, f"assets/characters/{cid}/{fname}", res, img,
                            f"{asset} for {ch['name']}")
                visual[asset] = True
                produced.append(f"{cid}/{asset}")
        summary = f"角色定妆完成：{len(assets['characters'])} 个角色（按 weight tier 强制四件套）"
    elif target == "scene":
        for sc in assets["scenes"]:
            if sc.get("is_main") and not sc.get("reference_image"):
                res = img.generate(config, ImageRequest(prompt=sc["name"], size=size))
                total_cost += res.cost
                _save_media(project, f"assets/scenes/{sc['id']}/reference.png", res, img,
                            f"scene {sc['name']}")
                sc["reference_image"] = True
                produced.append(sc["id"])
        summary = f"主场景参考图完成：{len(produced)} 张"
    elif target == "prop":
        for prop in assets["props"]:
            if prop.get("importance") in ("high", "medium") and not prop.get("reference_image"):
                res = img.generate(config, ImageRequest(prompt=prop["name"], size=size))
                total_cost += res.cost
                _save_media(project, f"assets/props/{prop['id']}/reference.png", res, img,
                            f"prop {prop['name']}")
                prop["reference_image"] = True
                produced.append(prop["id"])
        summary = f"道具参考图完成：{len(produced)} 张"
    else:  # clue
        for clue in assets["clues"]:
            if not clue.get("reference_image"):
                res = img.generate(config, ImageRequest(prompt=clue["name"], size=size))
                total_cost += res.cost
                _save_media(project, f"assets/clues/{clue['id']}/reference.png", res, img,
                            f"clue {clue['name']}")
                clue["reference_image"] = True
                produced.append(clue["id"])
        summary = f"线索参考图完成：{len(produced)} 张"

    total_cost = round(total_cost, 2)
    _add_cost(project, C.SKILL_CHARACTER_DESIGNER, total_cost)
    project.log_event(C.SKILL_CHARACTER_DESIGNER, f"{target}: {len(produced)} assets")
    return ExecResult(summary=summary, artifacts=produced, cost=total_cost,
                      next_actions=[C.SKILL_CHARACTER_DESIGNER])


# --------------------------------------------------------------------------- #
# 07 storyboard-breaker (episode N)
# --------------------------------------------------------------------------- #
def run_storyboard_breaker(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    script = _read_json(project, ep["script_path"]) or {}
    scenes = script.get("scenes", [])
    shots: List[Dict[str, Any]] = []
    idx = 1
    for sc in scenes:
        # ~2 shots per scene; mark some mergeable
        for k in range(2):
            sid = f"shot_{idx:03d}"
            intensity = 9 if ep.get("is_paywall_hook") and k == 1 else 5
            shot = {
                "id": sid,
                "scene_ref": sc.get("scene_id"),
                "readiness": "storyboard_locked",
                "duration": 5,
                "intensity": intensity,
                "mergeable_with_next": k == 0,
                "image_prompt": f"{sc.get('location')} - shot {idx} (mock)",
                "video_prompt": f"camera move on {sc.get('location')} (mock)",
                "dialogue": sc.get("dialogue", [])[:1],
            }
            project.write_json(
                f"storyboards/episode_{n:03d}/{sid}/meta.json", shot
            )
            shots.append(shot)
            idx += 1
    ep["shots"] = shots
    ep["keyframe_plan_locked"] = False
    _add_cost(project, C.SKILL_STORYBOARD_BREAKER, 0.2)
    project.log_event(C.SKILL_STORYBOARD_BREAKER, f"ep{n}: {len(shots)} shots")
    return ExecResult(
        summary=f"第 {n} 集分镜完成：{len(shots)} 个镜头（标记 mergeable + intensity）",
        artifacts=[f"storyboards/episode_{n:03d}/"],
        cost=0.2,
        next_actions=[C.SKILL_KEYFRAME_PLANNER],
    )


# --------------------------------------------------------------------------- #
# 08a keyframe-planner (episode N)
# --------------------------------------------------------------------------- #
def run_keyframe_planner(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    plan_rows = []
    for shot in ep["shots"]:
        mode = "first_last" if shot.get("intensity", 0) >= 8 else "image2video"
        plan_rows.append({
            "shot": shot["id"],
            "generation_mode": mode,
            "batch": 1,
            "reason": "high-intensity precise control" if mode == "first_last" else "default i2v",
        })
    rel = f"storyboards/episode_{n:03d}/keyframe_plan.yaml"
    miniyaml.dump_to({"episode": n, "locked": True, "shots": plan_rows}, project.path(rel))
    ep["keyframe_plan_locked"] = True
    ep["keyframe_plan"] = {"locked": True, "modes": {r["shot"]: r["generation_mode"] for r in plan_rows}}
    _add_cost(project, C.SKILL_KEYFRAME_PLANNER, 0.1)
    project.log_event(C.SKILL_KEYFRAME_PLANNER, f"ep{n}: keyframe plan locked")
    n_fl = sum(1 for r in plan_rows if r["generation_mode"] == "first_last")
    return ExecResult(
        summary=f"第 {n} 集关键帧方案已审阅锁定：{len(plan_rows)} 镜（{n_fl} 个首尾帧）",
        artifacts=[rel],
        cost=0.1,
        next_actions=[C.SKILL_KEYFRAME_GENERATOR],
    )


# --------------------------------------------------------------------------- #
# 08 keyframe-generator (episode N)
# --------------------------------------------------------------------------- #
def run_keyframe_generator(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    plan = project.data.get("production_plan") or {}
    size = _aspect_size(plan)
    img, config = bk.resolve_image(params.get("image_provider"))
    modes = (ep.get("keyframe_plan") or {}).get("modes", {})
    total_cost = 0.0
    count = 0
    for shot in ep["shots"]:
        mode = modes.get(shot["id"], "image2video")
        base = f"storyboards/episode_{n:03d}/{shot['id']}"
        res = img.generate(config, ImageRequest(prompt=shot["image_prompt"], size=size,
                                                frame_type="start"))
        total_cost += res.cost
        _save_media(project, f"{base}/start_frame.png", res, img, "start frame")
        if mode == "first_last":
            res2 = img.generate(config, ImageRequest(prompt=shot["image_prompt"], size=size,
                                                     frame_type="end"))
            total_cost += res2.cost
            _save_media(project, f"{base}/end_frame.png", res2, img, "end frame")
        shot["readiness"] = "keyframes_locked"  # batch=1 -> auto-lock (no candidate wait)
        count += 1
    total_cost = round(total_cost, 2)
    _add_cost(project, C.SKILL_KEYFRAME_GENERATOR, total_cost)
    project.log_event(C.SKILL_KEYFRAME_GENERATOR, f"ep{n}: {count} keyframes")
    return ExecResult(
        summary=f"第 {n} 集关键帧生成完成：{count} 镜（batch=1 自动锁定）",
        artifacts=[f"storyboards/episode_{n:03d}/"],
        cost=total_cost,
        next_actions=[C.SKILL_VIDEO_GENERATOR],
    )


# --------------------------------------------------------------------------- #
# 09 video-generator (episode N)
# --------------------------------------------------------------------------- #
def run_video_generator(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    plan = project.data.get("production_plan") or {}
    aspect = plan.get("aspect_ratio", "9:16")
    resolution = (plan.get("budget_constraints") or {}).get("resolution", "1080p")
    vid, config = bk.resolve_video(params.get("video_provider"))
    modes = (ep.get("keyframe_plan") or {}).get("modes", {})
    total_cost = 0.0
    count = 0
    for shot in ep["shots"]:
        base = f"storyboards/episode_{n:03d}/{shot['id']}"
        mode = modes.get(shot["id"], "image2video")
        res = vid.generate(config, VideoRequest(
            prompt=shot["video_prompt"], generation_mode=mode,
            first_frame_url=f"{base}/start_frame.png",
            last_frame_url=f"{base}/end_frame.png" if mode == "first_last" else None,
            duration=shot.get("duration", 5), aspect_ratio=aspect, resolution=resolution,
        ))
        total_cost += res.cost
        _save_media(project, f"{base}/clip.mp4", res, vid, "video clip")
        shot["readiness"] = "video_locked"
        count += 1
    total_cost = round(total_cost, 2)
    _add_cost(project, C.SKILL_VIDEO_GENERATOR, total_cost)
    project.log_event(C.SKILL_VIDEO_GENERATOR, f"ep{n}: {count} clips, ¥{total_cost}")
    return ExecResult(
        summary=f"第 {n} 集视频片段生成完成：{count} 镜，本集成本 ¥{total_cost}",
        artifacts=[f"storyboards/episode_{n:03d}/"],
        cost=total_cost,
        next_actions=[C.SKILL_VOICE_ASSIGNER, C.SKILL_TTS_SYNTHESIZER],
    )


# --------------------------------------------------------------------------- #
# 10 voice-assigner (first time only)
# --------------------------------------------------------------------------- #
_VOICE_CATALOG = [
    {"voice_id": "zh_female_warm", "gender": "female", "tags": ["温柔", "甜美"]},
    {"voice_id": "zh_male_deep", "gender": "male", "tags": ["低沉", "磁性"]},
    {"voice_id": "zh_female_lively", "gender": "female", "tags": ["活泼"]},
    {"voice_id": "zh_male_young", "gender": "male", "tags": ["少年感"]},
]


def run_voice_assigner(project: Project, params: Dict[str, Any]) -> ExecResult:
    assets = project.data["assets"]
    assigned = 0
    for i, ch in enumerate(assets["characters"]):
        if ch.get("role_type") in ("protagonist", "supporting") and not ch.get("voice_id"):
            voice = _VOICE_CATALOG[i % len(_VOICE_CATALOG)]
            ch["voice_id"] = voice["voice_id"]
            assigned += 1
    project.data["voices_assigned"] = True
    project.write_json("assets/voice_assignment.json",
                       {ch["id"]: ch.get("voice_id") for ch in assets["characters"]})
    _add_cost(project, C.SKILL_VOICE_ASSIGNER, 0.05)
    project.log_event(C.SKILL_VOICE_ASSIGNER, f"{assigned} voices assigned")
    return ExecResult(
        summary=f"音色分配完成：{assigned} 个有台词角色",
        artifacts=["assets/voice_assignment.json"],
        cost=0.05,
        next_actions=[C.SKILL_TTS_SYNTHESIZER],
    )


# --------------------------------------------------------------------------- #
# 11 tts-synthesizer (episode N)
# --------------------------------------------------------------------------- #
def run_tts_synthesizer(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    assets = project.data["assets"]
    voice_by_name = {c["name"]: c.get("voice_id") for c in assets["characters"]}
    default_voice = _VOICE_CATALOG[0]["voice_id"]
    tts, config = bk.resolve_tts(params.get("tts_provider"))
    total_cost = 0.0
    lines = 0
    for shot in ep["shots"]:
        base = f"storyboards/episode_{n:03d}/{shot['id']}"
        dialogue = shot.get("dialogue", [])
        if dialogue:
            line = dialogue[0]
            voice_id = voice_by_name.get(line.get("character"), default_voice)
            res = tts.synthesize(config, TTSRequest(voice_id=voice_id, text=line.get("line", "")))
            total_cost += res.cost
            _save_media(project, f"{base}/audio.wav", res, tts,
                        f"tts {voice_id} ({res.duration_sec}s)")
            lines += 1
        # TTS is the last per-shot media step (keyframes + video + audio all
        # locked), so the shot is now fully ready for the composer. We pass
        # through audio_locked straight to shot_ready -- the composer's gate
        # (step 16) requires every shot to be shot_ready.
        shot["readiness"] = "shot_ready"
    total_cost = round(total_cost, 2)
    _add_cost(project, C.SKILL_TTS_SYNTHESIZER, total_cost)
    project.log_event(C.SKILL_TTS_SYNTHESIZER, f"ep{n}: {lines} lines synthesized")
    return ExecResult(
        summary=f"第 {n} 集配音合成完成：{lines} 句对白",
        artifacts=[f"storyboards/episode_{n:03d}/"],
        cost=total_cost,
        next_actions=[C.SKILL_VIDEO_COMPOSER],
    )


# --------------------------------------------------------------------------- #
# 12 video-composer (episode N)
# --------------------------------------------------------------------------- #
def run_video_composer(project: Project, params: Dict[str, Any]) -> ExecResult:
    n = int(params["episode"])
    ep = project.episode(n)
    if ep is None:
        return ExecResult(status="error", summary=f"episode {n} not found")
    shots = ep["shots"]
    total_dur = sum(s.get("duration", 5) for s in shots)
    is_hook = ep.get("is_paywall_hook")
    final_rel = f"output/episode_{n}_final.mp4"
    manifest = {
        "episode": n,
        "shot_count": len(shots),
        "duration_sec": total_dur,
        "structure": ["Logo", "Recap", "Body", "Tease", "Outro"],
        "burn_in_subtitles": True,
        "paywall_hook": bool(is_hook),
        "conversion_copy": "下集更精彩，解锁查看" if is_hook else None,
        "clips": [f"storyboards/episode_{n:03d}/{s['id']}/clip.mp4" for s in shots],
    }
    _placeholder(project, final_rel, f"composed final cut: {manifest['structure']}")
    project.write_json(f"output/episode_{n}.manifest.json", manifest)
    _placeholder(project, f"output/episode_{n}.jianying.zip",
                 "jianying draft package (mock zip)")
    for shot in shots:
        shot["readiness"] = "shot_ready"
    ep["output"] = final_rel
    _add_cost(project, C.SKILL_VIDEO_COMPOSER, 0.3)
    project.log_event(C.SKILL_VIDEO_COMPOSER, f"ep{n}: composed {total_dur}s")
    return ExecResult(
        summary=(
            f"第 {n} 集成片完成：{len(shots)} 镜 / {total_dur}s，"
            f"输出 {final_rel} + 剪映草稿"
        ),
        artifacts=[final_rel, f"output/episode_{n}.jianying.zip"],
        cost=0.3,
    )


def _read_json(project: Project, rel_path: str) -> Optional[Dict[str, Any]]:
    import json
    full = project.path(rel_path)
    try:
        with open(full, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# dispatch table
# --------------------------------------------------------------------------- #
EXECUTORS = {
    C.SKILL_NOVEL_ANALYST: run_novel_analyst,
    C.SKILL_SHOW_PLANNER: run_show_planner,
    C.SKILL_SCRIPT_WRITER: run_script_writer,
    C.SKILL_ASSET_EXTRACTOR: run_asset_extractor,
    C.SKILL_ART_DIRECTOR: run_art_director,
    C.SKILL_CHARACTER_DESIGNER: run_character_designer,
    C.SKILL_STORYBOARD_BREAKER: run_storyboard_breaker,
    C.SKILL_KEYFRAME_PLANNER: run_keyframe_planner,
    C.SKILL_KEYFRAME_GENERATOR: run_keyframe_generator,
    C.SKILL_VIDEO_GENERATOR: run_video_generator,
    C.SKILL_VOICE_ASSIGNER: run_voice_assigner,
    C.SKILL_TTS_SYNTHESIZER: run_tts_synthesizer,
    C.SKILL_VIDEO_COMPOSER: run_video_composer,
}
