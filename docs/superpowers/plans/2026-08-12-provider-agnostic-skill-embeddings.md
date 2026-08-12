# Provider-Agnostic Skill Embeddings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the skill hybrid retrieval milestone with provider-agnostic query/document embeddings and benchmark MiniLM, GTE multilingual base, and BGE-M3 using a latency-aware selection policy.

**Architecture:** Keep `toram_skills` provider-runtime-free and evolve its embedding protocol to separate document encoding from query encoding. Put Ollama and Sentence Transformers adapters in `toram_skill_embeddings`, persist provider/model/config provenance alongside vectors, and make the benchmark/build CLIs construct providers from provider-qualified model specifications.

**Tech Stack:** Python >=3.12; SQLite; existing `unittest`; existing `ollama` client; optional `sentence-transformers` runtime using the public `SentenceTransformer.encode_query()` and `encode_document()` information-retrieval APIs.

## Global Constraints

- Exact, structured, and FTS5 retrieval remain deterministic and LLM-free.
- Do not change item search behavior, item Qwen behavior, Discord routing/UI, canonical skill import semantics, or lexical ranking semantics.
- `toram_skills` must not import `ollama`, `sentence_transformers`, `torch`, `transformers`, or hosted provider SDKs.
- Persisted document vectors are built ahead of query time; runtime semantic search embeds exactly one incoming query.
- Provider-specific query/document prompting stays inside provider adapters.
- Semantic provider failure must continue to degrade to lexical retrieval.
- Benchmark the initial local candidates `sentence-transformers/all-MiniLM-L6-v2`, `Alibaba-NLP/gte-multilingual-base`, and `BAAI/bge-m3`.
- Select the lowest-latency valid model within 2 percentage points of the best semantic top-5 hit rate, then tie-break by p95 latency, top-3, top-1, dimensions, and stable provider/model identifier.
- Sentence Transformers is an optional embedding dependency, not a dependency of `toram_skills`.
- Full repository tests remain the final acceptance gate.

---

## File Structure

```text
toram_skills/
    semantic_search.py          # provider-neutral protocol, validation, persisted-index provenance
    retrieval_benchmark.py      # benchmark result model, quality window, config rendering

toram_skill_embeddings/
    ollama_provider.py          # Ollama query/document adapter
    sentence_transformer_provider.py  # Sentence Transformers query/document adapter
    providers.py                # provider-qualified spec parsing and construction

build_skill_embeddings.py       # provider-aware final index build CLI
benchmark_skill_embeddings.py   # provider-aware real benchmark/fusion CLI
pyproject.toml                   # optional embedding dependency group

tests/
    test_skill_semantic_search.py
    test_skill_embedding_benchmark.py
    test_skill_embedding_providers.py
    test_core_module_boundaries.py
```

---

### Task 1: Split Query and Document Embedding in the Core

**Files:**
- Modify: `toram_skills/semantic_search.py`
- Modify: `tests/test_skill_semantic_search.py`

**Interfaces:**
- Produces:
  - `EmbeddingProvider.provider_name: str`
  - `EmbeddingProvider.model_name: str`
  - `EmbeddingProvider.config_id: str`
  - `EmbeddingProvider.embed_documents(texts: Sequence[str]) -> tuple[tuple[float, ...], ...]`
  - `EmbeddingProvider.embed_query(text: str) -> tuple[float, ...]`
- `build_embedding_index()` calls only `embed_documents()`.
- `SemanticSkillIndex.search()` calls only `embed_query()`.

- [ ] **Step 1: Add RED fake-provider tests for query/document separation**

Update the semantic-search test fake provider to record separate document and query calls:

```python
class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"
    config_id = "default"

    def __init__(self, document_vectors=(), query_vector=()):
        self.document_vectors = tuple(document_vectors)
        self.query_vector = tuple(query_vector)
        self.document_calls = []
        self.query_calls = []

    def embed_documents(self, texts):
        self.document_calls.append(tuple(texts))
        return self.document_vectors

    def embed_query(self, text):
        self.query_calls.append(text)
        return self.query_vector
```

