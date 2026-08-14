from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import sqlite3
from typing import Literal

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

from .sessions import DatabaseChatContext


DatabaseChatKind = Literal["item", "skill", "mixed", "fallback"]
_SHARED_DISCOVERY_RE = re.compile(
    r"^\s*what\s+(?:gives?|provides?|has)\s+(.+?)\s*[?!.]*\s*$",
    re.IGNORECASE,
)
_SHARED_TAIL_RE = re.compile(
    r"\s+and\s+(?:how|what)\s+does\s+(?:it|that)\b.*$",
    re.IGNORECASE,
)


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


def probe_item_deterministically(
    repository,
    query: str,
) -> ServiceOutcome | None:
    """Run the existing item interpretation stages without entering Qwen fallback."""
    service = SearchService(repository, llm_client=_RoutingOnlyItemLlm())
    route = route_deterministically(
        query,
        repository,
        service.all_items,
        service.help_service,
        service.database_service,
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
        return ServiceOutcome(
            "database",
            text=service.database_service.execute(route.database_request),
        )
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


def _shared_discovery_concept(query: str) -> str | None:
    cleaned = _SHARED_TAIL_RE.sub("", " ".join(str(query).split()))
    match = _SHARED_DISCOVERY_RE.match(cleaned)
    if match is None:
        return None
    concept = match.group(1).strip(" ?!.")
    return concept or None


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
        "structured",
        "results",
        "answer",
        "clarify",
        "refuse",
    }


def _skill_discovery_result(
    retriever: SkillEvidenceRetriever,
    concept: str,
    *,
    top_k: int,
    max_context_chars: int,
) -> SkillChatResult | None:
    evidence = retriever.retrieve(
        concept,
        limit=top_k,
        max_chars=max_context_chars,
    )
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
        text="\n".join(
            f"{index}. {name}"
            for index, name in enumerate(names, start=1)
        ),
        skill_ids=tuple(ids),
        evidence=evidence,
    )


def _set_item_context(
    context: DatabaseChatContext,
    query: str,
    item_ids: tuple[int, ...],
) -> None:
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
                concept_outcome = probe_item_deterministically(
                    item_repository,
                    f"stat {shared_concept}",
                )
            if concept_outcome is not None and _item_ids(concept_outcome):
                item_outcome = concept_outcome
        item_ids = _item_ids(item_outcome)

        # A clear deterministic item/stat request keeps the existing fast path and
        # does not even open the skill database.
        if shared_concept is None and item_outcome is not None and item_outcome.kind in {
            "search",
            "help",
            "database",
            "refuse",
        }:
            _set_item_context(context, query, item_ids)
            return DatabaseChatOutcome(
                kind="item",
                item_outcome=item_outcome,
                item_ids=item_ids,
            )

        skill_repository = None
        try:
            skill_repository = skill_repository_factory(
                Path(skill_database_path).expanduser().resolve()
            )
            retriever = SkillEvidenceRetriever(
                skill_repository,
                semantic_runtime=skill_semantic_runtime,
            )
            if shared_concept is not None:
                skill_result = _skill_discovery_result(
                    retriever,
                    shared_concept,
                    top_k=skill_rag_top_k,
                    max_context_chars=skill_rag_max_context_chars,
                )
            else:
                effective_rag_client = rag_client
                if effective_rag_client is None:
                    effective_rag_client = OllamaSkillRagClient(
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
                kind="mixed",
                item_outcome=item_outcome,
                skill_result=skill_result,
                item_ids=item_ids,
                skill_ids=skill_ids,
            )
        if _meaningful_skill(skill_result):
            return DatabaseChatOutcome(
                kind="skill",
                skill_result=skill_result,
                skill_ids=skill_ids,
            )
        if item_outcome is not None:
            _set_item_context(context, query, item_ids)
            return DatabaseChatOutcome(
                kind="item",
                item_outcome=item_outcome,
                item_ids=item_ids,
            )
        return DatabaseChatOutcome(kind="fallback")
    finally:
        item_repository.close()


__all__ = [
    "DatabaseChatOutcome",
    "probe_item_deterministically",
    "run_database_chat_sync",
]
