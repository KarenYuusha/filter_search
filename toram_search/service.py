from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal, Mapping

import search_items as core
from toram_search.fallback import SearchIntentRequest
from toram_search.session import FailedQueryContext


ServiceKind = Literal[
    "search",
    "confirm_search",
    "help",
    "database",
    "refuse",
    "unavailable",
    "failed",
]
ClarificationMode = Literal["choose", "confirm"]
ResolutionChoiceKey = tuple[int, int]
StatResolutionChoices = Mapping[ResolutionChoiceKey, str]


@dataclass(frozen=True)
class StatClarification:
    group_index: int
    clause_index: int
    typed_stat: str
    candidates: tuple[str, ...]
    display_labels: tuple[str, ...]
    mode: ClarificationMode


@dataclass(frozen=True)
class ItemDetailPayload:
    detail: core.ItemDetail


@dataclass(frozen=True)
class ItemResultsPayload:
    query: str
    results: tuple[core.RankedItem, ...]


@dataclass(frozen=True)
class UpgradeDetailPayload:
    graph: core.UpgradeGraph
    selected_item_id: int


@dataclass(frozen=True)
class UpgradeResultsPayload:
    query: str
    results: tuple[core.RankedItem, ...]


@dataclass(frozen=True)
class GuidedStatPayload:
    message: str


@dataclass(frozen=True)
class StatClarificationPayload:
    parsed: core.ParsedSearch
    clarification: StatClarification
    choices: tuple[tuple[ResolutionChoiceKey, str], ...] = ()


@dataclass(frozen=True)
class StatResultsPayload:
    parsed: core.ParsedSearch
    results: tuple[core.RankedStatItem, ...]


@dataclass(frozen=True)
class ExpressionResultsPayload:
    parsed: core.ParsedSearch
    results: tuple[core.RankedExpressionItem, ...]


SearchPayload = (
    ItemDetailPayload
    | ItemResultsPayload
    | UpgradeDetailPayload
    | UpgradeResultsPayload
    | GuidedStatPayload
    | StatClarificationPayload
    | StatResultsPayload
    | ExpressionResultsPayload
)


@dataclass(frozen=True)
class ServiceOutcome:
    kind: ServiceKind
    payload: SearchPayload | None = None
    text: str | None = None
    search_requests: tuple[SearchIntentRequest, ...] = ()


def resolve_expression_noninteractively(
    parsed: core.ParsedSearch,
    repository: core.ItemRepository,
    choices: StatResolutionChoices,
) -> core.ParsedSearch | StatClarification | None:
    """Resolve a parsed stat expression without terminal input.

    Exact aliases resolve immediately. Ambiguous/fuzzy terms are returned as a
    frontend-neutral clarification request. Supplying a validated choice lets a
    later call continue from the same parsed expression.
    """
    if parsed.error:
        return None
    if parsed.resolved_expression is not None:
        return parsed
    if parsed.parsed_expression is None:
        return None

    available_stats = repository.list_stat_names()
    resolved_groups: list[core.ResolvedAndGroup] = []

    for group_index, group in enumerate(parsed.parsed_expression.groups):
        resolved_clauses: list[core.ResolvedClause] = []
        for clause_index, clause in enumerate(group.clauses):
            typed = core.SEARCH_ONLY_STAT_ALIASES.get(
                core.normalize_stat_text(clause.typed_stat),
                clause.typed_stat,
            )
            resolution = core.resolve_stat_term(
                typed,
                available_stats,
                allow_fuzzy=True,
            )
            key = (group_index, clause_index)

            if resolution.status in {"exact", "alias"}:
                stat_name = resolution.candidates[0]
            elif resolution.status == "ambiguous":
                selected = choices.get(key)
                if selected is None:
                    return StatClarification(
                        group_index=group_index,
                        clause_index=clause_index,
                        typed_stat=clause.typed_stat,
                        candidates=resolution.candidates,
                        display_labels=resolution.display_labels,
                        mode="choose",
                    )
                if selected not in resolution.candidates:
                    return None
                stat_name = selected
            elif resolution.status == "fuzzy":
                selected = choices.get(key)
                if selected is None:
                    return StatClarification(
                        group_index=group_index,
                        clause_index=clause_index,
                        typed_stat=clause.typed_stat,
                        candidates=resolution.candidates[:1],
                        display_labels=resolution.display_labels[:1],
                        mode="confirm",
                    )
                if not resolution.candidates or selected != resolution.candidates[0]:
                    return None
                stat_name = selected
            else:
                return None

            resolved_clauses.append(
                core.ResolvedClause(
                    typed_stat=clause.typed_stat,
                    stat_name=stat_name,
                    operator=clause.operator,
                    value=clause.value,
                )
            )
        resolved_groups.append(core.ResolvedAndGroup(tuple(resolved_clauses)))

    return replace(
        parsed,
        resolved_expression=core.ResolvedStatExpression(tuple(resolved_groups)),
    )


