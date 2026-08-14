from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Literal

import discord
import search_items as core

from toram_search.reconstruction import try_reconstruct_simple_search
from toram_search.routing import route_deterministically
from toram_search.service import (
    ExpressionResultsPayload,
    ItemDetailPayload,
    ItemResultsPayload,
    SearchService,
    ServiceOutcome,
    StatResultsPayload,
    UpgradeDetailPayload,
    UpgradeResultsPayload,
)
from toram_search.session import PendingItemSearch
from toram_search.understanding import understand_item_query
from toram_skill_chat.llm import OllamaSkillRagClient
from toram_skill_chat.models import SkillChatResult
from toram_skill_chat.rag import GroundedSkillRag
from toram_skill_chat.retrieval import SkillEvidenceRetriever
from toram_skill_chat.service import SkillChatService
from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
from toram_skills.repository import SkillRepository
from toram_skills.schema import SchemaError

from .sessions import DatabaseChatContext, DiscordSessionManager, SessionKey
from .views import ActionButton, SessionBoundView


DatabaseChatKind = Literal["item", "skill", "mixed", "fallback"]
_SHARED_DISCOVERY_RE = re.compile(
    r"^\s*what\s+(?:gives?|provides?|has)\s+(.+?)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_SHARED_TAIL_RE = re.compile(
    r"\s+and\s+(?:how|what)\s+does\s+(?:it|that)\b.*$",
    re.IGNORECASE,
)
_NATURAL_CHAT_PREFIXES = ("what ", "which ", "how ", "compare ", "only ")


@dataclass(frozen=True)
class DatabaseChatOutcome:
    kind: DatabaseChatKind
    item_outcome: ServiceOutcome | None = None
    skill_result: SkillChatResult | None = None
    item_ids: tuple[int, ...] = ()
    skill_ids: tuple[str, ...] = ()


class _RoutingOnlyItemLlm:
    def complete(self, *args, **kwargs):
        raise RuntimeError("Item LLM is disabled during deterministic database-chat probing")


def _shared_discovery_concept(query: str) -> str | None:
    cleaned = _SHARED_TAIL_RE.sub("", " ".join(str(query).split()))
    match = _SHARED_DISCOVERY_RE.match(cleaned)
    if match is None:
        return None
    concept = match.group(1).strip(" ?!.")
    return concept or None


def is_database_chat_candidate(query: str, context: DatabaseChatContext) -> bool:
    normalized = " ".join(str(query).casefold().split())
    if _shared_discovery_concept(query) is not None:
        return True
    if normalized.startswith(_NATURAL_CHAT_PREFIXES):
        return True
    if context.active_domain in {"skill", "mixed"}:
        return normalized.startswith(("it ", "its "))
    return False


def probe_item_deterministically(repository, query: str) -> ServiceOutcome | None:
    service = SearchService(repository, llm_client=_RoutingOnlyItemLlm())
    route = route_deterministically(
        query, repository, service.all_items, service.help_service, service.database_service
    )
    if route.kind == "search":
        if route.parsed is None:
            return None
        return ServiceOutcome("search", payload=service._materialize(route.parsed, {}))
    if route.kind == "help":
        return ServiceOutcome("help", text=route.help_text or "")
    if route.kind == "database":
        if route.database_request is None:
            return None
        return ServiceOutcome("database", text=service.database_service.execute(route.database_request))
    if route.kind == "refuse":
        return ServiceOutcome("refuse")

    reconstruction = try_reconstruct_simple_search(
        query,
        available_stats=repository.list_stat_names(),
        available_item_types=repository.list_item_types(),
    )
    if reconstruction.kind in {"success", "ambiguous"} and reconstruction.canonical_query:
        reconstructed = service._execute_canonical_item_search(reconstruction.canonical_query)
        if reconstructed.kind == "search":
            return reconstructed

    understanding = understand_item_query(
        query,
        available_stats=repository.list_stat_names(),
        available_item_types=repository.list_item_types(),
    )
    if understanding.decision == "execute" and understanding.canonical_query:
        understood = service._execute_canonical_item_search(understanding.canonical_query)
        if understood.kind == "search":
            return understood
    elif understanding.decision in {"clarify", "confirm", "suggest"}:
        return ServiceOutcome(
            "item_understanding",
            pending_item_search=PendingItemSearch(query, understanding),
        )
    return None


