# Skill Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic exact, structured, lexical, and semantic skill retrieval over the canonical skill database, then combine lexical and semantic channels with benchmarked deterministic rank fusion.

**Architecture:** Keep `toram_skills` as the authoritative, LLM-free skill data/search domain. Rebuild `skills.sqlite` as schema version 2 with normalized search-support tables, an FTS5 index, semantic search documents, and persisted document vectors. Keep the Ollama embedding adapter outside `toram_skills`; query routing, `gemma4:e4b`, Discord, and formula evaluation remain out of this milestone.

**Tech Stack:** Python >=3.12; standard-library `dataclasses`, `pathlib`, `sqlite3`, `json`, `hashlib`, `math`, `struct`, `time`, `statistics`; SQLite FTS5; existing `ollama` Python package only in the embedding adapter; existing `unittest` style.

## Global Constraints

- `coryn_data/database/skills.sqlite` remains the canonical generated skill database.
- `raw_skills/` remains the editable source of truth for canonical skill content.
- Exact skill identity, structured fields, and canonical records remain authoritative over FTS/vector similarity.
- Exact-name lookup, structured retrieval, and FTS5 lexical retrieval must not require Ollama or embeddings.
- Semantic retrieval uses a dedicated embedding model, never `gemma4:e4b`.
- `gemma4:e4b`, natural-language intent parsing, grounded Q&A, Discord integration, and formula evaluation are Milestone 3+ work and must not be added here.
- Do not change item-search semantics, item Qwen behavior, `items.sqlite`, or Discord behavior.
- Do not introduce a hosted vector database.
- No semantic result may become a canonical fact; every result resolves back to a canonical `skill_id`.
- Search-document chunking follows skill/section boundaries, never arbitrary fixed token windows.
- Continue the existing `toram_skills` module-boundary rule: it must not import Discord, item-search entrypoints, `toram_search`, or `ollama`.
- The embedding benchmark candidates are fixed for this milestone to `embeddinggemma:300m`, `qwen3-embedding:0.6b`, and `nomic-embed-text:v1.5`.
- Model selection is deterministic: maximize semantic top-5 hit rate; tie-break by top-3, then top-1, then lower warmed median query latency.
- Hybrid tuning is deterministic: maximize hybrid top-1; tie-break by top-3, top-5, lower semantic weight, then lower `rrf_k`.

## File Structure

```text
toram_skills/
    schema.py
    repository.py
    search_models.py
    search_documents.py
    structured_search.py
    lexical_search.py
    semantic_search.py
    hybrid_search.py
    retrieval_benchmark.py
    retrieval_config.py

toram_skill_embeddings/
    __init__.py
    ollama_provider.py

build_skills.py
build_skill_embeddings.py
benchmark_skill_embeddings.py

tests/
    test_skill_exact_lookup.py
    test_skill_structured_search.py
    test_skill_lexical_search.py
    test_skill_semantic_search.py
    test_skill_hybrid_search.py
    test_skill_embedding_benchmark.py
    fixtures/skill_retrieval_golden.json

docs/benchmarks/
    2026-08-12-skill-embedding-benchmark.json
```

---

### Task 1: Exact Skill/Tree Retrieval and Typed Search Models

**Files:**
- Create: `toram_skills/search_models.py`
- Modify: `toram_skills/repository.py`
- Modify: `toram_skills/__init__.py`
- Create: `tests/test_skill_exact_lookup.py`

**Interfaces:**
- `SkillFilters(tree_ids=(), tree_groups=(), tiers=(), required_level_max=None, skill_types=(), mp_cost_max=None, damage_types=(), ailments=(), weapons=())`.
- `ChannelScore(channel, rank, raw_score)`.
- `SkillSearchHit(skill_id, score, channels=(), evidence_document_ids=())`.
- `SkillRepository.get_tree(tree_id) -> SkillTreeDraft`.
- `SkillRepository.resolve_tree_name(name) -> tuple[SkillTreeDraft, ...]`.
- `SkillRepository.list_skills_in_tree(tree_id) -> tuple[SkillDraft, ...]`.
- `SkillRepository.resolve_skill_name(name, *, tree_id=None) -> tuple[SkillDraft, ...]`.

