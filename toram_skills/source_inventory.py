from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

_GROUPS = {
    "assist_skills": "assist",
    "other_skill_trees": "other",
    "sub_weapon_skills": "sub-weapon",
    "weapon_class_skills": "weapon-class",
}
_CATEGORY_RE = re.compile(r"(?mi)^Category:\s*(.+?)\s*$")
_SKILL_MARKER_RE = re.compile(r"(?mi)^SKILL:\s*.+?\s*$")


@dataclass(frozen=True)
class SkillSource:
    path: Path
    relative_path: str
    tree_group: str
    text: str
    declared_category: str | None
    marker_count: int


def discover_skill_sources(raw_root: Path) -> tuple[SkillSource, ...]:
    root = Path(raw_root)
    result: list[SkillSource] = []
    for path in sorted(root.rglob("*.txt")):
        relative = path.relative_to(root).as_posix()
        group_dir = Path(relative).parts[0]
        if group_dir not in _GROUPS:
            raise ValueError(f"Unsupported raw skill group: {group_dir}")
        text = path.read_text(encoding="utf-8")
        category = _CATEGORY_RE.search(text)
        result.append(
            SkillSource(
                path=path,
                relative_path=relative,
                tree_group=_GROUPS[group_dir],
                text=text,
                declared_category=category.group(1).strip() if category else None,
                marker_count=len(_SKILL_MARKER_RE.findall(text)),
            )
        )
    return tuple(result)


def source_manifest_hash(sources: tuple[SkillSource, ...]) -> str:
    digest = sha256()
    for source in sources:
        digest.update(source.relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(source.text.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()
