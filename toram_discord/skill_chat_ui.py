from __future__ import annotations

from pathlib import Path

import discord

from toram_skill_search.models import SkillDetailPayload
from toram_skills.repository import SkillRepository

from .render import truncate_discord_text
from .sessions import DiscordSessionManager, SessionKey
from .skill_detail_pages import build_skill_detail_pages
from .skill_ui import (
    SkillDetailView,
    SkillRenderedMessage,
    build_skill_detail_message,
)


def run_skill_detail_by_id_sync(
    database_path: Path,
    skill_id: str,
) -> SkillDetailPayload:
    """Load one canonical skill detail payload without semantic retrieval."""
    with SkillRepository(Path(database_path).expanduser().resolve()) as repository:
        skill = repository.get_skill(skill_id)
        tree = repository.get_tree(skill.tree_id)
    return SkillDetailPayload(skill, tree)


def build_skill_chat_detail_message(
    payload: SkillDetailPayload,
    explanation: str,
    *,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
) -> SkillRenderedMessage:
    """Reuse the normal rich skill detail UI and append a grounded explanation."""
    pages = build_skill_detail_pages(payload)
    view = None
    if len(pages) > 1:
        view = SkillDetailView(
            sessions=sessions,
            key=key,
            generation=generation,
            pages=pages,
            page_index=0,
            detail_payload=payload,
            results_payload=None,
        )

    rendered = build_skill_detail_message(payload, 0, view=view)
    explanation_embed = discord.Embed(
        title="Explanation",
        description=truncate_discord_text(
            explanation.strip() or "No explanation available.",
            4096,
        ),
    )
    return SkillRenderedMessage(
        embeds=rendered.embeds + (explanation_embed,),
        files=rendered.files,
        view=rendered.view,
    )


__all__ = [
    "build_skill_chat_detail_message",
    "run_skill_detail_by_id_sync",
]
