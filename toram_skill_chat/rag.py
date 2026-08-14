from __future__ import annotations

import logging
import re

from .llm import SkillRagResponseError, SkillRagUnavailableError
from .models import SkillChatResult, SkillEvidence


logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You answer questions about the local Toram skill database.

Use only DATABASE CONTEXT supplied in this request.
Do not use outside knowledge or memory about Toram Online.

You may summarize, compare, and explain facts supported by the context.
Do not invent formulas, percentages, conditions, interactions, requirements,
effects, rankings, build recommendations, or game mechanics.

If the context is insufficient, say that the database does not contain
enough information to answer reliably.

Never recommend a best DPS/tank/build/strongest skill.
"""

_REFUSAL_TEXT = (
    "I can compare objective database facts such as MP cost, ailments, "
    "requirements, range, tier, and documented mechanics, but I can't decide "
    "which skill is best for DPS, tanking, or a build."
)

_WORD_RE = re.compile(r"\w+", re.UNICODE)
_MECHANIC_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "does",
        "how",
        "is",
        "it",
        "skill",
        "the",
        "what",
        "work",
    }
)


def _normal_words(text: str) -> tuple[str, ...]:
    return tuple(match.group(0).casefold() for match in _WORD_RE.finditer(text))


def _is_unsupported_recommendation(question: str) -> bool:
    normalized = " ".join(_normal_words(question))
    phrases = (
        "best dps",
        "best tank",
        "best mage",
        "best combo",
        "strongest skill",
        "highest dps",
        "most damage",
    )
    if any(phrase in normalized for phrase in phrases):
        return True
    return normalized.startswith("best ") and any(
        word in normalized for word in ("skill", "build", "dps", "tank", "mage")
    )


def _evidence_covers_required_skills(
    evidence: tuple[SkillEvidence, ...],
    required_skill_ids: tuple[str, ...],
) -> bool:
    available = {chunk.skill_id for chunk in evidence}
    return all(skill_id in available for skill_id in required_skill_ids)


def _mechanic_evidence_is_relevant(
    question: str,
    evidence: tuple[SkillEvidence, ...],
) -> bool:
    query_terms = {
        word
        for word in _normal_words(question)
        if word not in _MECHANIC_STOP_WORDS and len(word) > 1
    }
    if not query_terms:
        return False
    evidence_terms = set(_normal_words("\n".join(chunk.text for chunk in evidence)))
    return bool(query_terms & evidence_terms)


def _user_prompt(question: str, evidence: tuple[SkillEvidence, ...]) -> str:
    blocks: list[str] = [f"QUESTION:\n{question}\n\nDATABASE CONTEXT:"]
    for index, chunk in enumerate(evidence, start=1):
        source = chunk.source_kind
        if chunk.label:
            source = f"{source} / {chunk.label}"
        blocks.append(
            "\n".join(
                (
                    f"[{index}]",
                    f"Skill: {chunk.skill_name}",
                    f"Tree: {chunk.tree_name}",
                    f"Source: {source}",
                    chunk.text,
                )
            )
        )
    return "\n\n".join(blocks)


def _fallback_text(evidence: tuple[SkillEvidence, ...]) -> str:
    lines = ["Synthesized explanation is unavailable. Database evidence:"]
    for chunk in evidence:
        compact = " ".join(chunk.text.split())
        lines.append(f"- {chunk.skill_name} ({chunk.tree_name}): {compact}")
    return "\n".join(lines)


def _skill_ids(evidence: tuple[SkillEvidence, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for chunk in evidence:
        if chunk.skill_id not in seen:
            seen.add(chunk.skill_id)
            ordered.append(chunk.skill_id)
    return tuple(ordered)


class GroundedSkillRag:
    def __init__(self, client) -> None:
        self.client = client

    def answer(
        self,
        question: str,
        *,
        evidence: tuple[SkillEvidence, ...],
        required_skill_ids: tuple[str, ...] = (),
        general_mechanic: bool = False,
    ) -> SkillChatResult:
        document_ids = tuple(chunk.document_id for chunk in evidence)
        if _is_unsupported_recommendation(question):
            logger.debug("skill rag gemma_called=False fallback=unsupported_recommendation")
            return SkillChatResult(kind="refuse", text=_REFUSAL_TEXT)

        sufficient = bool(evidence) and _evidence_covers_required_skills(
            evidence,
            required_skill_ids,
        )
        if sufficient and general_mechanic:
            sufficient = _mechanic_evidence_is_relevant(question, evidence)
        if not sufficient:
            logger.debug(
                "skill rag gemma_called=False fallback=insufficient_evidence document_ids=%s",
                document_ids,
            )
            return SkillChatResult(
                kind="not_found",
                text=(
                    "The skill database does not contain enough information to answer reliably."
                ),
                evidence=evidence,
            )

        logger.debug(
            "skill rag gemma_called=True document_ids=%s",
            document_ids,
        )
        try:
            answer = self.client.complete(SYSTEM_PROMPT, _user_prompt(question, evidence))
        except SkillRagUnavailableError:
            logger.debug(
                "skill rag fallback=llm_unavailable document_ids=%s",
                document_ids,
            )
            return SkillChatResult(
                kind="answer",
                text=_fallback_text(evidence),
                skill_ids=_skill_ids(evidence),
                evidence=evidence,
            )
        except SkillRagResponseError:
            logger.debug(
                "skill rag fallback=llm_response_error document_ids=%s",
                document_ids,
            )
            return SkillChatResult(
                kind="answer",
                text=_fallback_text(evidence),
                skill_ids=_skill_ids(evidence),
                evidence=evidence,
            )
        return SkillChatResult(
            kind="answer",
            text=answer,
            skill_ids=_skill_ids(evidence),
            evidence=evidence,
        )


__all__ = ["GroundedSkillRag", "SYSTEM_PROMPT"]
