"""Project file-system manager.

``project.json`` is the single source of truth for a project (see
docs/ARCHITECTURE.md s7). Everything else on disk -- ``production_plan.yaml``,
``scripts/``, ``assets/``, ``storyboards/``, ``output/`` -- is a generated
mirror of state recorded here. Copying the whole ``projects/<name>/`` directory
is therefore a complete project export.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from . import constants as C

# Sub-directories created for every project (mirrors docs/ARCHITECTURE.md s7).
SUBDIRS = (
    "source",
    "analysis",
    "scripts",
    "assets",
    "assets/characters",
    "assets/scenes",
    "assets/props",
    "assets/clues",
    "storyboards",
    "versions",
    "output",
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return slug or "project"


class Project:
    """Thin wrapper around the ``project.json`` document."""

    def __init__(self, root: str, data: Dict[str, Any]):
        self.root = os.path.abspath(root)
        self.data = data

    # ---- persistence -------------------------------------------------------
    @property
    def file_path(self) -> str:
        return os.path.join(self.root, C.PROJECT_FILE)

    def save(self) -> None:
        self.data["updated_at"] = _now()
        with open(self.file_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")

    # ---- path helpers ------------------------------------------------------
    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def ensure_dir(self, *parts: str) -> str:
        target = self.path(*parts)
        os.makedirs(target, exist_ok=True)
        return target

    def write_json(self, rel_path: str, payload: Any) -> str:
        full = self.path(rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        return rel_path

    def write_text(self, rel_path: str, text: str) -> str:
        full = self.path(rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(text)
        return rel_path

    def write_bytes(self, rel_path: str, data: bytes) -> str:
        full = self.path(rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as handle:
            handle.write(data)
        return rel_path

    # ---- convenience accessors --------------------------------------------
    @property
    def name(self) -> str:
        return self.data.get("name", "")

    @property
    def episodes(self) -> List[Dict[str, Any]]:
        return self.data.setdefault("episodes", [])

    def episode(self, episode_id: int) -> Optional[Dict[str, Any]]:
        for ep in self.episodes:
            if ep.get("id") == episode_id:
                return ep
        return None

    def log_event(self, skill: str, summary: str) -> None:
        self.data.setdefault("log", []).append(
            {"ts": _now(), "skill": skill, "summary": summary}
        )


def _blank_document(name: str, content_mode: str) -> Dict[str, Any]:
    return {
        "schema_version": C.SCHEMA_VERSION,
        "name": name,
        "content_mode": content_mode,
        "created_at": _now(),
        "updated_at": _now(),
        "source": {
            "novel_path": None,
            "title": None,
            "char_count": 0,
            "chapter_count": 0,
        },
        "analysis": None,
        "production_plan": None,
        "art_style_id": None,
        "scripts": None,
        "assets": None,
        "voices_assigned": False,
        "episodes": [],
        "log": [],
    }


class ProjectManager:
    """Create, locate and load projects under a workspace ``projects/`` dir."""

    def __init__(self, workspace: Optional[str] = None):
        self.workspace = os.path.abspath(workspace or os.getcwd())

    def projects_dir(self) -> str:
        return os.path.join(self.workspace, "projects")

    def project_root(self, name: str) -> str:
        return os.path.join(self.projects_dir(), slugify(name))

    # ---- create ------------------------------------------------------------
    def create(
        self,
        name: str,
        content_mode: str = "drama",
        novel_path: Optional[str] = None,
        novel_text: Optional[str] = None,
    ) -> Project:
        if content_mode not in C.CONTENT_MODES:
            raise ValueError(f"unknown content_mode: {content_mode}")
        root = self.project_root(name)
        if os.path.exists(os.path.join(root, C.PROJECT_FILE)):
            raise FileExistsError(f"project already exists: {root}")
        os.makedirs(root, exist_ok=True)
        for sub in SUBDIRS:
            os.makedirs(os.path.join(root, sub), exist_ok=True)

        project = Project(root, _blank_document(slugify(name), content_mode))

        if novel_path:
            with open(novel_path, "r", encoding="utf-8", errors="replace") as handle:
                novel_text = handle.read()
        if novel_text is not None:
            project.write_text("source/novel.txt", novel_text)
            project.data["source"]["novel_path"] = "source/novel.txt"
            project.data["source"]["char_count"] = len(novel_text)
            project.data["source"]["chapter_count"] = _count_chapters(novel_text)
            project.data["source"]["title"] = _guess_title(novel_text, name)

        project.write_text("CLAUDE.md", _claude_md(project))
        project.save()
        return project

    # ---- load / locate -----------------------------------------------------
    def load(self, name_or_path: str) -> Project:
        root = self._resolve_root(name_or_path)
        file_path = os.path.join(root, C.PROJECT_FILE)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"no project.json under {root}")
        with open(file_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return Project(root, data)

    def _resolve_root(self, name_or_path: str) -> str:
        candidates = [
            name_or_path,
            os.path.join(name_or_path, ""),
            self.project_root(name_or_path),
        ]
        for cand in candidates:
            if os.path.exists(os.path.join(cand, C.PROJECT_FILE)):
                return os.path.abspath(cand)
        return os.path.abspath(self.project_root(name_or_path))

    def list_projects(self) -> List[str]:
        base = self.projects_dir()
        if not os.path.isdir(base):
            return []
        found = []
        for entry in sorted(os.listdir(base)):
            if os.path.exists(os.path.join(base, entry, C.PROJECT_FILE)):
                found.append(entry)
        return found


def _count_chapters(text: str) -> int:
    # Heuristic: count "第N章" style markers; fall back to paragraph blocks.
    matches = re.findall(r"第\s*[0-9一二三四五六七八九十百千]+\s*章", text)
    if matches:
        return len(matches)
    blocks = [b for b in text.split("\n\n") if b.strip()]
    return max(1, len(blocks))


def _guess_title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if line:
            return line[:40]
    return fallback


def _claude_md(project: Project) -> str:
    return (
        f"# {project.name}\n\n"
        f"> content_mode: `{project.data['content_mode']}`\n\n"
        "This file is the project-level system prompt for the Agents Studio\n"
        "orchestrator. The orchestrator reads `project.json` to detect the\n"
        "current pipeline stage and dispatches the matching skill under\n"
        "`skills/`. See `skills/00-orchestrator/SKILL.md` for the routing table.\n\n"
        "Do not edit generated mirror files by hand; edit via the CLI so that\n"
        "`project.json` (the single source of truth) stays consistent.\n"
    )
