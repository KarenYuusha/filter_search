from __future__ import annotations

from pathlib import Path
import sqlite3

from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillPayload,
    SkillResultItem,
    SkillResultsPayload,
    SkillUnavailablePayload,
)
from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
from toram_skills.hybrid_search import HybridSkillSearcher
from toram_skills.models import SkillDraft
from toram_skills.repository import SkillRepository
from toram_skills.retrieval_config import DEFAULT_FUSION_CONFIG
from toram_skills.schema import SchemaError
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


def parse_skill_command(query: str) -> str | None:
    parts = str(query).strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "skill":
        return None
    if len(parts) == 1:
        return ""
    return " ".join(parts[1].split())


def _snippet(skill: SkillDraft) -> str:
    for text in (skill.description, skill.game_description):
        if text and text.strip():
            return " ".join(text.split())
    metadata = [
        value
        for value in (
            f"Tier {skill.tier}" if skill.tier is not None else None,
            skill.skill_type,
            f"MP {skill.mp_cost_text}" if skill.mp_cost_text else None,
            skill.damage_type,
        )
        if value
    ]
    return " · ".join(metadata) or "Canonical skill record"


def _result_item(repository: SkillRepository, skill: SkillDraft) -> SkillResultItem:
    return SkillResultItem(
        skill=skill,
        tree=repository.get_tree(skill.tree_id),
        snippet=_snippet(skill),
    )


class SkillSearchService:
    def __init__(
        self,
        repository: SkillRepository,
        *,
        semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
    ) -> None:
        self.repository = repository
        self.semantic_runtime = semantic_runtime

    def handle(self, query: str) -> SkillPayload:
        cleaned = " ".join(str(query).split())
        if not cleaned:
            return SkillHelpPayload(
                "Search Toram skills with `skill <words>`. Examples: "
                "`skill magic finale`, `skill attack while moving`, "
                "`skill inflict tumble`."
            )

        exact = self.repository.resolve_skill_name(cleaned)
        if len(exact) == 1:
            skill = exact[0]
            return SkillDetailPayload(
                skill,
                self.repository.get_tree(skill.tree_id),
            )
        if len(exact) > 1:
            return SkillResultsPayload(
                cleaned,
                tuple(_result_item(self.repository, skill) for skill in exact),
            )
        return self._search_free_text(cleaned)

    def _search_free_text(self, query: str) -> SkillResultsPayload:
        semantic_index = (
            None
            if self.semantic_runtime is None
            else self.semantic_runtime.get_index(self.repository)
        )
        searcher = HybridSkillSearcher(
            self.repository,
            semantic_index=semantic_index,
            fusion_config=DEFAULT_FUSION_CONFIG,
        )
        try:
            hits = searcher.search(query, limit=20)
        except (EmbeddingUnavailable, EmbeddingIndexError):
            hits = HybridSkillSearcher(
                self.repository,
                semantic_index=None,
                fusion_config=DEFAULT_FUSION_CONFIG,
            ).search(query, limit=20)
        return SkillResultsPayload(
            query,
            tuple(
                _result_item(
                    self.repository,
                    self.repository.get_skill(hit.skill_id),
                )
                for hit in hits
            ),
        )


def run_skill_search(
    database_path: Path,
    query: str,
    *,
    repository_factory=SkillRepository,
    semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
) -> SkillPayload:
    repository = None
    try:
        repository = repository_factory(Path(database_path).expanduser().resolve())
        return SkillSearchService(
            repository,
            semantic_runtime=semantic_runtime,
        ).handle(query)
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, SchemaError):
        return SkillUnavailablePayload(
            "Skill search is currently unavailable. "
            "Item and stat search are still available."
        )
    finally:
        if repository is not None:
            repository.close()


__all__ = ["SkillSearchService", "parse_skill_command", "run_skill_search"]
