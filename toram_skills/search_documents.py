from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from .models import SkillDraft, SkillTreeDraft


@dataclass(frozen=True)
class SkillSearchDocument:
    id: str
    skill_id: str
    position: int
    kind: str
    label: str | None
    text: str
    text_hash: str


def _text_hash(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _summary_text(tree: SkillTreeDraft, skill: SkillDraft) -> str:
    lines: list[str] = [
        f"Skill: {skill.name}",
        f"Tree: {tree.name}",
    ]
    if skill.aliases:
        lines.append("Aliases: " + ", ".join(skill.aliases))
    if skill.tier is not None:
        lines.append(f"Tier: {skill.tier}")
    if skill.required_level is not None:
        lines.append(f"Required Level: {skill.required_level}")
    if skill.skill_type:
        lines.append(f"Type: {skill.skill_type}")
    if skill.mp_cost_text:
        lines.append(f"MP Cost: {skill.mp_cost_text}")
    if skill.damage_type:
        lines.append(f"Damage Type: {skill.damage_type}")
    if skill.element:
        lines.append(f"Element: {skill.element}")
    if skill.cast_range_text:
        lines.append(f"Cast Range: {skill.cast_range_text}")
    if skill.hit_range_text:
        lines.append(f"Hit Range: {skill.hit_range_text}")
    if skill.cast_time_text:
        lines.append(f"Cast Time: {skill.cast_time_text}")
    if skill.hit_count_text:
        lines.append(f"Hit Count: {skill.hit_count_text}")
    if skill.ailments:
        lines.append("Ailments: " + ", ".join(skill.ailments))
    if skill.weapon_requirements:
        lines.append("Weapon Requirements: " + ", ".join(skill.weapon_requirements))
    if skill.weapon_restrictions:
        lines.append("Weapon Restrictions: " + ", ".join(skill.weapon_restrictions))
    if tree.weapon_restrictions:
        lines.append("Tree Weapon Restrictions: " + ", ".join(tree.weapon_restrictions))
    if skill.description:
        lines.append(f"Description: {skill.description}")
    if skill.game_description:
        lines.append(f"Game Description: {skill.game_description}")
    if skill.sections:
        lines.append("Sections: " + ", ".join(section.label for section in skill.sections))
    return "\n".join(lines).strip()


def _document(
    *,
    document_id: str,
    skill_id: str,
    position: int,
    kind: str,
    label: str | None,
    text: str,
) -> SkillSearchDocument:
    cleaned = text.strip()
    return SkillSearchDocument(
        id=document_id,
        skill_id=skill_id,
        position=position,
        kind=kind,
        label=label,
        text=cleaned,
        text_hash=_text_hash(cleaned),
    )


def build_search_documents(
    tree: SkillTreeDraft,
    skill: SkillDraft,
) -> tuple[SkillSearchDocument, ...]:
    documents: list[SkillSearchDocument] = [
        _document(
            document_id=f"{skill.id}#summary",
            skill_id=skill.id,
            position=0,
            kind="summary",
            label=None,
            text=_summary_text(tree, skill),
        )
    ]

    for section in skill.sections:
        body = section.body.strip()
        if not body:
            continue
        documents.append(
            _document(
                document_id=f"{skill.id}#section:{section.position}",
                skill_id=skill.id,
                position=len(documents),
                kind="section",
                label=section.label,
                text=f"{section.label}\n{body}",
            )
        )

    if not skill.sections and skill.raw_text.strip():
        documents.append(
            _document(
                document_id=f"{skill.id}#source",
                skill_id=skill.id,
                position=len(documents),
                kind="source",
                label=None,
                text=skill.raw_text,
            )
        )

    return tuple(documents)


def search_document_manifest_hash(
    documents: tuple[SkillSearchDocument, ...],
) -> str:
    digest = sha256()
    for document in documents:
        digest.update(document.id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(document.text_hash.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


__all__ = [
    "SkillSearchDocument",
    "build_search_documents",
    "search_document_manifest_hash",
]