def _item_ids(outcome: ServiceOutcome | None) -> tuple[int, ...]:
    if outcome is None or outcome.kind != "search" or outcome.payload is None:
        return ()
    payload = outcome.payload
    if isinstance(payload, ItemDetailPayload):
        return (int(payload.detail.id),)
    if isinstance(payload, ItemResultsPayload):
        return tuple(int(row.item.id) for row in payload.results)
    if isinstance(payload, (StatResultsPayload, ExpressionResultsPayload)):
        return tuple(int(row.item.id) for row in payload.results)
    if isinstance(payload, UpgradeDetailPayload):
        return (int(payload.selected_item_id),)
    if isinstance(payload, UpgradeResultsPayload):
        return tuple(int(row.item.id) for row in payload.results)
    return ()


def _meaningful_skill(result: SkillChatResult | None) -> bool:
    return result is not None and result.kind in {
        "structured", "results", "answer", "clarify", "refuse"
    }


def _skill_discovery_result(
    retriever: SkillEvidenceRetriever,
    concept: str,
    *,
    top_k: int,
    max_context_chars: int,
) -> SkillChatResult | None:
    evidence = retriever.retrieve(concept, limit=top_k, max_chars=max_context_chars)
    if not evidence:
        return None
    seen: set[str] = set()
    ids: list[str] = []
    names: list[str] = []
    for chunk in evidence:
        if chunk.skill_id in seen:
            continue
        seen.add(chunk.skill_id)
        ids.append(chunk.skill_id)
        names.append(chunk.skill_name)
    if not ids:
        return None
    return SkillChatResult(
        kind="results",
        text="\n".join(f"{index}. {name}" for index, name in enumerate(names, start=1)),
        skill_ids=tuple(ids),
        evidence=evidence,
    )


def _set_item_context(context: DatabaseChatContext, query: str, item_ids: tuple[int, ...]) -> None:
    context.active_domain = "item"
    context.active_item_ids = item_ids
    context.selected_item_id = item_ids[0] if len(item_ids) == 1 else None
    context.last_operation = "item"
    context.last_metric = None
    context.last_user_query = query


def _set_mixed_context(
    context: DatabaseChatContext,
    query: str,
    item_ids: tuple[int, ...],
    skill_ids: tuple[str, ...],
) -> None:
    context.active_domain = "mixed"
    context.active_item_ids = item_ids
    context.active_skill_ids = skill_ids
    context.selected_item_id = None
    context.selected_skill_id = None
    context.last_operation = "mixed_discovery"
    context.last_metric = None
    context.last_user_query = query


