# Provider-Agnostic Skill Embedding Benchmark Design

## Goal

Finish the skill hybrid retrieval milestone without tying semantic retrieval quality to Ollama. Keep exact, structured, and FTS5 retrieval fully deterministic and LLM-free, while allowing the semantic benchmark/build step to use any embedding provider that implements the existing vector contract.

The selection target is not maximum benchmark accuracy at any cost. The preferred production embedder should preserve strong retrieval quality while staying fast and lightweight enough for an interactive Discord bot.

## Scope

This change covers only skill embedding providers, benchmark candidates, benchmark selection policy, and final semantic-index generation.

It does not change:

- item search behavior;
- item Qwen behavior;
- Discord routing or UI;
- canonical skill parsing/import semantics;
- FTS5 lexical ranking semantics;
- hybrid RRF math outside any selected tuned constants;
- natural-language skill routing or grounded skill Q&A.

## Alternatives Considered

### A. Keep Ollama-only candidates

This requires the least code change but incorrectly makes the local runtime wrapper part of the model-selection decision. It also prevents benchmarking lightweight Sentence Transformers models directly.

Rejected.

### B. Add a second hard-coded Sentence Transformers benchmark path

This would allow MiniLM/GTE testing quickly, but would duplicate provider-specific logic in benchmark/build scripts and make future providers awkward.

Rejected.

### C. Provider-agnostic embedding interface with provider adapters

Keep `toram_skills` dependent only on an `EmbeddingProvider` protocol. Put runtime-specific adapters outside the core package. Benchmark code receives provider factories/configurations and treats all resulting vectors identically.

Selected.

## Architecture

The existing `EmbeddingProvider` contract remains the semantic-search boundary:

```python
class EmbeddingProvider(Protocol):
    model_name: str
    def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...
```

Provider adapters live outside `toram_skills`:

```text
toram_skill_embeddings/
    ollama_provider.py
    sentence_transformer_provider.py
```

`toram_skills.semantic_search` continues to validate dimensions, finiteness, vector counts, normalization, manifest compatibility, and persisted vector provenance. It must not import Ollama, Sentence Transformers, Torch, Transformers, or hosted SDKs.

The benchmark/build scripts are responsible for constructing the selected adapter.

## Initial Benchmark Set

Benchmark three local tiers first:

1. `sentence-transformers/all-MiniLM-L6-v2`
   - lightweight speed baseline;
   - 384-dimensional output;
   - expected to have the lowest memory and query-latency cost.

2. `Alibaba-NLP/gte-multilingual-base`
   - preferred quality/speed candidate;
   - 768-dimensional output;
   - larger than MiniLM but still intended for practical encoder inference.

3. `BAAI/bge-m3`
   - larger local quality ceiling;
   - benchmark dense embeddings only for this milestone;
   - sparse/multi-vector modes remain out of scope because the project already has FTS5 plus dense semantic retrieval.

The existing Ollama candidates may remain supported, but they are no longer mandatory for milestone acceptance.

A hosted API model is not required for acceptance. It can be added later as an optional quality reference without changing the core interface.

## Query and Document Encoding

All persisted skill document vectors are generated ahead of query time.

At runtime a semantic lookup embeds only the incoming query, then compares that vector against the in-memory persisted document vectors. No canonical skill document is re-embedded per request.

Provider-specific query/document conventions are handled by the adapter. For example, if a model requires query prefixes or task instructions, the adapter applies them consistently while exposing the same `embed()` interface to the semantic index.

For the first implementation, the Sentence Transformers adapter uses the model's standard encode behavior unless a candidate has an explicitly required retrieval prompt. Any such prompt must be deterministic and covered by tests.

## Benchmark Metrics

For each candidate record:

- top-1 hit rate;
- top-3 hit rate;
- top-5 hit rate;
- warmed median query latency;
- warmed p95 query latency;
- embedding dimensions;
- persisted vector/index byte size where practical;
- provider/model identifier;
- corpus/search-document manifest hashes.

Benchmark rankings must use the same committed golden retrieval cases and the same canonical database build.

## Selection Policy

Quality remains the primary gate, but the winner must also be appropriate for an interactive bot.

Selection proceeds as follows:

1. Reject any model that fails deterministic validation, golden-set integrity, or semantic-index provenance checks.
2. Determine the best observed semantic top-5 hit rate.
3. Keep candidates within 2 percentage points of that best top-5 rate.
4. Among those candidates, maximize top-3, then top-1.
5. If quality is still tied, choose the lower warmed median latency, then lower p95 latency, then smaller vector dimensions.

This prevents a heavyweight model from winning for a negligible top-5 improvement while still requiring it to demonstrate materially better retrieval quality if it is going to justify its cost.

If MiniLM is within the quality window and ties higher-quality metrics closely, its latency/size advantages should allow it to win. If GTE provides a meaningful quality improvement while remaining responsive, it should win over MiniLM. BGE-M3 should win only if the larger model produces a clear enough retrieval gain to survive the quality/latency policy.

## Hybrid Fusion

The selected semantic model is used with the existing deterministic lexical/semantic reciprocal-rank fusion.

Fusion tuning remains independent from raw BM25 and cosine magnitudes. The existing fixed RRF grid remains valid unless tests demonstrate that it must be changed.

Exact skill-name matches still bypass lexical and semantic retrieval entirely. Structured filters still define the eligible set for both retrieval channels. Semantic-provider failure still degrades to deterministic lexical retrieval.

## Failure Handling

The new adapter must convert model-load and inference failures into the existing semantic embedding-unavailable error contract.

A failed provider call must not partially replace persisted embeddings. The current valid semantic index remains intact until a complete new vector build has validated successfully.

Dimension changes across candidate models are expected and legal when rebuilding the index, but all vectors within one built index must have one consistent dimension.

## Dependencies

Add Sentence Transformers as an optional/local embedding dependency for the benchmark/build path rather than a dependency of `toram_skills` itself.

The core-module boundary test must continue to reject direct imports of provider runtimes from `toram_skills`.

If installing the full dependency globally would unnecessarily burden users who do not use semantic search, expose it through a project dependency group/extra appropriate to the existing `pyproject.toml` structure.

## Testing

Add focused tests for:

- Sentence Transformers adapter construction using a fake model/factory;
- single and batched embedding conversion to immutable tuples;
- exact vector-count validation;
- non-finite/invalid provider output rejection through the semantic layer;
- provider/model identity persistence;
- selection-policy behavior, including a lightweight candidate beating a marginally higher top-5 heavyweight candidate inside the 2-point quality window;
- core package import boundaries;
- benchmark scripts accepting provider-qualified candidates without embedding provider logic in `toram_skills`.

The existing exact, structured, lexical, semantic, hybrid, importer, and full repository suites remain acceptance gates.

## Final Milestone Output

Once the three candidates have been benchmarked on the real golden set:

- commit the benchmark JSON;
- generate `toram_skills/retrieval_config.py` from benchmark output;
- rebuild `skills.sqlite` embeddings with the selected provider/model;
- verify vector-row count equals current search-document count;
- verify embedding manifest equals the current search-document manifest;
- run the full repository test suite;
- only then consider the skill hybrid retrieval milestone complete and ready to merge.
