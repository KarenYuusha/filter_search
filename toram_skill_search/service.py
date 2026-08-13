from __future__ import annotations

from pathlib import Path
import sqlite3

from rapidfuzz import fuzz

from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillPayload,
    SkillResultItem,
    SkillResultsPayload,
    SkillTreeChoicesPayload,
    SkillTreeConfirmationPayload,
    SkillTreeHelpPayload,
    SkillTreeNotFoundPayload,
    SkillTreeResultsPayload,
    SkillUnavailablePayload,
)
from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
from toram_skills.hybrid_search import HybridSkillSearcher
from toram_skills.models import SkillDraft, SkillTreeDraft
from toram_skills.parsing import normalize_skill_name
from toram_skills.repository import SkillRepository
from toram_skills.retrieval_config import DEFAULT_FUSION_CONFIG
from toram_skills.schema import SchemaError
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable

TREE_PREFIX = "tree"
TREE_FUZZY_MIN_SCORE = 80.0
TREE_FUZZY_AMBIGUITY_MARGIN = 10.0


def _tree_shorthand(name: str) -> str:
    normalized = normalize_skill_name(name)
    suffix = " skills"
    return normalized[:-len(suffix)].strip() if normalized.endswith(suffix) else normalized


def _tree_request(cleaned: str) -> tuple[bool, str]:
    parts = cleaned.split(maxsplit=1)
    if not parts or parts[0].casefold() != TREE_PREFIX:
        return False, ""
    return True, "" if len(parts) == 1 else parts[1].strip()


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

    def _tree_results(self, tree: SkillTreeDraft) -> SkillTreeResultsPayload:
        return SkillTreeResultsPayload(
            tree=tree,
            results=tuple(
                _result_item(self.repository, skill)
                for skill in self.repository.list_skills_in_tree(tree.id)
            ),
        )

    def _resolve_exact_tree(self, query: str) -> SkillTreeDraft | None:
        exact = self.repository.resolve_tree_name(query)
        if len(exact) == 1:
            return exact[0]

        normalized_query = normalize_skill_name(query)
        shorthand_names = [
            name
            for name in self.repository.list_tree_names()
            if _tree_shorthand(name) == normalized_query
        ]
        if len(shorthand_names) != 1:
            return None
        resolved = self.repository.resolve_tree_name(shorthand_names[0])
        return resolved[0] if len(resolved) == 1 else None

    def handle_tree_request(self, query: str) -> SkillPayload:
        cleaned = " ".join(query.split())
        names = tuple(self.repository.list_tree_names())
        if not cleaned:
            return SkillTreeHelpPayload(names)

        exact = self._resolve_exact_tree(cleaned)
        if exact is not None:
            return self._tree_results(exact)

        normalized_query = normalize_skill_name(cleaned)
        scored: list[tuple[float, str]] = []
        for name in names:
            score = max(
                fuzz.WRatio(normalized_query, normalize_skill_name(name)),
                fuzz.WRatio(normalized_query, _tree_shorthand(name)),
            )
            scored.append((float(score), name))
        scored.sort(key=lambda item: (-item[0], item[1].casefold(), item[1]))

        suggestions = tuple(name for _, name in scored[:3])
        if not scored or scored[0][0] < TREE_FUZZY_MIN_SCORE:
            return SkillTreeNotFoundPayload(cleaned, suggestions)

        best_score = scored[0][0]
        actionable_names = [
            name
            for score, name in scored
            if score >= TREE_FUZZY_MIN_SCORE
            and score >= best_score - TREE_FUZZY_AMBIGUITY_MARGIN
        ]
        candidates = tuple(
            self.repository.resolve_tree_name(name)[0]
            for name in actionable_names
        )
        if len(candidates) == 1:
            return SkillTreeConfirmationPayload(cleaned, candidates[0])
        return SkillTreeChoicesPayload(cleaned, candidates)

    def handle(self, query: str) -> SkillPayload:
        cleaned = " ".join(str(query).split())
        if not cleaned:
            return SkillHelpPayload(
                "Search Toram skills with `skill <words>`. Examples: "
                "`skill magic finale`, `skill attack while moving`, "
                "`skill inflict tumble`."
            )

        is_tree_request, tree_query = _tree_request(cleaned)
        if is_tree_request:
            return self.handle_tree_request(tree_query)

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


def run_skill_tree_by_id(
    database_path: Path,
    tree_id: str,
    *,
    repository_factory=SkillRepository,
) -> SkillTreeResultsPayload:
    repository = None
    try:
        repository = repository_factory(Path(database_path).expanduser().resolve())
        service = SkillSearchService(repository, semantic_runtime=None)
        return service._tree_results(repository.get_tree(tree_id))
    finally:
        if repository is not None:
            repository.close()


__all__ = [
    "SkillSearchService",
    "parse_skill_command",
    "run_skill_search",
    "run_skill_tree_by_id",
]
