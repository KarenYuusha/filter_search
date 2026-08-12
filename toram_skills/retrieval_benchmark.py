from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from time import perf_counter
from typing import Iterable, Protocol, Sequence


@dataclass(frozen=True)
class RetrievalCase:
    id: str
    query: str
    expected_skill_ids: tuple[str, ...]
    top_k: int = 5
    kind: str = "semantic"


@dataclass(frozen=True)
class RetrievalMetrics:
    top1: float
    top3: float
    top5: float
    median_ms: float
    p95_ms: float


@dataclass(frozen=True)
class ModelBenchmarkResult:
    model: str
    metrics: RetrievalMetrics

    @property
    def top1(self) -> float:
        return self.metrics.top1

    @property
    def top3(self) -> float:
        return self.metrics.top3

    @property
    def top5(self) -> float:
        return self.metrics.top5

    @property
    def median_ms(self) -> float:
        return self.metrics.median_ms

    @property
    def p95_ms(self) -> float:
        return self.metrics.p95_ms


class SearchIndex(Protocol):
    def search(self, query: str, *, limit: int = 20): ...


def _percentile_95(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return ordered[index]


def evaluate_semantic_cases(
    index: SearchIndex,
    cases: Iterable[RetrievalCase],
) -> RetrievalMetrics:
    selected = tuple(cases)
    if not selected:
        raise ValueError("At least one retrieval case is required")

    top1_hits = 0
    top3_hits = 0
    top5_hits = 0
    latencies_ms: list[float] = []

    for case in selected:
        if not case.expected_skill_ids:
            raise ValueError(f"Retrieval case {case.id!r} has no expected skill IDs")
        start = perf_counter()
        hits = index.search(case.query, limit=max(5, case.top_k))
        latencies_ms.append((perf_counter() - start) * 1000.0)
        result_ids = tuple(hit.skill_id for hit in hits)
        expected = set(case.expected_skill_ids)
        top1_hits += int(any(skill_id in expected for skill_id in result_ids[:1]))
        top3_hits += int(any(skill_id in expected for skill_id in result_ids[:3]))
        top5_hits += int(any(skill_id in expected for skill_id in result_ids[:5]))

    total = len(selected)
    return RetrievalMetrics(
        top1=top1_hits / total,
        top3=top3_hits / total,
        top5=top5_hits / total,
        median_ms=median(latencies_ms),
        p95_ms=_percentile_95(latencies_ms),
    )


def select_embedding_model(
    results: Sequence[ModelBenchmarkResult],
) -> ModelBenchmarkResult:
    if not results:
        raise ValueError("At least one embedding benchmark result is required")
    return max(
        results,
        key=lambda result: (
            result.metrics.top5,
            result.metrics.top3,
            result.metrics.top1,
            -result.metrics.median_ms,
            result.model,
        ),
    )


__all__ = [
    "ModelBenchmarkResult",
    "RetrievalCase",
    "RetrievalMetrics",
    "evaluate_semantic_cases",
    "select_embedding_model",
]
