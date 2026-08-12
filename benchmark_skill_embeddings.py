from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from toram_skill_embeddings.providers import build_provider, parse_provider_spec
from toram_skills.hybrid_search import fuse_ranked_hits, tune_fusion
from toram_skills.lexical_search import lexical_search
from toram_skills.repository import SkillRepository
from toram_skills.retrieval_benchmark import (
    ModelBenchmarkResult,
    RetrievalCase,
    evaluate_semantic_cases,
    render_retrieval_config,
    select_embedding_model,
)
from toram_skills.search_models import SkillFilters, SkillSearchHit
from toram_skills.semantic_search import (
    EmbeddingIndexError,
    EmbeddingUnavailable,
    SemanticSkillIndex,
    build_embedding_index,
)
from toram_skills.structured_search import structured_skill_ids


DEFAULT_MODELS = (
    "st:sentence-transformers/all-MiniLM-L6-v2",
    "st:Alibaba-NLP/gte-multilingual-base",
    "st:BAAI/bge-m3",
)
_FILTER_TUPLE_FIELDS = {
    "tree_ids",
    "tree_groups",
    "tiers",
    "skill_types",
    "damage_types",
    "ailments",
    "weapons",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Benchmark local skill embedding providers and models"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("coryn_data/database/skills.sqlite"),
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=Path("tests/fixtures/skill_retrieval_golden.json"),
    )
    parser.add_argument("--models", nargs="+", default=list(DEFAULT_MODELS))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/benchmarks/2026-08-12-skill-embedding-benchmark.json"),
    )
    parser.add_argument("--config-output", type=Path)
    parser.add_argument("--host", help="Optional Ollama host; ignored by other providers")
    parser.add_argument("--device", help="Optional Sentence Transformers device, e.g. cpu or cuda")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def _load_cases(path: Path) -> tuple[dict[str, object], ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("Golden retrieval fixture must contain a list of cases")
    cases: list[dict[str, object]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("Every golden retrieval case must be an object")
        case_id = str(value.get("id", "")).strip()
        query = str(value.get("query", "")).strip()
        kind = str(value.get("kind", "")).strip()
        expected = value.get("expected_skill_ids")
        if not case_id or case_id in seen:
            raise ValueError(f"Invalid or duplicate golden case ID: {case_id!r}")
        if not query:
            raise ValueError(f"Golden case {case_id!r} has an empty query")
        if kind not in {"exact", "lexical", "semantic", "combined"}:
            raise ValueError(f"Golden case {case_id!r} has unsupported kind {kind!r}")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"Golden case {case_id!r} has no expected skill IDs")
        seen.add(case_id)
        cases.append(dict(value))
    if len(cases) < 60:
        raise ValueError("Golden retrieval fixture must contain at least 60 cases")
    return tuple(cases)


def _semantic_cases(values: tuple[dict[str, object], ...]) -> tuple[RetrievalCase, ...]:
    cases = tuple(
        RetrievalCase(
            id=str(value["id"]),
            query=str(value["query"]),
            expected_skill_ids=tuple(str(item) for item in value["expected_skill_ids"]),
            top_k=int(value.get("top_k", 5)),
            kind="semantic",
        )
        for value in values
        if value["kind"] == "semantic"
    )
    if not cases:
        raise ValueError("Golden retrieval fixture contains no semantic cases")
    return cases


def _filters(value: object) -> SkillFilters:
    if value is None:
        return SkillFilters()
    if not isinstance(value, dict):
        raise ValueError("Golden case filters must be an object")
    kwargs: dict[str, object] = {}
    for key, raw in value.items():
        if key in _FILTER_TUPLE_FIELDS:
            if not isinstance(raw, list):
                raise ValueError(f"Filter field {key!r} must be a list")
            kwargs[key] = tuple(raw)
        elif key in {"required_level_max", "mp_cost_max"}:
            kwargs[key] = None if raw is None else int(raw)
        else:
            raise ValueError(f"Unsupported golden filter field: {key}")
    return SkillFilters(**kwargs)


def _result_payload(result: ModelBenchmarkResult) -> dict[str, object]:
    return {
        "provider": result.provider,
        "model": result.model,
        "dimensions": result.dimensions,
        "top1": result.metrics.top1,
        "top3": result.metrics.top3,
        "top5": result.metrics.top5,
        "median_ms": result.metrics.median_ms,
        "p95_ms": result.metrics.p95_ms,
    }


def _rank_metrics(
    cases: tuple[dict[str, object], ...],
    results: dict[str, tuple[SkillSearchHit, ...]],
) -> dict[str, float | int]:
    if not cases:
        raise ValueError("At least one ranked retrieval case is required")
    top1 = top3 = top5 = 0
    for case in cases:
        case_id = str(case["id"])
        expected = set(str(item) for item in case["expected_skill_ids"])
        ids = tuple(hit.skill_id for hit in results.get(case_id, ()))
        top1 += int(any(skill_id in expected for skill_id in ids[:1]))
        top3 += int(any(skill_id in expected for skill_id in ids[:3]))
        top5 += int(any(skill_id in expected for skill_id in ids[:5]))
    total = len(cases)
    return {
        "case_count": total,
        "top1": top1 / total,
        "top3": top3 / total,
        "top5": top5 / total,
    }


def _provider(
    provider_model: str,
    host: str | None,
    timeout_seconds: float,
    device: str | None = None,
):
    spec = parse_provider_spec(provider_model)
    return build_provider(
        spec,
        host=host,
        timeout_seconds=timeout_seconds,
        device=device,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        all_cases = _load_cases(args.golden)
        semantic_cases = _semantic_cases(all_cases)
        results: list[ModelBenchmarkResult] = []
        provider_spec_by_identity: dict[tuple[str, str], str] = {}
        config_by_identity: dict[tuple[str, str], str] = {}

        with SkillRepository(args.database) as repository:
            source_manifest = repository.get_metadata("source_manifest_hash")
            document_manifest = repository.get_metadata("search_document_manifest_hash")
            if not source_manifest or not document_manifest:
                raise EmbeddingIndexError("Skill database is missing retrieval provenance metadata")

            for provider_model in args.models:
                print(f"Benchmarking embedding candidate: {provider_model}", flush=True)
                provider = _provider(
                    provider_model,
                    args.host,
                    args.timeout_seconds,
                    args.device,
                )
                identity = (provider.provider_name, provider.model_name)
                if identity in provider_spec_by_identity:
                    raise ValueError(
                        "Duplicate embedding provider/model candidate after normalization: "
                        f"{provider.provider_name}:{provider.model_name}"
                    )
                provider_spec_by_identity[identity] = provider_model
                config_by_identity[identity] = provider.config_id

                build_embedding_index(repository, provider, batch_size=args.batch_size)
                dimensions_text = repository.get_metadata("embedding_dimensions")
                if dimensions_text is None:
                    raise EmbeddingIndexError("Embedding dimensions metadata is missing")
                dimensions = int(dimensions_text)

                index = SemanticSkillIndex.from_repository(repository, provider)
                index.search(
                    semantic_cases[0].query,
                    limit=max(5, semantic_cases[0].top_k),
                )
                metrics = evaluate_semantic_cases(index, semantic_cases)
                results.append(
                    ModelBenchmarkResult(
                        provider.provider_name,
                        provider.model_name,
                        dimensions,
                        metrics,
                    )
                )
                print(
                    f"{provider.provider_name}:{provider.model_name}: "
                    f"top1={metrics.top1:.3f} top3={metrics.top3:.3f} "
                    f"top5={metrics.top5:.3f} median_ms={metrics.median_ms:.1f}",
                    flush=True,
                )

            selected = select_embedding_model(tuple(results))
            selected_identity = (selected.provider, selected.model)
            selected_spec = provider_spec_by_identity[selected_identity]
            print(
                "Rebuilding selected embedding index: "
                f"{selected.provider}:{selected.model}",
                flush=True,
            )
            selected_provider = _provider(
                selected_spec,
                args.host,
                args.timeout_seconds,
                args.device,
            )
            build_embedding_index(
                repository,
                selected_provider,
                batch_size=args.batch_size,
            )
            selected_index = SemanticSkillIndex.from_repository(repository, selected_provider)

            fusion_cases = tuple(case for case in all_cases if case["kind"] != "exact")
            lexical_results: dict[str, tuple[SkillSearchHit, ...]] = {}
            semantic_results: dict[str, tuple[SkillSearchHit, ...]] = {}
            for case in fusion_cases:
                case_id = str(case["id"])
                filters = _filters(case.get("filters"))
                eligible_ids = None
                if filters != SkillFilters():
                    eligible_ids = structured_skill_ids(repository, filters)
                candidate_limit = max(20, int(case.get("top_k", 5)) * 4)
                lexical_results[case_id] = lexical_search(
                    repository,
                    str(case["query"]),
                    eligible_skill_ids=eligible_ids,
                    limit=candidate_limit,
                )
                semantic_results[case_id] = selected_index.search(
                    str(case["query"]),
                    eligible_skill_ids=eligible_ids,
                    limit=candidate_limit,
                )

            fusion = tune_fusion(fusion_cases, lexical_results, semantic_results)
            fused_results = {
                str(case["id"]): fuse_ranked_hits(
                    lexical_results[str(case["id"])],
                    semantic_results[str(case["id"])],
                    config=fusion,
                    limit=max(5, int(case.get("top_k", 5))),
                )
                for case in fusion_cases
            }
            model_payloads = []
            for result in results:
                identity = (result.provider, result.model)
                entry = _result_payload(result)
                entry["config_id"] = config_by_identity[identity]
                model_payloads.append(entry)

            payload = {
                "source_manifest_hash": source_manifest,
                "search_document_manifest_hash": document_manifest,
                "semantic_case_count": len(semantic_cases),
                "fusion_case_count": len(fusion_cases),
                "models": model_payloads,
                "selected_provider": selected.provider,
                "selected_model": selected.model,
                "selected_config_id": selected_provider.config_id,
                "fusion_config": {
                    "rrf_k": fusion.rrf_k,
                    "lexical_weight": fusion.lexical_weight,
                    "semantic_weight": fusion.semantic_weight,
                },
                "hybrid_metrics": _rank_metrics(fusion_cases, fused_results),
            }
    except (OSError, ValueError, json.JSONDecodeError, EmbeddingUnavailable, EmbeddingIndexError) as exc:
        print(f"Embedding benchmark failed: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    if args.config_output is not None:
        args.config_output.parent.mkdir(parents=True, exist_ok=True)
        args.config_output.write_text(
            render_retrieval_config(
                str(payload["selected_provider"]),
                str(payload["selected_model"]),
                str(payload["selected_config_id"]),
                fusion,
            ),
            encoding="utf-8",
        )
    print(
        "Selected embedding model: "
        f"{payload['selected_provider']}:{payload['selected_model']}"
    )
    print(
        "Selected fusion: "
        f"rrf_k={fusion.rrf_k}, "
        f"lexical={fusion.lexical_weight}, semantic={fusion.semantic_weight}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
