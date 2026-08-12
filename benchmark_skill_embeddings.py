from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from toram_skill_embeddings.ollama_provider import OllamaEmbeddingProvider
from toram_skills.repository import SkillRepository
from toram_skills.retrieval_benchmark import (
    ModelBenchmarkResult,
    RetrievalCase,
    evaluate_semantic_cases,
    select_embedding_model,
)
from toram_skills.semantic_search import (
    EmbeddingIndexError,
    EmbeddingUnavailable,
    SemanticSkillIndex,
    build_embedding_index,
)


DEFAULT_MODELS = (
    "embeddinggemma:300m",
    "qwen3-embedding:0.6b",
    "nomic-embed-text:v1.5",
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark local Ollama skill embedding models")
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
    parser.add_argument("--host")
    parser.add_argument("--batch-size", type=int, default=32)
    return parser


def _load_semantic_cases(path: Path) -> tuple[RetrievalCase, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    values = payload.get("cases", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        raise ValueError("Golden retrieval fixture must contain a list of cases")
    cases: list[RetrievalCase] = []
    for value in values:
        if not isinstance(value, dict) or value.get("kind") != "semantic":
            continue
        expected = value.get("expected_skill_ids")
        if not isinstance(expected, list) or not expected:
            raise ValueError(f"Semantic case {value.get('id')!r} has no expected skill IDs")
        cases.append(
            RetrievalCase(
                id=str(value["id"]),
                query=str(value["query"]),
                expected_skill_ids=tuple(str(item) for item in expected),
                top_k=int(value.get("top_k", 5)),
                kind="semantic",
            )
        )
    if not cases:
        raise ValueError("Golden retrieval fixture contains no semantic cases")
    return tuple(cases)


def _result_payload(result: ModelBenchmarkResult) -> dict[str, object]:
    return {
        "model": result.model,
        "top1": result.metrics.top1,
        "top3": result.metrics.top3,
        "top5": result.metrics.top5,
        "median_ms": result.metrics.median_ms,
        "p95_ms": result.metrics.p95_ms,
    }


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        cases = _load_semantic_cases(args.golden)
        results: list[ModelBenchmarkResult] = []
        with SkillRepository(args.database) as repository:
            source_manifest = repository.get_metadata("source_manifest_hash")
            document_manifest = repository.get_metadata("search_document_manifest_hash")
            if not source_manifest or not document_manifest:
                raise EmbeddingIndexError("Skill database is missing retrieval provenance metadata")

            for model in args.models:
                provider = OllamaEmbeddingProvider(model, host=args.host)
                build_embedding_index(repository, provider, batch_size=args.batch_size)
                index = SemanticSkillIndex.from_repository(repository, provider)
                index.search(cases[0].query, limit=max(5, cases[0].top_k))
                metrics = evaluate_semantic_cases(index, cases)
                results.append(ModelBenchmarkResult(model, metrics))

            selected = select_embedding_model(tuple(results))
            payload = {
                "source_manifest_hash": source_manifest,
                "search_document_manifest_hash": document_manifest,
                "semantic_case_count": len(cases),
                "models": [_result_payload(result) for result in results],
                "selected_model": selected.model,
            }
    except (OSError, ValueError, json.JSONDecodeError, EmbeddingUnavailable, EmbeddingIndexError) as exc:
        print(f"Embedding benchmark failed: {exc}", file=sys.stderr)
        return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Selected embedding model: {payload['selected_model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