- [ ] **Step 1: Write RED exact-resolution tests**

```python
class SkillExactLookupTests(unittest.TestCase):
    def test_exact_name_resolution_is_case_insensitive_and_cross_tree(self):
        matches = self.repo.resolve_skill_name("magic: finale")
        self.assertEqual([skill.id for skill in matches], [
            "weapon_class_skills/magic_skills/magic-finale",
        ])

    def test_tree_qualified_resolution_never_leaks_other_trees(self):
        matches = self.repo.resolve_skill_name(
            "magic: finale",
            tree_id="weapon_class_skills/magic_skills",
        )
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].tree_id, "weapon_class_skills/magic_skills")

    def test_unknown_exact_name_returns_empty_tuple(self):
        self.assertEqual(self.repo.resolve_skill_name("definitely not a skill"), ())
```

Add a fixture-database alias test proving canonical-name matches sort before alias-only matches.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_exact_lookup -v
```

- [ ] **Step 3: Add immutable search dataclasses**

```python
@dataclass(frozen=True)
class SkillFilters:
    tree_ids: tuple[str, ...] = ()
    tree_groups: tuple[str, ...] = ()
    tiers: tuple[int, ...] = ()
    required_level_max: int | None = None
    skill_types: tuple[str, ...] = ()
    mp_cost_max: int | None = None
    damage_types: tuple[str, ...] = ()
    ailments: tuple[str, ...] = ()
    weapons: tuple[str, ...] = ()


SearchChannel = Literal["lexical", "semantic"]


@dataclass(frozen=True)
class ChannelScore:
    channel: SearchChannel
    rank: int
    raw_score: float


@dataclass(frozen=True)
class SkillSearchHit:
    skill_id: str
    score: float
    channels: tuple[ChannelScore, ...] = ()
    evidence_document_ids: tuple[str, ...] = ()
```

- [ ] **Step 4: Implement exact normalized-equality resolvers**

Use `normalize_skill_name()` for input. Query canonical names and aliases, deduplicate by skill ID, and sort by `(canonical match first, tree_id, source_order, id)`. Do not add fuzzy auto-resolution.

- [ ] **Step 5: Verify focused and full suites**

```bash
python -m unittest tests.test_skill_exact_lookup -v
python -m unittest discover -s tests -v
```

- [ ] **Step 6: Commit**

```bash
git add toram_skills/search_models.py toram_skills/repository.py toram_skills/__init__.py tests/test_skill_exact_lookup.py
git commit -m "feat: add exact skill retrieval"
```

---

### Task 2: Deterministic Structured Skill Filters

**Files:**
- Create: `toram_skills/structured_search.py`
- Modify: `toram_skills/schema.py`
- Modify: `toram_skills/repository.py`
- Modify: `toram_skills/importer.py`
- Create: `tests/test_skill_structured_search.py`

**Interfaces:**
- `structured_skill_ids(repository, filters: SkillFilters) -> tuple[str, ...]`.

- [ ] **Step 1: Write RED filter tests**

```python
filters = SkillFilters(tree_ids=("weapon_class_skills/magic_skills",), tiers=(4,))
ids = structured_skill_ids(repo, filters)
self.assertIn("weapon_class_skills/magic_skills/magic-finale", ids)

filters = SkillFilters(mp_cost_max=100)
ids = structured_skill_ids(repo, filters)
self.assertTrue(all(repo.get_skill(skill_id).mp_cost_value <= 100 for skill_id in ids))

filters = SkillFilters(ailments=("Tumble",))
self.assertGreater(len(structured_skill_ids(repo, filters)), 0)
```

Also test `skill_types`, `damage_types`, and `weapons`. Across fields use AND semantics; multiple values in one field use OR semantics.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_structured_search -v
```

- [ ] **Step 3: Extend schema and bump generated schema version to 2**

