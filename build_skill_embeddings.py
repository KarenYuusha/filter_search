from __future__ import annotations

import argparse
from pathlib import Path
import sys

from toram_skill_embeddings.ollama_provider import OllamaEmbeddingProvider
from toram_skills.repository import SkillRepository
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable, build_embedding_index


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build persisted embeddings for skill search documents")
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("coryn_data/database/skills.sqlite"),
    )
    parser.add_argument("--model", required=True)
    parser.add_argument("--host")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    provider = OllamaEmbeddingProvider(
        args.model,
        host=args.host,
        timeout_seconds=args.timeout_seconds,
    )
    try:
        with SkillRepository(args.database) as repository:
            count = build_embedding_index(
                repository,
                provider,
                batch_size=args.batch_size,
            )
    except (EmbeddingUnavailable, EmbeddingIndexError) as exc:
        print(f"Embedding build failed: {exc}", file=sys.stderr)
        return 1
    print(f"Embedded {count} skill search documents with {args.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
