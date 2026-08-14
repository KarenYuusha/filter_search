from __future__ import annotations

from dataclasses import fields

from toram_skills.models import SkillDraft
from toram_skills.repository import SkillRepository

from .analytics import COMPARABLE_FIELDS, SkillAnalytics
from .models import SkillChatFilter, SkillChatPlan, SkillChatResult
from .retrieval import SkillEvidenceRetriever
from .router import SkillChatRouter


_NOT_FOUND_TEXT = "I couldn't find enough matching skill database information for that question."


def _filter_dict(filters: SkillChatFilter) -> dict[str, object]:
    output: dict[str, object] = {}
    for data_field in fields(SkillChatFilter):
        value = getattr(filters, data_field.name)
        if value not in ((), None):
            output[data_field.name] = value
    return output


def _metric_label(field: str) -> str:
    return {
        "mp_cost_value": "MP",
        "required_level": "Required Level",
        "tier": "Tier",
    }.get(field, field)


def _metric_value(skill: SkillDraft, field: str) -> str:
    if field == "mp_cost_value" and skill.mp_cost_text:
        return f"MP {skill.mp_cost_text}"
    value = getattr(skill, field)
    return f"{_metric_label(field)} {value}" if value is not None else f"{_metric_label(field)} not recorded"


class SkillChatService:
    def __init__(
        self,
        repository: SkillRepository,
        *,
        router: SkillChatRouter | None = None,
        analytics: SkillAnalytics | None = None,
        retriever: SkillEvidenceRetriever | None = None,
        rag=None,
        top_k: int = 5,
        max_context_chars: int = 12000,
    ) -> None:
        self.repository = repository
        self.router = router or SkillChatRouter(repository)
        self.analytics = analytics or SkillAnalytics(repository)
        self.retriever = retriever or SkillEvidenceRetriever(repository)
        self.rag = rag
        self.top_k = top_k
        self.max_context_chars = max_context_chars

    def _rank_selected_ids(self, plan: SkillChatPlan) -> tuple[SkillDraft, ...]:
        field = plan.field
        direction = plan.direction
        if field not in COMPARABLE_FIELDS or direction not in ("asc", "desc"):
            return ()
        candidates = [
            self.repository.get_skill(skill_id)
            for skill_id in plan.skill_ids
        ]
        candidates = [skill for skill in candidates if getattr(skill, field) is not None]
        if direction == "asc":
            candidates.sort(
                key=lambda skill: (
                    int(getattr(skill, field)),
                    skill.normalized_name,
                    skill.id,
                )
            )
        else:
            candidates.sort(
                key=lambda skill: (
                    -int(getattr(skill, field)),
                    skill.normalized_name,
                    skill.id,
                )
            )
        return tuple(candidates[: plan.limit])

    def _lookup(self, plan: SkillChatPlan) -> SkillChatResult:
        if len(plan.skill_ids) != 1:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        skill = self.repository.get_skill(plan.skill_ids[0])
        tree = self.repository.get_tree(skill.tree_id)
        if plan.field == "tree":
            text = f"{skill.name} is in {tree.name}."
        elif plan.field == "mp_cost":
            text = f"{skill.name}: {('MP ' + skill.mp_cost_text) if skill.mp_cost_text else 'MP cost not recorded'}."
        elif plan.field == "tier":
            text = f"{skill.name}: {('Tier ' + str(skill.tier)) if skill.tier is not None else 'tier not recorded'}."
        else:
            details = [f"Tree: {tree.name}"]
            if skill.tier is not None:
                details.append(f"Tier: {skill.tier}")
            if skill.mp_cost_text:
                details.append(f"MP: {skill.mp_cost_text}")
            text = f"{skill.name} — " + " · ".join(details)
        return SkillChatResult(kind="structured", text=text, skill_ids=(skill.id,))

    def _filter(self, plan: SkillChatPlan) -> SkillChatResult:
        skills = self.analytics.filter_skills(plan.filters)
        if not skills:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        visible = skills[: plan.limit]
        lines = [f"{index}. {skill.name}" for index, skill in enumerate(visible, start=1)]
        if len(skills) > len(visible):
            lines.append(f"…and {len(skills) - len(visible)} more matching skills.")
        return SkillChatResult(
            kind="results",
            text="\n".join(lines),
            skill_ids=tuple(skill.id for skill in skills),
        )

    def _count(self, plan: SkillChatPlan) -> SkillChatResult:
        count = self.analytics.count(plan.filters)
        return SkillChatResult(
            kind="structured",
            text=f"{count} skills match those database filters.",
        )

    def _rank(self, plan: SkillChatPlan) -> SkillChatResult:
        if plan.field is None or plan.direction is None:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        if plan.skill_ids:
            skills = self._rank_selected_ids(plan)
        else:
            skills = self.analytics.rank(
                plan.field,
                plan.direction,
                filters=plan.filters,
                limit=plan.limit,
            )
        if not skills:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        lines = [
            f"{index}. {skill.name} — {_metric_value(skill, plan.field)}"
            for index, skill in enumerate(skills, start=1)
        ]
        return SkillChatResult(
            kind="results",
            text="\n".join(lines),
            skill_ids=tuple(skill.id for skill in skills),
        )

    def _compare_field(self, plan: SkillChatPlan) -> SkillChatResult:
        if plan.field is None:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        values = self.analytics.compare_field(plan.skill_ids, plan.field)
        if len(values) < 2:
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)
        lines = [
            f"{entry.skill.name} — {_metric_value(entry.skill, plan.field)}"
            for entry in values
        ]
        return SkillChatResult(
            kind="structured",
            text="\n".join(lines),
            skill_ids=tuple(entry.skill.id for entry in values),
        )

    def _clarify_reference(self, plan: SkillChatPlan) -> SkillChatResult:
        names = [self.repository.get_skill(skill_id).name for skill_id in plan.skill_ids]
        return SkillChatResult(
            kind="clarify",
            text="Which skill do you mean: " + ", ".join(names) + "?",
            skill_ids=plan.skill_ids,
        )

    def _rag_answer(self, query: str, plan: SkillChatPlan) -> SkillChatResult:
        if self.rag is None:
            return SkillChatResult(
                kind="unavailable",
                text="A synthesized skill explanation is currently unavailable.",
            )
        evidence = self.retriever.retrieve(
            query,
            skill_ids=plan.skill_ids,
            limit=self.top_k,
            max_chars=self.max_context_chars,
        )
        return self.rag.answer(
            query,
            evidence=evidence,
            required_skill_ids=plan.skill_ids,
            general_mechanic=plan.intent == "general_mechanic",
        )

    def _update_context(
        self,
        context,
        *,
        query: str,
        plan: SkillChatPlan,
        result: SkillChatResult,
    ) -> None:
        if result.kind not in ("structured", "results", "answer"):
            return
        context.active_domain = "skill"
        context.last_operation = plan.intent
        context.last_metric = plan.field
        context.last_user_query = query

        if result.skill_ids:
            context.active_skill_ids = result.skill_ids
        if plan.intent == "lookup" and len(result.skill_ids) == 1:
            context.selected_skill_id = result.skill_ids[0]
        elif plan.intent == "rank" and len(result.skill_ids) == 1:
            context.selected_skill_id = result.skill_ids[0]
        elif plan.intent in ("compare", "compare_field"):
            context.selected_skill_id = None
        elif plan.intent == "general_mechanic":
            context.selected_skill_id = None

        filter_values = _filter_dict(plan.filters)
        if filter_values:
            context.active_skill_filters = filter_values
            tree_ids = filter_values.get("tree_ids", ())
            context.active_tree_id = tree_ids[0] if isinstance(tree_ids, tuple) and tree_ids else None

    def answer(self, query: str, *, context) -> SkillChatResult:
        plan = self.router.route(query, context=context)

        if plan.intent == "refuse":
            return SkillChatResult(kind="refuse", text=plan.refusal_reason)
        if plan.intent == "unknown":
            if plan.mechanic_query == "ambiguous_reference" and plan.skill_ids:
                return self._clarify_reference(plan)
            return SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)

        if plan.intent == "lookup":
            result = self._lookup(plan)
        elif plan.intent == "filter":
            result = self._filter(plan)
        elif plan.intent == "count":
            result = self._count(plan)
        elif plan.intent == "rank":
            result = self._rank(plan)
        elif plan.intent == "compare_field":
            result = self._compare_field(plan)
        elif plan.intent in ("explain", "compare", "general_mechanic"):
            result = self._rag_answer(query, plan)
        else:
            result = SkillChatResult(kind="not_found", text=_NOT_FOUND_TEXT)

        self._update_context(
            context,
            query=query,
            plan=plan,
            result=result,
        )
        return result


__all__ = ["SkillChatService"]