```sql
CREATE TABLE skill_tree_weapon_restrictions (
    tree_id TEXT NOT NULL REFERENCES skill_trees(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    weapon TEXT NOT NULL,
    normalized_weapon TEXT NOT NULL,
    PRIMARY KEY(tree_id, position)
);
```

Add `normalized_name` to `skill_ailments`, `skill_weapon_requirements`, and `skill_weapon_restrictions`. This is a generated database: rebuild v2 rather than introducing an in-place migration.

- [ ] **Step 4: Populate normalized child rows during import**

Use the same casefold/whitespace normalization principle as skill names while preserving display values unchanged.

- [ ] **Step 5: Implement parameterized SQL filtering**

Use `EXISTS` for ailments/weapons so multi-valued rows cannot duplicate results. `weapons` matches explicit skill requirements/restrictions or parent-tree restrictions. Never interpolate filter values into SQL text.

- [ ] **Step 6: Verify schema-v2 rebuild**

```bash
python build_skills.py --source raw_skills --database /tmp/skills-v2.sqlite
python -m unittest tests.test_skill_structured_search tests.test_skill_repository tests.test_skill_importer -v
```

- [ ] **Step 7: Commit**

```bash
git add toram_skills/schema.py toram_skills/repository.py toram_skills/importer.py toram_skills/structured_search.py tests/test_skill_structured_search.py
git commit -m "feat: add structured skill filters"
```

---

### Task 3: Deterministic Search Documents and SQLite FTS5

**Files:**
- Create: `toram_skills/search_documents.py`
- Create: `toram_skills/lexical_search.py`
- Modify: `toram_skills/schema.py`
- Modify: `toram_skills/importer.py`
- Create: `tests/test_skill_lexical_search.py`

**Interfaces:**
- `SkillSearchDocument(id, skill_id, position, kind, label, text, text_hash)`.
- `build_search_documents(tree, skill) -> tuple[SkillSearchDocument, ...]`.
- `search_document_manifest_hash(documents) -> str`.
- `lexical_search(repository, query, *, eligible_skill_ids=None, limit=20) -> tuple[SkillSearchHit, ...]`.

- [ ] **Step 1: Write RED document/FTS tests**

```python
def test_finale_documents_are_skill_and_section_bounded(self):
    docs = build_search_documents(tree, finale)
    self.assertEqual(docs[0].kind, "summary")
    self.assertTrue(all(doc.skill_id == finale.id for doc in docs))
    self.assertTrue(all(doc.text.strip() for doc in docs))


def test_fts_finds_exact_toram_terms(self):
    hits = lexical_search(repo, "Magic Finale", limit=5)
    self.assertEqual(hits[0].skill_id, "weapon_class_skills/magic_skills/magic-finale")


def test_fts_query_syntax_is_not_user_controlled(self):
    self.assertIsInstance(lexical_search(repo, '\" OR * NEAR(', limit=5), tuple)
```

Add source-backed cases for `Tumble`, `Venom`, `AMPR`, and one formula/mechanics phrase.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_lexical_search -v
```

- [ ] **Step 3: Build stable skill/section documents**

Every skill gets one `summary` document containing name, tree, aliases, structured scalar facts, description, game description, and section labels. Every non-empty canonical section gets one section document. If a skill has no sections and little normalized prose, add one `source` document containing its `raw_text`. Do not split by token count.

Stable IDs:

```text
<skill_id>#summary
<skill_id>#section:<zero-based-position>
<skill_id>#source
```

- [ ] **Step 4: Add search-document and FTS tables**

```sql
CREATE TABLE skill_search_documents (
    id TEXT PRIMARY KEY,
    skill_id TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    kind TEXT NOT NULL,
    label TEXT,
    text TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    UNIQUE(skill_id, position)
);