Add assertions that an index build never calls `embed_query()` and a search never calls `embed_documents()`.

- [ ] **Step 2: Run focused test and verify RED**

Run:

```bash
python -m unittest tests.test_skill_semantic_search -v
```

Expected: failures because the current protocol still requires `embed()`.

- [ ] **Step 3: Replace the core protocol and helper paths**

Use:

```python
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    config_id: str

    def embed_documents(
        self,
        texts: Sequence[str],
    ) -> tuple[tuple[float, ...], ...]: ...

    def embed_query(self, text: str) -> tuple[float, ...]: ...
```

Replace `_embed_batch()` with `_embed_documents()` and `_embed_query()` helpers. Preserve existing vector-count, numeric, finite, zero-norm, and dimension validation.

- [ ] **Step 4: Persist and validate provider/config provenance**

During successful `build_embedding_index()` write metadata:

```text
embedding_provider
embedding_model
embedding_config_id
embedding_dimensions
embedding_document_manifest_hash
```

Reject blank provider/model/config identifiers. `SemanticSkillIndex.from_repository()` must require exact provider/model/config equality before loading vectors.

Do not change the `skill_embedding_vectors` table schema; provider/config identity is index-wide metadata while each row continues to persist model/dimensions/text hash/vector.

- [ ] **Step 5: Run focused semantic tests**

```bash
python -m unittest tests.test_skill_semantic_search -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toram_skills/semantic_search.py tests/test_skill_semantic_search.py
git commit -m "refactor: split skill query and document embeddings"
```

---

### Task 2: Add Provider Adapters Without Polluting the Core

**Files:**
- Modify: `toram_skill_embeddings/ollama_provider.py`
- Create: `toram_skill_embeddings/sentence_transformer_provider.py`
- Create: `toram_skill_embeddings/providers.py`
- Create: `tests/test_skill_embedding_providers.py`
- Modify: `tests/test_skill_embedding_benchmark.py`
- Modify: `tests/test_core_module_boundaries.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces:
  - `OllamaEmbeddingProvider(provider_name="ollama", model_name=..., config_id="default")`
  - `SentenceTransformerEmbeddingProvider(model_name, *, device=None, model=None, model_factory=None)`
  - `EmbeddingProviderSpec(provider: str, model: str)`
  - `parse_provider_spec(value: str) -> EmbeddingProviderSpec`
  - `build_provider(spec, *, host=None, timeout_seconds=120.0, device=None) -> EmbeddingProvider`

- [ ] **Step 1: Write RED adapter tests**

Create `tests/test_skill_embedding_providers.py` with a fake Sentence Transformer exposing:

```python
class FakeSentenceTransformer:
    def __init__(self):
        self.document_calls = []
        self.query_calls = []

    def encode_document(self, texts, **kwargs):
        self.document_calls.append((texts, kwargs))
        return [[1.0, 0.0], [0.0, 1.0]][:len(texts)]

    def encode_query(self, text, **kwargs):
        self.query_calls.append((text, kwargs))
        return [0.5, 0.5]
