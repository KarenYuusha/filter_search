from __future__ import annotations

import re

from .repository import SkillRepository
from .search_models import ChannelScore, SkillSearchHit


_WORD_RE = re.compile(r"\w+", re.UNICODE)


def _query_tokens(query: str) -> tuple[str, ...]:
    seen: set[str] = set()
    tokens: list[str] = []
    for match in _WORD_RE.finditer(query.casefold()):
        token = match.group(0).strip("_")
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _fts_expression(tokens: tuple[str, ...], operator: str) -> str:
    return f" {operator} ".join(f'"{token}"' for token in tokens)


def _search_documents(
    repository: SkillRepository,
    expression: str,
    eligible_skill_ids: tuple[str, ...] | None,
) -> tuple[tuple[str, str, float], ...]:
    params: list[object] = [expression]
    sql = """
        SELECT document_id, skill_id, bm25(skill_fts) AS score
        FROM skill_fts
        WHERE skill_fts MATCH ?
    """
    if eligible_skill_ids is not None:
        if not eligible_skill_ids:
            return ()
        placeholders = ", ".join("?" for _ in eligible_skill_ids)
        sql += f" AND skill_id IN ({placeholders})"
        params.extend(eligible_skill_ids)
    sql += " ORDER BY score ASC, document_id ASC"
    rows = repository.connection.execute(sql, tuple(params))
    return tuple(
        (str(row["document_id"]), str(row["skill_id"]), float(row["score"]))
        for row in rows
    )


def lexical_search(
    repository: SkillRepository,
    query: str,
    *,
    eligible_skill_ids: tuple[str, ...] | None = None,
    limit: int = 20,
) -> tuple[SkillSearchHit, ...]:
    if limit <= 0:
        return ()
    tokens = _query_tokens(query)
    if not tokens:
        return ()

    rows = _search_documents(
        repository,
        _fts_expression(tokens, "AND"),
        eligible_skill_ids,
    )
    if not rows and len(tokens) > 1:
        rows = _search_documents(
            repository,
            _fts_expression(tokens, "OR"),
            eligible_skill_ids,
        )

    best_by_skill: dict[str, tuple[str, float]] = {}
    for document_id, skill_id, score in rows:
        current = best_by_skill.get(skill_id)
        if current is None or (score, document_id) < (current[1], current[0]):
            best_by_skill[skill_id] = (document_id, score)

    ordered = sorted(
        (
            (skill_id, document_id, score)
            for skill_id, (document_id, score) in best_by_skill.items()
        ),
        key=lambda value: (value[2], value[0], value[1]),
    )[:limit]

    hits: list[SkillSearchHit] = []
    for rank, (skill_id, document_id, bm25_score) in enumerate(ordered, start=1):
        raw_score = -bm25_score
        hits.append(
            SkillSearchHit(
                skill_id=skill_id,
                score=raw_score,
                channels=(ChannelScore("lexical", rank, raw_score),),
                evidence_document_ids=(document_id,),
            )
        )
    return tuple(hits)


__all__ = ["lexical_search"]