def run_database_chat_sync(
    item_database_path: Path,
    skill_database_path: Path,
    query: str,
    context: DatabaseChatContext,
    *,
    item_repository_factory=core.ItemRepository,
    skill_repository_factory=SkillRepository,
    skill_semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
    rag_client=None,
    skill_rag_model: str = "gemma4:e4b",
    ollama_host: str | None = None,
    skill_rag_top_k: int = 5,
    skill_rag_max_context_chars: int = 12000,
    skill_rag_max_output_tokens: int = 256,
    skill_rag_keep_alive: str = "10m",
) -> DatabaseChatOutcome:
    shared_concept = _shared_discovery_concept(query)
    item_repository = item_repository_factory(Path(item_database_path).expanduser().resolve())
    try:
        item_outcome = probe_item_deterministically(item_repository, query)
        if shared_concept is not None:
            concept_outcome = probe_item_deterministically(item_repository, shared_concept)
            if concept_outcome is None or not _item_ids(concept_outcome):
                concept_outcome = probe_item_deterministically(item_repository, f"stat {shared_concept}")
            if concept_outcome is not None and _item_ids(concept_outcome):
                item_outcome = concept_outcome
        item_ids = _item_ids(item_outcome)

        if shared_concept is None and item_outcome is not None and item_outcome.kind in {
            "search", "help", "database", "refuse"
        }:
            _set_item_context(context, query, item_ids)
            return DatabaseChatOutcome(kind="item", item_outcome=item_outcome, item_ids=item_ids)

        skill_repository = None
        try:
            skill_repository = skill_repository_factory(Path(skill_database_path).expanduser().resolve())
            retriever = SkillEvidenceRetriever(skill_repository, semantic_runtime=skill_semantic_runtime)
            if shared_concept is not None:
                skill_result = _skill_discovery_result(
                    retriever,
                    shared_concept,
                    top_k=skill_rag_top_k,
                    max_context_chars=skill_rag_max_context_chars,
                )
            else:
                effective_rag_client = rag_client or OllamaSkillRagClient(
                    model=skill_rag_model,
                    host=ollama_host,
                    max_output_tokens=skill_rag_max_output_tokens,
                    keep_alive=skill_rag_keep_alive,
                )
                skill_service = SkillChatService(
                    skill_repository,
                    retriever=retriever,
                    rag=GroundedSkillRag(effective_rag_client),
                    top_k=skill_rag_top_k,
                    max_context_chars=skill_rag_max_context_chars,
                )
                skill_result = skill_service.answer(query, context=context)
        except (FileNotFoundError, OSError, sqlite3.DatabaseError, SchemaError):
            skill_result = None
        finally:
            if skill_repository is not None:
                skill_repository.close()

        skill_ids = () if skill_result is None else skill_result.skill_ids
        if item_ids and _meaningful_skill(skill_result):
            _set_mixed_context(context, query, item_ids, skill_ids)
            return DatabaseChatOutcome(
                kind="mixed", item_outcome=item_outcome, skill_result=skill_result,
                item_ids=item_ids, skill_ids=skill_ids,
            )
        if _meaningful_skill(skill_result):
            return DatabaseChatOutcome(kind="skill", skill_result=skill_result, skill_ids=skill_ids)
        if item_outcome is not None:
            _set_item_context(context, query, item_ids)
            return DatabaseChatOutcome(kind="item", item_outcome=item_outcome, item_ids=item_ids)
        return DatabaseChatOutcome(kind="fallback")
    finally:
        item_repository.close()


def build_database_chat_help_embed(bot_example_prefix: str) -> discord.Embed:
    embed = discord.Embed(
        title="Toram Search",
        description="Search item and skill databases directly, or ask grounded natural-language questions.",
    )
    embed.add_field(name="Item Search", value="\n".join((
        f"`{bot_example_prefix} hp armor`",
        f"`{bot_example_prefix} hp > 5000 and cr bow`",
        f"`{bot_example_prefix} item Rapier`",
    )), inline=False)
    embed.add_field(name="Skill Search", value="\n".join((
        f"`{bot_example_prefix} skill hard hit`",
        f"`{bot_example_prefix} skill magic finale`",
        f"`{bot_example_prefix} skill tree shield`",
    )), inline=False)
    embed.add_field(name="Ask Naturally", value="\n".join((
        f"`{bot_example_prefix} how does Hard Hit work?`",
        f"`{bot_example_prefix} which skills inflict Ignite?`",
        f"`{bot_example_prefix} which skill has the highest MP cost?`",
        f"`{bot_example_prefix} compare Protection and Aegis`",
        f"`{bot_example_prefix} what gives Crit Rate?`",
    )), inline=False)
    embed.add_field(
        name="Conversation",
        value=("Follow-ups can use previous database results. "
               f"Use `{bot_example_prefix} reset` to clear conversation context."),
        inline=False,
    )
    return embed


