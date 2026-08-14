from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from toram_skills.parsing import normalize_skill_name


ConceptConfidence = Literal["exact", "alias", "none"]


@dataclass(frozen=True)
class ConceptResolution:
    canonical: str | None
    confidence: ConceptConfidence


# Keep this intentionally small. New aliases should be added only when validated
# against real user language and a canonical value that exists in the database.
CONCEPT_ALIASES: dict[str, str] = {
    "ignition": "ignite",
}


def resolve_ailment(
    text: str,
    known_ailments: tuple[str, ...],
) -> ConceptResolution:
    normalized_query = normalize_skill_name(text)
    canonical_by_normalized = {
        normalize_skill_name(value): value
        for value in known_ailments
    }

    # Explicit user-language aliases overlay raw database vocabulary. This matters
    # when the corpus contains both the alias word and the intended canonical term.
    aliased_normalized = CONCEPT_ALIASES.get(normalized_query)
    if aliased_normalized is not None:
        canonical = canonical_by_normalized.get(aliased_normalized)
        if canonical is not None:
            return ConceptResolution(canonical, "alias")

    exact = canonical_by_normalized.get(normalized_query)
    if exact is not None:
        return ConceptResolution(exact, "exact")
    return ConceptResolution(None, "none")


__all__ = ["CONCEPT_ALIASES", "ConceptResolution", "resolve_ailment"]