```

Assert:

```python
provider.provider_name == "sentence-transformers"
provider.model_name == "sentence-transformers/all-MiniLM-L6-v2"
provider.config_id == "ir-default"
provider.embed_documents(("a", "b")) == ((1.0, 0.0), (0.0, 1.0))
provider.embed_query("q") == (0.5, 0.5)
```

Also assert malformed output becomes `EmbeddingIndexError`, model-load/inference exceptions become `EmbeddingUnavailable`, and no `sentence_transformers` import occurs at module import time when a fake model is injected.

- [ ] **Step 2: Add provider-spec RED tests**

Required forms:

```text
st:sentence-transformers/all-MiniLM-L6-v2
st:Alibaba-NLP/gte-multilingual-base
st:BAAI/bge-m3
ollama:qwen3-embedding:0.6b
```

Reject missing provider, missing model, and unknown provider. Preserve every colon after the first separator as part of the model name.

- [ ] **Step 3: Run provider tests and verify RED**

```bash
python -m unittest tests.test_skill_embedding_providers tests.test_skill_embedding_benchmark -v
```

Expected: failures for missing adapter/spec code and old Ollama `embed()` interface.

- [ ] **Step 4: Update the Ollama adapter**

Implement `embed_documents()` by sending the text list to the existing Ollama `embed()` API. Implement `embed_query()` by sending one-item input and requiring exactly one returned vector. Set:

```python
provider_name = "ollama"
config_id = "default"
```

Retain current timeout/host/error conversion behavior.

- [ ] **Step 5: Implement the Sentence Transformers adapter**

Lazy-import `SentenceTransformer` inside the constructor only when no injected model/factory is supplied. Construct with the requested `model_name` and optional `device`.

Use the current public IR APIs:

```python
model.encode_document(list(texts), convert_to_numpy=True, normalize_embeddings=False)
model.encode_query(text, convert_to_numpy=True, normalize_embeddings=False)
```

Convert all returned vectors to immutable tuples of floats. Core semantic code remains responsible for L2 normalization and dimension validation.

- [ ] **Step 6: Implement provider-qualified parsing/factory**

`parse_provider_spec()` accepts aliases `st` and `sentence-transformers`, canonicalizing both to `sentence-transformers`; `ollama` stays `ollama`.

`build_provider()` routes only to the two adapters. Only Ollama receives `host`/`timeout_seconds`; Sentence Transformers receives optional `device`.

- [ ] **Step 7: Add optional dependency group**

Extend `pyproject.toml` with:

```toml
[project.optional-dependencies]
embeddings = [
    "sentence-transformers>=5,<6",
]
```

Do not add it to the base `dependencies` array.

- [ ] **Step 8: Strengthen module-boundary tests**

The core package test must reject these imports in `toram_skills/*.py`:

```text
ollama
sentence_transformers
torch
transformers
```

- [ ] **Step 9: Run focused tests**

```bash
python -m unittest tests.test_skill_embedding_providers tests.test_skill_embedding_benchmark tests.test_core_module_boundaries -v
```

Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add toram_skill_embeddings tests/test_skill_embedding_providers.py tests/test_skill_embedding_benchmark.py tests/test_core_module_boundaries.py pyproject.toml
git commit -m "feat: add provider-agnostic skill embedding adapters"
```

---

### Task 3: Make Benchmark Selection Latency-Aware and Provider-Aware

**Files:**
- Modify: `toram_skills/retrieval_benchmark.py`
- Modify: `tests/test_skill_embedding_benchmark.py`

**Interfaces:**
- `ModelBenchmarkResult(provider, model, dimensions, metrics)`
- `provider_model_id -> f"{provider}:{model}"`
- `select_embedding_model(results, quality_window=0.02)`
- `render_retrieval_config(selected_provider, selected_model, selected_config_id, fusion)`

- [ ] **Step 1: Write RED selection-policy tests**

Add a lightweight-within-window case:

```python
results = (
    ModelBenchmarkResult("sentence-transformers", "mini", 384, RetrievalMetrics(.70, .88, .96, 7, 10)),
    ModelBenchmarkResult("sentence-transformers", "bge", 1024, RetrievalMetrics(.73, .90, .98, 70, 90)),
)
self.assertEqual(select_embedding_model(results).model, "mini")
```

Add a >2-point quality-gap case where the larger model must win:

```python
results = (
    ModelBenchmarkResult("sentence-transformers", "mini", 384, RetrievalMetrics(.70, .88, .94, 7, 10)),
    ModelBenchmarkResult("sentence-transformers", "gte", 768, RetrievalMetrics(.76, .92, .97, 20, 30)),
)
self.assertEqual(select_embedding_model(results).model, "gte")
```

Also test exact 0.02 boundary inclusion, p95 tie-break, quality tie-break after latency, dimensions tie-break, and stable provider/model final ordering.

- [ ] **Step 2: Run benchmark tests and verify RED**

```bash
python -m unittest tests.test_skill_embedding_benchmark -v
```

Expected: failures because the current result type and selection policy are model-only/top5-first.

- [ ] **Step 3: Extend benchmark result identity and dimensions**

Use:

```python
@dataclass(frozen=True)
class ModelBenchmarkResult:
    provider: str
    model: str
    dimensions: int
    metrics: RetrievalMetrics
```

Validate `dimensions > 0` in selection inputs.

- [ ] **Step 4: Implement the 2-point quality window**

Algorithm:

```python
best_top5 = max(result.top5 for result in results)
eligible = tuple(result for result in results if result.top5 >= best_top5 - quality_window - 1e-12)
selected = min(
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
```

Reject negative `quality_window`, blank provider/model names, non-positive dimensions, and non-finite metric values.

- [ ] **Step 5: Render provider/model/config into generated config**

Generated file must contain:

```python
DEFAULT_EMBEDDING_PROVIDER = "sentence-transformers"
DEFAULT_EMBEDDING_MODEL = "..."
DEFAULT_EMBEDDING_CONFIG_ID = "ir-default"
DEFAULT_FUSION_CONFIG = FusionConfig(...)
```

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_skill_embedding_benchmark -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add toram_skills/retrieval_benchmark.py tests/test_skill_embedding_benchmark.py
git commit -m "feat: prefer lightweight embeddings within quality window"
```

---

### Task 4: Make Build and Benchmark CLIs Provider-Aware

**Files:**
- Modify: `build_skill_embeddings.py`
- Modify: `benchmark_skill_embeddings.py`
- Modify: `tests/test_skill_embedding_benchmark.py`

**Interfaces:**
- Build CLI accepts `--provider-model <provider:model>` instead of requiring Ollama-only `--model`.
- Benchmark CLI accepts `--models <provider:model>...`.
- Default benchmark candidates:
  - `st:sentence-transformers/all-MiniLM-L6-v2`
  - `st:Alibaba-NLP/gte-multilingual-base`
  - `st:BAAI/bge-m3`
- Optional `--device` is passed only to Sentence Transformers.
- Existing `--host` remains Ollama-specific.

- [ ] **Step 1: Add RED CLI construction tests**

Patch `toram_skill_embeddings.providers.build_provider` and assert the build CLI parses:

```text
--provider-model st:sentence-transformers/all-MiniLM-L6-v2 --device cpu
```

into canonical provider/model identity.

For benchmark defaults, assert exactly the three approved local candidates are attempted in order.

- [ ] **Step 2: Run RED tests**

```bash
python -m unittest tests.test_skill_embedding_benchmark -v
```

Expected: failures because both scripts are Ollama-only.

- [ ] **Step 3: Convert build CLI**

Use provider specs and provider factory. Success output includes both provider and model. Preserve `--host`, `--timeout-seconds`, and `--batch-size` for Ollama compatibility; add `--device` for Sentence Transformers.

- [ ] **Step 4: Convert benchmark CLI**

For each provider-qualified candidate:

1. build vectors;
2. read resulting `embedding_dimensions` metadata;
3. warm one semantic query;
4. evaluate semantic golden cases;
5. record `ModelBenchmarkResult(provider, model, dimensions, metrics)`.

After selection, rebuild the chosen provider/model/config index, tune fusion, and emit JSON with:

```json
{
  "selected_provider": "sentence-transformers",
  "selected_model": "...",
  "selected_config_id": "ir-default"
}
```

Each model entry also records `provider`, `model`, and `dimensions`.

- [ ] **Step 5: Generate config with provider provenance**

When `--config-output` is supplied, call the new four-argument `render_retrieval_config()` using the selected adapter's exact identity.

- [ ] **Step 6: Run focused tests**

```bash
python -m unittest tests.test_skill_embedding_benchmark tests.test_skill_embedding_providers -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add build_skill_embeddings.py benchmark_skill_embeddings.py tests/test_skill_embedding_benchmark.py
git commit -m "feat: benchmark multiple skill embedding providers"
```

---

### Task 5: Verification and Real Benchmark Acceptance

**Files:**
- Generate: `docs/benchmarks/2026-08-12-skill-embedding-benchmark.json`
- Generate: `toram_skills/retrieval_config.py`
- Generate/Modify: `coryn_data/database/skills.sqlite`

**Interfaces:**
- Final benchmark JSON and generated Python config must agree exactly on selected provider/model/config and fusion values.

- [ ] **Step 1: Install optional benchmark runtime in the execution environment**

```bash
uv sync --extra embeddings
```

If CUDA is not available, benchmark on CPU and record the device/runtime environment in the benchmark JSON so latency numbers are not misread as universal hardware claims.

- [ ] **Step 2: Rebuild the canonical skill database before benchmarking**

```bash
python build_skills.py \
  --source raw_skills \
  --database coryn_data/database/skills.sqlite \
  --json-report /tmp/skills-import.json
```

Require 34 trees, 481 skills, zero import errors, a current source manifest, and non-empty deterministic search documents.

- [ ] **Step 3: Run the three-model benchmark**

```bash
python benchmark_skill_embeddings.py \
  --database coryn_data/database/skills.sqlite \
  --golden tests/fixtures/skill_retrieval_golden.json \
  --models \
    st:sentence-transformers/all-MiniLM-L6-v2 \
    st:Alibaba-NLP/gte-multilingual-base \
    st:BAAI/bge-m3 \
  --output docs/benchmarks/2026-08-12-skill-embedding-benchmark.json \
  --config-output toram_skills/retrieval_config.py \
  --device cpu
```

If the available execution host has a supported GPU, rerun all three candidates on that same device and commit only one internally consistent benchmark artifact.

- [ ] **Step 4: Rebuild final vectors from generated config**

Use a Python command to read provider/model constants from `toram_skills.retrieval_config`, construct the provider through `toram_skill_embeddings.providers`, and call `build_embedding_index()` on the canonical DB. Do not manually copy the selected model name.

- [ ] **Step 5: Verify database provenance**

Require:

```text
embedding_provider = DEFAULT_EMBEDDING_PROVIDER
embedding_model = DEFAULT_EMBEDDING_MODEL
embedding_config_id = DEFAULT_EMBEDDING_CONFIG_ID
embedding_document_manifest_hash = search_document_manifest_hash
embedding vector rows = search document rows
```

- [ ] **Step 6: Run fresh focused verification**

```bash
python -m compileall -q toram_skills toram_skill_embeddings build_skill_embeddings.py benchmark_skill_embeddings.py
python -m unittest tests.test_skill_semantic_search -v
python -m unittest tests.test_skill_embedding_providers -v
python -m unittest tests.test_skill_embedding_benchmark -v
python -m unittest tests.test_skill_hybrid_search -v
python -m unittest tests.test_core_module_boundaries -v
```

Expected: all PASS.

- [ ] **Step 7: Run full repository verification**

```bash
python -m unittest discover -s tests -v
```

Expected: all PASS.

- [ ] **Step 8: Commit acceptance artifacts**

```bash
git add docs/benchmarks/2026-08-12-skill-embedding-benchmark.json toram_skills/retrieval_config.py coryn_data/database/skills.sqlite
git commit -m "feat: finalize lightweight skill embedding retrieval"
```

- [ ] **Step 9: Fast-forward the parent feature branch only after verification**

Fast-forward `feat/skill-hybrid-retrieval` to the verified implementation head. Do not merge to `main` until the complete skill milestone is reviewed.