CREATE VIRTUAL TABLE skill_fts USING fts5(
    document_id UNINDEXED,
    skill_id UNINDEXED,
    name,
    tree_name,
    text,
    tokenize='unicode61 remove_diacritics 2'
);
```

Populate both during canonical import and store metadata `search_document_manifest_hash`.

- [ ] **Step 5: Implement safe lexical querying**

Tokenize with a plain Unicode word regex; never pass raw user text as FTS syntax. Search normalized tokens with AND; if no result and there are multiple tokens, retry with OR. Sort by `bm25(skill_fts)`, collapse each skill to its best document, and expose that document ID as evidence.

- [ ] **Step 6: Verify rebuild and tests**

```bash
python build_skills.py --source raw_skills --database /tmp/skills-v2.sqlite
python -m unittest tests.test_skill_lexical_search tests.test_skill_importer -v
```

- [ ] **Step 7: Commit**

```bash
git add toram_skills/search_documents.py toram_skills/lexical_search.py toram_skills/schema.py toram_skills/importer.py tests/test_skill_lexical_search.py
git commit -m "feat: add skill full text search"
```

---

### Task 4: Semantic Vector Storage and Pure-Python Search

**Files:**
- Create: `toram_skills/semantic_search.py`
- Modify: `toram_skills/schema.py`
- Modify: `toram_skills/repository.py`
- Create: `tests/test_skill_semantic_search.py`

**Interfaces:**
- `EmbeddingProvider` protocol: `model_name: str` and `embed(texts: Sequence[str]) -> tuple[tuple[float, ...], ...]`.
- `build_embedding_index(repository, provider, *, batch_size=32) -> int`.
- `SemanticSkillIndex.from_repository(repository, provider)`.
- `SemanticSkillIndex.search(query, *, eligible_skill_ids=None, limit=20)`.

- [ ] **Step 1: Write RED tests with a deterministic fake provider**

Prove ranking plus failure behavior for dimension mismatch, zero vectors, NaN/Inf, stale document manifest, model mismatch, and provider unavailability.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_semantic_search -v
```

- [ ] **Step 3: Add persisted vector table**

```sql
CREATE TABLE skill_embedding_vectors (
    document_id TEXT PRIMARY KEY REFERENCES skill_search_documents(id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    text_hash TEXT NOT NULL,
    vector BLOB NOT NULL
);
```

Successful vector builds set `embedding_model`, `embedding_dimensions`, and `embedding_document_manifest_hash` metadata.

- [ ] **Step 4: Implement deterministic vector codec**

Store normalized vectors as little-endian float32 via `struct.pack`. Reject empty, non-finite, mixed-dimension, and zero-norm vectors before modifying persisted index state.

- [ ] **Step 5: Replace the vector index atomically**

Embed current documents in deterministic ID order, validate all output, then replace vectors + embedding metadata in one SQLite transaction. A failed provider call must leave the old valid index intact.

- [ ] **Step 6: Implement in-memory cosine retrieval**

Load vectors only when model and search-document manifest metadata match. Normalize the query vector, compute dot products, sort by `(-similarity, document_id)`, and collapse each skill to its best document.

- [ ] **Step 7: Verify focused/full tests**

```bash
python -m unittest tests.test_skill_semantic_search -v
python -m unittest discover -s tests -v
```

- [ ] **Step 8: Commit**

```bash
git add toram_skills/semantic_search.py toram_skills/schema.py toram_skills/repository.py tests/test_skill_semantic_search.py
git commit -m "feat: add semantic skill index"
```

---

### Task 5: Ollama Embedding Adapter and Benchmark Machinery

**Files:**
- Create: `toram_skill_embeddings/__init__.py`
- Create: `toram_skill_embeddings/ollama_provider.py`
- Create: `toram_skills/retrieval_benchmark.py`
- Create: `build_skill_embeddings.py`
- Create: `benchmark_skill_embeddings.py`
- Create: `tests/test_skill_embedding_benchmark.py`
- Modify: `tests/test_core_module_boundaries.py`

**Interfaces:**
- `OllamaEmbeddingProvider(model_name, host=None)`.
- `RetrievalMetrics(top1, top3, top5, median_ms, p95_ms)`.
- `ModelBenchmarkResult(model, metrics)`.
- `select_embedding_model(results) -> ModelBenchmarkResult`.