def build_skill_chat_embed(result: SkillChatResult) -> discord.Embed:
    if result.kind == "refuse":
        title = "Unsupported request"
        description = (
            "I can compare objective database facts such as MP cost, ailments, requirements, "
            "range, tier, and documented mechanics, but I can't decide which skill is best for "
            "DPS, tanking, or a build."
        )
    elif result.kind == "clarify":
        title, description = "Which skill?", result.text or "Please specify the skill you mean."
    elif result.kind == "not_found":
        title = "Not enough database information"
        description = result.text or "The skill database does not contain enough information to answer reliably."
    elif result.kind == "unavailable":
        title, description = "Skill explanation unavailable", result.text or "Structured skill search is still available."
    elif result.kind == "results":
        title, description = "Skill results", result.text or "No skill details available."
    else:
        title, description = "Skill database", result.text or "No answer available."
    return discord.Embed(title=title, description=description[:4096])


def _item_preview_lines(outcome: ServiceOutcome | None) -> tuple[str, ...]:
    if outcome is None or outcome.payload is None:
        return ()
    payload = outcome.payload
    if isinstance(payload, ItemDetailPayload):
        return (f"1. **{payload.detail.summary.name}** — {payload.detail.summary.item_type}",)
    if isinstance(payload, (ItemResultsPayload, UpgradeResultsPayload, StatResultsPayload, ExpressionResultsPayload)):
        return tuple(
            f"{index}. **{row.item.name}** — {row.item.item_type}"
            for index, row in enumerate(payload.results, start=1)
        )
    return ()


def _skill_preview_lines(result: SkillChatResult | None) -> tuple[str, ...]:
    if result is None:
        return ()
    names_by_id = {chunk.skill_id: chunk.skill_name for chunk in result.evidence}
    lines = [
        f"{index}. **{names_by_id.get(skill_id, skill_id)}**"
        for index, skill_id in enumerate(result.skill_ids, start=1)
    ]
    if lines:
        return tuple(lines)
    return tuple(line for line in (result.text or "").splitlines() if line.strip())


class MixedResultsView(SessionBoundView):
    def __init__(
        self, *, sessions: DiscordSessionManager, key: SessionKey, generation: int,
        item_lines: tuple[str, ...], skill_lines: tuple[str, ...],
    ) -> None:
        super().__init__(sessions=sessions, key=key, generation=generation, owner_id=key[2])
        self.item_lines = item_lines
        self.skill_lines = skill_lines
        if len(item_lines) > 3:
            self.add_item(ActionButton(label="More Items", style=discord.ButtonStyle.secondary, handler=self._more_items))
        if len(skill_lines) > 3:
            self.add_item(ActionButton(label="More Skills", style=discord.ButtonStyle.secondary, handler=self._more_skills))

    async def _more_items(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=discord.Embed(title="More item matches", description="\n".join(self.item_lines)[:4096]),
            ephemeral=True,
        )

    async def _more_skills(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            embed=discord.Embed(title="More skill matches", description="\n".join(self.skill_lines)[:4096]),
            ephemeral=True,
        )


def build_mixed_chat_message(
    outcome: DatabaseChatOutcome,
    *, sessions: DiscordSessionManager, key: SessionKey, generation: int,
) -> tuple[discord.Embed, discord.ui.View]:
    item_lines = _item_preview_lines(outcome.item_outcome)
    skill_lines = _skill_preview_lines(outcome.skill_result)
    embed = discord.Embed(title="Item and skill matches", description="Showing up to 3 matches from each database.")
    if item_lines:
        embed.add_field(name="Items", value="\n".join(item_lines[:3]), inline=False)
    if skill_lines:
        embed.add_field(name="Skills", value="\n".join(skill_lines[:3]), inline=False)
    return embed, MixedResultsView(
        sessions=sessions, key=key, generation=generation,
        item_lines=item_lines, skill_lines=skill_lines,
    )


__all__ = [
    "DatabaseChatOutcome", "MixedResultsView", "build_database_chat_help_embed",
    "build_mixed_chat_message", "build_skill_chat_embed", "is_database_chat_candidate",
    "probe_item_deterministically", "run_database_chat_sync",
]
