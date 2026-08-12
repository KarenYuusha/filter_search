from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from .lexical_search import lexical_search
from .repository import SkillRepository
from .search_models import ChannelScore, SkillFilters, SkillSearchHit
from .semantic_search import EmbeddingUnavailable, SemanticSkillIndex
from .structured_search import structured_skill_ids


@dataclass(frozen=True)
class FusionConfig:
    rrf_k: int = 60
    lexical_weight: float = 1.0
    semantic_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        if self.lexical_weight < 0.0 or self.semantic_weight < 0.0:
            raise ValueError("fusion weights must be non-negative")


_DEFAULT_FUSION = FusionConfig()
_FUSION_GRID = tuple(
    FusionConfig(rrf_k=rrf_k, lexical_weight=lexical, semantic_weight=semantic)
    for rrf_k in (20, 60)
    for lexical, semantic in (
        (1.0, 1.0),
        (1.5, 1.0),
        (2.0, 1.0),
        (1.5, 0.5),
        (2.0, 0.5),
    )
)


def _channel_rank(hit: SkillSearchHit, channel: str, fallback_rank: int) -> int:
    ranks = [score.rank for score in hit.channels if score.channel == channel]
    return min(ranks) if ranks else fallback_rank


def _channel_raw_score(hit: SkillSearchHit, channel: str) -> float:
    values = [score.raw_score for score in hit.channels if score.channel == channel]
    return values[0] if values else hit.score


def fuse_ranked_hits(
    lexical_hits: Sequence[SkillSearchHit],
    semantic_hits: Sequence[SkillSearchHit],
    *,
    config: FusionConfig = _DEFAULT_FUSION,
    limit: int = 10,
) -> tuple[SkillSearchHit, ...]:
    if limit <= 0:
        return ()

    scores: dict[str, float] = {}
    channel_scores: dict[str, dict[str, ChannelScore]] = {}
    evidence: dict[str, list[str]] = {}

    for channel, hits, weight in (
        ("lexical", lexical_hits, config.lexical_weight),
        ("semantic", semantic_hits, config.semantic_weight),
    ):
        if weight <= 0.0:
            continue
        seen_in_channel: set[str] = set()
        for fallback_rank, hit in enumerate(hits, start=1):
            if hit.skill_id in seen_in_channel:
                continue
            seen_in_channel.add(hit.skill_id)
            rank = _channel_rank(hit, channel, fallback_rank)
            if rank <= 0:
                continue
            scores[hit.skill_id] = scores.get(hit.skill_id, 0.0) + weight / (
                config.rrf_k + rank
            )
            per_skill = channel_scores.setdefault(hit.skill_id, {})
            per_skill[channel] = ChannelScore(
                channel=channel,  # type: ignore[arg-type]
                rank=rank,
                raw_score=_channel_raw_score(hit, channel),
            )
            output_evidence = evidence.setdefault(hit.skill_id, [])
            for document_id in hit.evidence_document_ids:
                if document_id not in output_evidence:
                    output_evidence.append(document_id)

    ordered_ids = sorted(scores, key=lambda skill_id: (-scores[skill_id], skill_id))[:limit]
    return tuple(
        SkillSearchHit(
            skill_id=skill_id,
            score=scores[skill_id],
            channels=tuple(
                channel_scores[skill_id][name]
                for name in ("lexical", "semantic")
                if name in channel_scores[skill_id]
            ),
            evidence_document_ids=tuple(evidence.get(skill_id, ())),
        )
        for skill_id in ordered_ids
    )


def _has_filters(filters: SkillFilters) -> bool:
    return filters != SkillFilters()


class HybridSkillSearcher:
    def __init__(
        self,
        repository: SkillRepository,
        semantic_index: SemanticSkillIndex | None = None,
        *,
        fusion_config: FusionConfig = _DEFAULT_FUSION,
    ) -> None:
        self.repository = repository
        self.semantic_index = semantic_index
        self.fusion_config = fusion_config

    def search(
        self,
        query: str,
        *,
        filters: SkillFilters = SkillFilters(),
        limit: int = 10,
    ) -> tuple[SkillSearchHit, ...]:
        if limit <= 0:
            return ()

        eligible_skill_ids: tuple[str, ...] | None = None
        if _has_filters(filters):
            eligible_skill_ids = structured_skill_ids(self.repository, filters)
            eligible = set(eligible_skill_ids)
        else:
            eligible = None

        exact = self.repository.resolve_skill_name(query)
        if exact:
            if eligible is not None:
                exact = tuple(skill for skill in exact if skill.id in eligible)
            if exact:
                return tuple(
                    SkillSearchHit(skill_id=skill.id, score=1.0)
                    for skill in exact[:limit]
                )

        if eligible_skill_ids == ():
            return ()

        candidate_limit = max(20, limit * 4)
        lexical_hits = lexical_search(
            self.repository,
            query,
            eligible_skill_ids=eligible_skill_ids,
            limit=candidate_limit,
        )

        semantic_hits: tuple[SkillSearchHit, ...] = ()
        if self.semantic_index is not None:
            try:
                semantic_hits = self.semantic_index.search(
                    query,
                    eligible_skill_ids=eligible_skill_ids,
                    limit=candidate_limit,
                )
            except EmbeddingUnavailable:
                semantic_hits = ()

        return fuse_ranked_hits(
            lexical_hits,
            semantic_hits,
            config=self.fusion_config,
            limit=limit,
        )


def _case_value(case: object, key: str):
    if isinstance(case, Mapping):
        return case[key]
    return getattr(case, key)


def _fusion_metrics(
    cases: Sequence[object],
    lexical_results: Mapping[str, Sequence[SkillSearchHit]],
    semantic_results: Mapping[str, Sequence[SkillSearchHit]],
    config: FusionConfig,
) -> tuple[float, float, float]:
    if not cases:
        raise ValueError("At least one fusion case is required")
    top1 = top3 = top5 = 0
    for case in cases:
        case_id = str(_case_value(case, "id"))
        expected = set(_case_value(case, "expected_skill_ids"))
        if not expected:
            raise ValueError(f"Fusion case {case_id!r} has no expected skill IDs")
        hits = fuse_ranked_hits(
            lexical_results.get(case_id, ()),
            semantic_results.get(case_id, ()),
            config=config,
            limit=5,
        )
        ids = tuple(hit.skill_id for hit in hits)
        top1 += int(any(skill_id in expected for skill_id in ids[:1]))
        top3 += int(any(skill_id in expected for skill_id in ids[:3]))
        top5 += int(any(skill_id in expected for skill_id in ids[:5]))
    total = len(cases)
    return top1 / total, top3 / total, top5 / total


def tune_fusion(
    cases: Sequence[object],
    lexical_results: Mapping[str, Sequence[SkillSearchHit]],
    semantic_results: Mapping[str, Sequence[SkillSearchHit]],
) -> FusionConfig:
    if not cases:
        raise ValueError("At least one fusion case is required")
    scored = tuple(
        (config, _fusion_metrics(cases, lexical_results, semantic_results, config))
        for config in _FUSION_GRID
    )
    return max(
        scored,
        key=lambda item: (
            item[1][0],
            item[1][1],
            item[1][2],
            -item[0].semantic_weight,
            -item[0].rrf_k,
        ),
    )[0]


__all__ = [
    "FusionConfig",
    "HybridSkillSearcher",
    "fuse_ranked_hits",
    "tune_fusion",
]