- [ ] **Step 1: Write RED adapter tests with a fake Ollama client**

Assert use of `embed(model=..., input=[...])`, exact response-count validation, and conversion of connection/read/server failures to `EmbeddingUnavailable`.

- [ ] **Step 2: Write RED deterministic selection test**

```python
results = (
    ModelBenchmarkResult("a", RetrievalMetrics(.70, .85, .95, 20.0, 30.0)),
    ModelBenchmarkResult("b", RetrievalMetrics(.75, .90, .95, 30.0, 40.0)),
    ModelBenchmarkResult("c", RetrievalMetrics(.80, .88, .94, 10.0, 15.0)),
)
self.assertEqual(select_embedding_model(results).model, "b")
```

- [ ] **Step 3: Implement Ollama adapter outside `toram_skills`**

When no host is configured, omit explicit host so existing Ollama environment behavior is preserved. Batch inputs as a list and require one vector per input.

- [ ] **Step 4: Implement semantic benchmark metrics**

Warm each model once before timing. For each semantic golden case record whether any expected skill appears in top 1/3/5 plus warmed query-embedding + retrieval latency. JSON output may vary only in latency fields; ranking metrics and selection must be deterministic for a fixed model build/database.

- [ ] **Step 5: Implement two CLIs**

```text
build_skill_embeddings.py
  --database coryn_data/database/skills.sqlite
  --model <required CLI value>
  --host <optional CLI value>
  --batch-size 32

benchmark_skill_embeddings.py
  --database coryn_data/database/skills.sqlite
  --golden tests/fixtures/skill_retrieval_golden.json
  --models embeddinggemma:300m qwen3-embedding:0.6b nomic-embed-text:v1.5
  --output docs/benchmarks/2026-08-12-skill-embedding-benchmark.json
  --host <optional CLI value>
```

The benchmark exits nonzero if any of the three fixed candidates fails; it must not silently select from a partial set.

- [ ] **Step 6: Preserve module boundaries**

Keep the `toram_skills/*.py` ban on importing `ollama`; only `toram_skill_embeddings/ollama_provider.py` and top-level embedding scripts may import it.

- [ ] **Step 7: Verify and commit**

```bash
python -m unittest tests.test_skill_embedding_benchmark tests.test_core_module_boundaries -v
git add toram_skill_embeddings toram_skills/retrieval_benchmark.py build_skill_embeddings.py benchmark_skill_embeddings.py tests/test_skill_embedding_benchmark.py tests/test_core_module_boundaries.py
git commit -m "feat: benchmark local skill embeddings"
```

---

### Task 6: Golden Retrieval Set and Deterministic Hybrid Fusion

**Files:**
- Create: `toram_skills/hybrid_search.py`
- Create: `tests/test_skill_hybrid_search.py`
- Create: `tests/fixtures/skill_retrieval_golden.json`

**Interfaces:**
- `HybridSkillSearcher(repository, semantic_index=None)`.
- `search(query, *, filters=SkillFilters(), limit=10) -> tuple[SkillSearchHit, ...]`.
- `FusionConfig(rrf_k, lexical_weight, semantic_weight)`.
- `tune_fusion(...) -> FusionConfig`.

- [ ] **Step 1: Build a source-backed golden fixture with at least 60 cases**

Required distribution: 15 exact-name/tree cases, 15 lexical terminology/mechanics cases, 20 semantic concept cases, 10 combined typed-filter + text cases. Every expected ID must exist in the canonical DB.

Mandatory source-backed examples include:

