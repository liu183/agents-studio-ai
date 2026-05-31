"""Load SKILL.md files and parse their YAML frontmatter.

We only need the small, well-known frontmatter subset the skills actually use
(see skills/README.md): ``name``, ``description``, ``agent_type``,
``content_modes`` (flow list), ``required_tools`` (block list), plus a few
optional scalars. A targeted parser is more robust here than a general YAML
loader -- ``description`` values legitimately contain colons and quotes.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_FLOW_LIST = re.compile(r"^\[(.*)\]$")


@dataclass
class Skill:
    skill_id: str  # directory name, e.g. "07-storyboard-breaker"
    path: str  # path to SKILL.md
    name: str = ""
    description: str = ""
    agent_type: str = ""
    content_modes: List[str] = field(default_factory=list)
    required_tools: List[str] = field(default_factory=list)
    extra: Dict[str, str] = field(default_factory=dict)


def _split_frontmatter(text: str) -> str:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return ""
    out: List[str] = []
    for line in lines[1:]:
        if line.strip() == "---":
            break
        out.append(line)
    return "\n".join(out)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _parse_flow_list(value: str) -> List[str]:
    inner = _FLOW_LIST.match(value.strip())
    if not inner:
        return []
    body = inner.group(1).strip()
    if not body:
        return []
    return [_strip_quotes(item) for item in body.split(",") if item.strip()]


def parse_frontmatter(text: str) -> Dict[str, object]:
    """Parse the limited frontmatter subset into a dict."""
    block = _split_frontmatter(text)
    result: Dict[str, object] = {}
    current_list_key: Optional[str] = None

    for raw in block.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        # block-list item belonging to the most recent "key:" with empty value
        stripped = raw.strip()
        if stripped.startswith("- ") and current_list_key:
            result.setdefault(current_list_key, [])
            result[current_list_key].append(_strip_quotes(stripped[2:]))  # type: ignore[union-attr]
            continue

        if ":" not in raw:
            continue
        key, _, value = raw.partition(":")
        key = key.strip()
        value = value.strip()
        current_list_key = None

        if value == "":
            # could be a block list opener; remember the key
            current_list_key = key
            result[key] = []
        elif _FLOW_LIST.match(value):
            result[key] = _parse_flow_list(value)
        else:
            result[key] = _strip_quotes(value)
    return result


def load_skill(skill_dir: str) -> Optional[Skill]:
    md_path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.exists(md_path):
        return None
    with open(md_path, "r", encoding="utf-8") as handle:
        text = handle.read()
    fm = parse_frontmatter(text)
    known = {"name", "description", "agent_type", "content_modes", "required_tools"}
    extra = {k: v for k, v in fm.items() if k not in known and isinstance(v, str)}
    return Skill(
        skill_id=os.path.basename(skill_dir.rstrip("/")),
        path=md_path,
        name=str(fm.get("name", "")),
        description=str(fm.get("description", "")),
        agent_type=str(fm.get("agent_type", "")),
        content_modes=list(fm.get("content_modes", []) or []),
        required_tools=list(fm.get("required_tools", []) or []),
        extra=extra,
    )


def load_all_skills(skills_root: str) -> List[Skill]:
    if not os.path.isdir(skills_root):
        return []
    skills: List[Skill] = []
    for entry in sorted(os.listdir(skills_root)):
        full = os.path.join(skills_root, entry)
        if os.path.isdir(full):
            skill = load_skill(full)
            if skill:
                skills.append(skill)
    return skills


def find_skills_root(start: str) -> Optional[str]:
    """Walk upward from *start* looking for a ``skills/`` directory."""
    cur = os.path.abspath(start)
    while True:
        candidate = os.path.join(cur, "skills")
        if os.path.isdir(candidate) and os.path.exists(
            os.path.join(candidate, "00-orchestrator", "SKILL.md")
        ):
            return candidate
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent
