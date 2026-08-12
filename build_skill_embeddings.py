from __future__ import annotations

import argparse
from pathlib import Path
import sys

from toram_skill_embeddings.providers import build_provider, parse_provider_spec
from toram_skills.repository import SkillRepository
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable, build_embedding_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build persisted embeddings for skill search documents"
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("coryn_data/database/skills.sqlite"),
    )
    parser.add_argument(
        "--provider-model",
        required=True,
        help="Embedding provider/model as '<provider>:<model>', e.g. st:sentence-transformers/all-MiniLM-L6-v2",
    )
    parser.add_argument("--host", help="Optional Ollama host; ignored by other providers")
    parser.add_argument("--device", help="Optional Sentence Transformers device, e.g. cpu or cuda")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        spec = parse_provider_spec(args.provider_model)
        provider = build_provider(
            spec,
            host=args.host,
            timeout_seconds=args.timeout_seconds,
            device=args.device,
        )
        with SkillRepository(args.database) as repository:
            count = build_embedding_index(
                repository,
                provider,
                batch_size=args.batch_size,
            )
    except (ValueError, EmbeddingUnavailable, EmbeddingIndexError) as exc:
        print(f"Embedding build failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Embedded {count} skill search documents with "
        f"{provider.provider_name}:{provider.model_name}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