```json
{"id":"exact-finale","query":"magic: finale","expected_skill_ids":["weapon_class_skills/magic_skills/magic-finale"],"top_k":1},
{"id":"exact-assassin-stab","query":"assassin stab","expected_skill_ids":["sub_weapon_skills/assassin_skills/assassin-stab"],"top_k":1},
{"id":"semantic-evasion","query":"skills that work with evasion","expected_skill_ids":["sub_weapon_skills/assassin_skills/shadow-walk","sub_weapon_skills/assassin_skills/evasion"],"top_k":5},
{"id":"semantic-poison","query":"skills related to poison stacking","expected_skill_ids":["sub_weapon_skills/assassin_skills/corrosive-poison","sub_weapon_skills/assassin_skills/venom-injection"],"top_k":5}
```

A fixture-validation test rejects empty expected lists, nonexistent skill IDs, duplicate case IDs, or fewer than 60 cases.

- [ ] **Step 2: Write RED hybrid behavior tests**

Prove exact lookup bypasses FTS/semantic, typed filters restrict both channels, semantic unavailability falls back to lexical, multiple document hits collapse to one skill, and ties sort deterministically.

- [ ] **Step 3: Verify RED**

```bash
python -m unittest tests.test_skill_hybrid_search -v
```

- [ ] **Step 4: Implement exact fast path and eligible-set filtering**

Exact candidates that satisfy typed filters return directly with score `1.0`. Otherwise compute the eligible structured set and pass it to lexical and semantic channels.

- [ ] **Step 5: Implement weighted reciprocal-rank fusion**

```python
score = sum(weight[channel] / (rrf_k + rank) for channel, rank in ranks)
```

Never combine raw BM25 and cosine magnitudes. Use equal weights and `rrf_k=60` only as the test bootstrap configuration.

- [ ] **Step 6: Implement fixed fusion tuning grid**

Evaluate `rrf_k` in `{20, 60}` and weights in `{(1.0,1.0),(1.5,1.0),(2.0,1.0),(1.5,0.5),(2.0,0.5)}`. Select by hybrid top-1, then top-3, top-5, lower semantic weight, then lower `rrf_k`.

- [ ] **Step 7: Verify and commit**

```bash
python -m unittest tests.test_skill_hybrid_search -v
git add toram_skills/hybrid_search.py tests/test_skill_hybrid_search.py tests/fixtures/skill_retrieval_golden.json
git commit -m "feat: add hybrid skill retrieval"
```

---

### Task 7: Real Benchmark, Generated Config/DB, and Milestone Acceptance

**Files:**
- Create: `toram_skills/retrieval_config.py`
- Create: `docs/benchmarks/2026-08-12-skill-embedding-benchmark.json`
- Modify: `tests/test_skill_hybrid_search.py`
- Generate: `coryn_data/database/skills.sqlite`

- [ ] **Step 1: Rebuild schema-v2 canonical DB**

```bash
python build_skills.py --source raw_skills --database coryn_data/database/skills.sqlite --json-report /tmp/skills-v2-import.json
```

Require 34 trees, 481 skills, zero import errors, current source manifest, deterministic search-document manifest, and non-empty FTS rows before benchmarking.

- [ ] **Step 2: Run all three real Ollama candidates**

```bash
ollama pull embeddinggemma:300m
ollama pull qwen3-embedding:0.6b
ollama pull nomic-embed-text:v1.5
python benchmark_skill_embeddings.py \
  --database coryn_data/database/skills.sqlite \
  --golden tests/fixtures/skill_retrieval_golden.json \
  --models embeddinggemma:300m qwen3-embedding:0.6b nomic-embed-text:v1.5 \
  --output docs/benchmarks/2026-08-12-skill-embedding-benchmark.json
```

The artifact records per-model top-1/top-3/top-5, median/p95 latency, selected model, source manifest, search-document manifest, and tuned fusion config.

- [ ] **Step 3: Generate retrieval config from the benchmark result**

Implement and call a deterministic writer so no human copies benchmark values by hand:

```python
def render_retrieval_config(selected_model: str, fusion: FusionConfig) -> str:
    return (
        "from toram_skills.hybrid_search import FusionConfig\n\n"
        f"DEFAULT_EMBEDDING_MODEL = {selected_model!r}\n"
        "DEFAULT_FUSION_CONFIG = FusionConfig(\n"
        f"    rrf_k={fusion.rrf_k},\n"
        f"    lexical_weight={fusion.lexical_weight!r},\n"
        f"    semantic_weight={fusion.semantic_weight!r},\n"
        ")\n"
    )
```

