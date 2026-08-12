from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import median
from time import perf_counter
from typing import Iterable, Protocol, Sequence

from .hybrid_search import FusionConfig


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
    provider: str
    model: str
    dimensions: int
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

    @property
    def provider_model_id(self) -> str:
        return f"{self.provider}:{self.model}"


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


def _validate_benchmark_result(result: ModelBenchmarkResult) -> None:
    if not result.provider.strip():
        raise ValueError("Embedding benchmark provider must not be empty")
    if not result.model.strip():
        raise ValueError("Embedding benchmark model must not be empty")
    if result.dimensions <= 0:
        raise ValueError("Embedding benchmark dimensions must be positive")

    metric_values = (
        result.top1,
        result.top3,
        result.top5,
        result.median_ms,
        result.p95_ms,
    )
    if any(not math.isfinite(value) for value in metric_values):
        raise ValueError("Embedding benchmark metrics must be finite")
    if any(value < 0.0 or value > 1.0 for value in (result.top1, result.top3, result.top5)):
        raise ValueError("Embedding benchmark hit rates must be between 0 and 1")
    if result.median_ms < 0.0 or result.p95_ms < 0.0:
        raise ValueError("Embedding benchmark latency must not be negative")


def select_embedding_model(
    results: Sequence[ModelBenchmarkResult],
    *,
    quality_window: float = 0.02,
) -> ModelBenchmarkResult:
    if not math.isfinite(quality_window) or quality_window < 0.0:
        raise ValueError("quality_window must be a finite non-negative value")
    if not results:
        raise ValueError("At least one embedding benchmark result is required")

    selected_results = tuple(results)
    for result in selected_results:
        _validate_benchmark_result(result)

    best_top5 = max(result.top5 for result in selected_results)
    threshold = best_top5 - quality_window - 1e-12
    eligible = tuple(result for result in selected_results if result.top5 >= threshold)
    return min(
        eligible,
        key=lambda result: (
            result.median_ms,
            result.p95_ms,
            -result.top3,
            -result.top1,
            result.dimensions,
            result.provider,
            result.model,
        ),
    )


def render_retrieval_config(
    selected_provider: str,
    selected_model: str,
    selected_config_id: str,
    fusion: FusionConfig,
) -> str:
    provider = selected_provider.strip()
    model = selected_model.strip()
    config_id = selected_config_id.strip()
    if not provider:
        raise ValueError("selected_provider must not be empty")
    if not model:
        raise ValueError("selected_model must not be empty")
    if not config_id:
        raise ValueError("selected_config_id must not be empty")
    return (
        "from toram_skills.hybrid_search import FusionConfig\n\n"
        f"DEFAULT_EMBEDDING_PROVIDER = {provider!r}\n"
        f"DEFAULT_EMBEDDING_MODEL = {model!r}\n"
        f"DEFAULT_EMBEDDING_CONFIG_ID = {config_id!r}\n"
        "DEFAULT_FUSION_CONFIG = FusionConfig(\n"
        f"    rrf_k={fusion.rrf_k},\n"
        f"    lexical_weight={fusion.lexical_weight!r},\n"
        f"    semantic_weight={fusion.semantic_weight!r},\n"
        ")\n"
    )


__all__ = [
    "ModelBenchmarkResult",
    "RetrievalCase",
    "RetrievalMetrics",
    "evaluate_semantic_cases",
    "render_retrieval_config",
    "select_embedding_model",
]