class SearchService:
    """Frontend-neutral orchestration over the existing search engine."""

    def __init__(
        self,
        repository: core.ItemRepository,
        *,
        llm_client: object | None = None,
    ) -> None:
        self.repository = repository
        self.all_items = repository.list_items()
        self.help_service = core.HelpService()
        self.database_service = core.make_database_question_service(repository)
        self.llm_client = llm_client if llm_client is not None else core.OllamaQwenClient()
        self.fallback_service = core._build_fallback_service(
            repository,
            self.all_items,
            self.help_service,
            self.database_service,
            self.llm_client,
        )

    def handle_query(
        self,
        query: str,
        context: FailedQueryContext,
    ) -> ServiceOutcome:
        route = core.route_deterministically(
            query,
            self.repository,
            self.all_items,
            self.help_service,
            self.database_service,
        )
        if route.kind == "search":
            if route.parsed is None:
                return ServiceOutcome("failed")
            return ServiceOutcome("search", payload=self._materialize(route.parsed, {}))
        if route.kind == "help":
            return ServiceOutcome("help", text=route.help_text or "")
        if route.kind == "database":
            if route.database_request is None:
                return ServiceOutcome("failed")
            return ServiceOutcome(
                "database",
                text=self.database_service.execute(route.database_request),
            )
        if route.kind == "refuse":
            return ServiceOutcome("refuse")

        if route.record_failure:
            context.record_failure(query)
        fallback = self.fallback_service.interpret(query, context.snapshot())
        if fallback.kind == "search_requests" and fallback.search_requests:
            context.set_latest_suggestion(
                core._format_structured_search_request(fallback.search_requests[0])
            )
            return ServiceOutcome(
                "confirm_search",
                search_requests=fallback.search_requests,
            )
        if fallback.kind == "database_action" and fallback.database_request is not None:
            return ServiceOutcome(
                "database",
                text=self.database_service.execute(fallback.database_request),
            )
        if fallback.kind == "help" and fallback.help_topic is not None:
            text = self.help_service.answer_topic(fallback.help_topic)
            return ServiceOutcome("help", text=text) if text is not None else ServiceOutcome("failed")
        if fallback.kind == "refuse":
            return ServiceOutcome("refuse")
        if fallback.kind == "unavailable":
            return ServiceOutcome("unavailable")
        return ServiceOutcome("failed")

    def confirm_search_request(
        self,
        request: SearchIntentRequest,
        raw_query: str,
        context: FailedQueryContext,
    ) -> ServiceOutcome:
        parsed = core.parse_structured_search_request(
            request,
            self.repository,
            raw_query=raw_query,
        )
        if parsed is None:
            return ServiceOutcome("failed")
        payload = self._materialize(parsed, {})
        if not isinstance(payload, StatClarificationPayload):
            context.clear()
        return ServiceOutcome("search", payload=payload)

    def continue_clarification(
        self,
        parsed: core.ParsedSearch,
        choices: Mapping[ResolutionChoiceKey, str],
    ) -> ServiceOutcome:
        if parsed.intent == "stat_choices":
            selected = choices.get((-1, -1))
            if selected is None or selected not in parsed.stat_choices:
                return ServiceOutcome("failed")
            parsed = core.ParsedSearch(
                intent="stat_search",
                raw_query=parsed.raw_query,
                stat=core.StatResolution(selected, selected, 100.0, False),
                filter=parsed.filter,
            )
            return ServiceOutcome("search", payload=self._materialize(parsed, {}))

        if parsed.intent == "stat_search" and parsed.stat is not None:
            selected = choices.get((-1, -1))
            if selected != parsed.stat.stat_name:
                return ServiceOutcome("failed")
            confirmed = replace(parsed, requires_confirmation=False)
            return ServiceOutcome("search", payload=self._materialize(confirmed, {}))

        return ServiceOutcome("search", payload=self._materialize(parsed, choices))

    def item_detail(self, item_id: int) -> ItemDetailPayload:
        return ItemDetailPayload(self.repository.get_item(item_id))

    def continue_upgrade_selection(
        self,
        item_id: int,
        item_name: str,
    ) -> ServiceOutcome:
        parsed = core.ParsedSearch(
            intent="exact_upgrade",
            raw_query=f"upgrade {item_name}",
            item_id=item_id,
        )
        return ServiceOutcome("search", payload=self._materialize(parsed, {}))

    @staticmethod
    def _rank_upgrade_successors(items: list[core.ItemSummary]) -> tuple[core.RankedItem, ...]:
        unique: dict[int, core.ItemSummary] = {item.id: item for item in items}
        ordered = sorted(unique.values(), key=lambda item: (item.name.casefold(), item.id))
        return tuple(
            core.RankedItem(item=item, score=100.0, match_kind="upgrade")
            for item in ordered
        )

    def _materialize(
        self,
        parsed: core.ParsedSearch,
        choices: Mapping[ResolutionChoiceKey, str],
    ) -> SearchPayload:
        if parsed.intent == "exact_item":
            return ItemDetailPayload(self.repository.get_item(int(parsed.item_id)))

        if parsed.intent == "item_search":
            query = parsed.item_query or parsed.raw_query
            exact = self.repository.exact_name_matches(query)
            if len(exact) == 1:
                return ItemDetailPayload(self.repository.get_item(exact[0].id))
            results = (
                [core.RankedItem(item=item, score=100.0, match_kind="exact") for item in exact]
                if len(exact) > 1
                else core.rank_items(query, self.all_items)
            )
            return ItemResultsPayload(query, tuple(results))

        if parsed.intent == "exact_upgrade":
            selected_id = int(parsed.item_id)
            return UpgradeDetailPayload(
                graph=self.repository.get_upgrade_component(selected_id),
                selected_item_id=selected_id,
            )

        if parsed.intent == "upgrade_search":
            query = parsed.item_query or ""
            exact = self.repository.exact_upgrade_name_matches(query)
            if len(exact) == 1:
                selected_id = exact[0].id
                return UpgradeDetailPayload(
                    graph=self.repository.get_upgrade_component(selected_id),
                    selected_item_id=selected_id,
                )
            upgrade_items = [
                item for item in self.all_items
                if core.is_crysta_item_type(item.item_type)
            ]
            results = (
                [core.RankedItem(item=item, score=100.0, match_kind="exact") for item in exact]
                if len(exact) > 1
                else core.rank_items(query, upgrade_items)
            )
            return UpgradeResultsPayload(query, tuple(results))

        if parsed.intent == "guided_stat":
            return GuidedStatPayload("Include a stat after 'stat'.")

        if parsed.intent == "stat_choices":
            clarification = StatClarification(
                group_index=-1,
                clause_index=-1,
                typed_stat=parsed.raw_query,
                candidates=parsed.stat_choices,
                display_labels=parsed.stat_choices,
                mode="choose",
            )
            return StatClarificationPayload(parsed, clarification)

        if parsed.intent == "stat_search":
            if parsed.stat is None:
                return GuidedStatPayload(parsed.error or "No stat was selected.")
            if parsed.requires_confirmation:
                clarification = StatClarification(
                    group_index=-1,
                    clause_index=-1,
                    typed_stat=parsed.stat.matched_text,
                    candidates=(parsed.stat.stat_name,),
                    display_labels=(parsed.stat.stat_name,),
                    mode="confirm",
                )
                return StatClarificationPayload(parsed, clarification)
            results = self.repository.search_by_stat(
                parsed.stat.stat_name,
                parsed.filter.item_types if parsed.filter else None,
            )
            return StatResultsPayload(parsed, tuple(results))

        if parsed.intent == "stat_expression":
            resolved = resolve_expression_noninteractively(
                parsed,
                self.repository,
                choices,
            )
            if isinstance(resolved, StatClarification):
                return StatClarificationPayload(
                    parsed,
                    resolved,
                    tuple(sorted(choices.items())),
                )
            if resolved is None or resolved.resolved_expression is None:
                return GuidedStatPayload(parsed.error or "I couldn't resolve that stat expression.")
            results = self.repository.search_by_expression(
                resolved.resolved_expression,
                resolved.filter.item_types if resolved.filter else None,
                primary_sort_ascending=resolved.primary_sort_ascending,
            )
            return ExpressionResultsPayload(resolved, tuple(results))

        return GuidedStatPayload("I couldn't convert that into a supported search.")


def format_search_request(request: SearchIntentRequest) -> str:
    return core._format_structured_search_request(request)


def item_id_from_payload(payload: SearchPayload, result_index: int) -> int | None:
    if result_index < 0:
        return None
    if isinstance(payload, ItemResultsPayload):
        results = payload.results
    elif isinstance(payload, UpgradeResultsPayload):
        results = payload.results
    elif isinstance(payload, StatResultsPayload):
        results = payload.results
    elif isinstance(payload, ExpressionResultsPayload):
        results = payload.results
    else:
        return None
    if result_index >= len(results):
        return None
    return int(results[result_index].item.id)