Add a test loading both the JSON artifact and `retrieval_config.py` and asserting exact parity.

- [ ] **Step 4: Build final vectors using the selected config**

```bash
python -c "from toram_skills.retrieval_config import DEFAULT_EMBEDDING_MODEL; print(DEFAULT_EMBEDDING_MODEL)" >/tmp/skill-model.txt
python build_skill_embeddings.py --database coryn_data/database/skills.sqlite --model "$(cat /tmp/skill-model.txt)"
```

Require vector-row count to equal current search-document count and embedding manifest to equal the search-document manifest.

- [ ] **Step 5: Run fresh final verification**

```bash
python -m compileall -q toram_skills toram_skill_embeddings build_skills.py build_skill_embeddings.py benchmark_skill_embeddings.py
python -m unittest tests.test_skill_exact_lookup -v
python -m unittest tests.test_skill_structured_search -v
python -m unittest tests.test_skill_lexical_search -v
python -m unittest tests.test_skill_semantic_search -v
python -m unittest tests.test_skill_embedding_benchmark -v
python -m unittest tests.test_skill_hybrid_search -v
python -m unittest tests.test_core_module_boundaries -v
python -m unittest discover -s tests -v
```

- [ ] **Step 6: Run retrieval acceptance gates**

```text
all exact-name golden cases: expected skill in top 1
all structured cases: no result violates typed filters
all lexical cases: expected skill in configured top_k
semantic top-5: exactly matches committed selected-model benchmark result
hybrid top-1/top-3/top-5: exactly matches committed tuned-fusion result
semantic unavailable: deterministic exact/structured/lexical paths still pass
```

Never weaken a source-backed expected result merely to make a metric pass; classify a failure as source-label, retrieval, or benchmark logic and fix the root cause.

- [ ] **Step 7: Verify database provenance**

Require:

```text
schema_version = 2
source_manifest_hash = current raw source manifest
search_document_manifest_hash = current deterministic document manifest
embedding_model = DEFAULT_EMBEDDING_MODEL
embedding_document_manifest_hash = search_document_manifest_hash
embedding vector rows = search document rows
```

- [ ] **Step 8: Commit final milestone output**

```bash
git add toram_skills/retrieval_config.py docs/benchmarks/2026-08-12-skill-embedding-benchmark.json tests/test_skill_hybrid_search.py coryn_data/database/skills.sqlite
git commit -m "feat: finalize hybrid skill retrieval"
```

---

## Milestone 2 Completion Criteria

Milestone 2 is complete only when:

- exact canonical/alias lookup works across trees without an LLM;
- tree browsing and typed structured filters operate on canonical fields;
- schema v2 is reproducibly generated from the current raw corpus;
- FTS5 indexes canonical names, tree names, source-backed text, and sections;
- raw user strings cannot inject FTS syntax;
- semantic documents are deterministic and bounded by skill/section identity;
- vectors are validated, persisted, manifest-bound, and loaded in memory for cosine search;
- the core `toram_skills` package remains free of direct Ollama imports;
- all three fixed embedding candidates are benchmarked on the same golden semantic cases;
- the embedding model and fusion weights are chosen by documented metrics, not intuition;
- hybrid fusion uses rank positions rather than incomparable BM25/cosine magnitudes;
- semantic unavailability degrades to deterministic lexical/structured retrieval;
- exact-name requests bypass semantic retrieval entirely;
- no `gemma4:e4b`, natural-language skill intent routing, Discord skill UI, or formula evaluation is added;
- item/Qwen behavior remains unchanged;
- generated `skills.sqlite` is provenance-verified;
- the full repository test suite remains green.

After this milestone is accepted, write a separate Milestone 3 plan for deterministic skill query routing plus `gemma4:e4b` typed interpretation and grounded skill Q&A.