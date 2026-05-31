"""Minimal, dependency-free YAML emitter.

Agents Studio's M1 CLI MVP runs on the Python standard library only (the
sandbox is offline). PyYAML is therefore unavailable, so we ship a tiny
emitter that covers exactly the subset we produce:

    - nested mappings (dict)
    - block sequences of scalars and of mappings (list)
    - scalars: str / int / float / bool / None

This is intentionally NOT a general YAML implementation. ``project.json`` is
the single source of truth (see docs/ARCHITECTURE.md s7); the ``*.yaml`` files
we emit (e.g. ``production_plan.yaml``) are human-readable *mirrors* of that
truth, so a one-way dumper is all we need.
"""

from __future__ import annotations

from typing import Any, List

__all__ = ["dumps", "dump_to"]

_NEEDS_QUOTE = set(":#{}[],&*!|>'\"%@`")


def _scalar(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    text = str(value)
    if text == "":
        return '""'
    # Quote when the scalar could otherwise be misparsed as structure.
    risky = (
        text[0] in _NEEDS_QUOTE
        or text[0] in "-?"
        or text != text.strip()
        or any(ch in text for ch in (": ", " #", "\n", "\t"))
        or text.lower() in {"null", "true", "false", "yes", "no", "~"}
    )
    if risky:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        return f'"{escaped}"'
    return text


def _emit(value: Any, indent: int, lines: List[str]) -> None:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            lines[-1] += " {}"
            return
        for key, val in value.items():
            if isinstance(val, dict) and val:
                lines.append(f"{pad}{key}:")
                _emit(val, indent + 1, lines)
            elif isinstance(val, list) and val:
                lines.append(f"{pad}{key}:")
                _emit(val, indent, lines)  # sequences align with their key
            else:
                lines.append(f"{pad}{key}: {_inline(val)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item:
                # "- key: value" with continuation indented to match.
                first = True
                for key, val in item.items():
                    prefix = f"{pad}- " if first else f"{pad}  "
                    first = False
                    if isinstance(val, (dict, list)) and val:
                        lines.append(f"{prefix}{key}:")
                        _emit(val, indent + 2, lines)
                    else:
                        lines.append(f"{prefix}{key}: {_inline(val)}")
            else:
                lines.append(f"{pad}- {_inline(item)}")
    else:
        lines.append(f"{pad}{_scalar(value)}")


def _inline(value: Any) -> str:
    """Render empty containers / scalars on a single line."""
    if isinstance(value, dict):
        return "{}"
    if isinstance(value, list):
        return "[]"
    return _scalar(value)


def dumps(data: Any) -> str:
    """Serialize *data* to a YAML string."""
    lines: List[str] = []
    if isinstance(data, (dict, list)):
        _emit(data, 0, lines)
    else:
        lines.append(_scalar(data))
    return "\n".join(lines) + "\n"


def dump_to(data: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(dumps(data))
