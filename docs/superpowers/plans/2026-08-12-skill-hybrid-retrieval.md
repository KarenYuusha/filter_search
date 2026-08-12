# Skill Hybrid Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic exact, structured, lexical, and semantic skill retrieval over the canonical skill database, then combine lexical and semantic channels with benchmarked deterministic rank fusion.

**Architecture:** Keep `toram_skills` as the authoritative, LLM-free skill data/search domain. Rebuild `skills.sqlite` as schema version 2 with normalized search-support tables, an FTS5 index, semantic search documents, and persisted document vectors. Keep the Ollama embedding adapter outside `toram_skills`; query routing, `gemma4:e4b`, Discord, and formula evaluation remain out of this milestone.

**Tech Stack:** Python >=3.12; standard-library `dataclasses`, `pathlib`, `sqlite3`, `json`, `hashlib`, `math`, `struct`, `time`, `statistics`; SQLite FTS5; existing `ollama` Python package only in the embedding adapter; existing `unittest` style.

## Global Constraints

- `coryn_data/database/skills.sqlite` remains the canonical generated skill database.
- `raw_skills/` remains the editable source of truth for canonical skill content.
- Exact skill identity, structured fields, and canonical records remain authoritative over FTS/vector similarity.
- Exact-name lookup must not require Ollama or embeddings.
- Structured retrieval must not require Ollama or embeddings.
- FTS5 lexical retrieval must not require Ollama or embeddings.
- Semantic retrieval uses a dedicated embedding model, never `gemma4:e4b`.
- `gemma4:e4b`, natural-language intent parsing, grounded Q&A, Discord integration, and formula evaluation are Milestone 3+ work and must not be added here.
- Do not change item-search semantics, item Qwen behavior, `items.sqlite`, or Discord behavior.
- Do not introduce a hosted vector database.
- No semantic result may become a canonical fact; every result resolves back to a canonical `skill_id`.
- Search-document chunking follows skill/section boundaries, never arbitrary fixed token windows.
- Fuzzy skill names, if introduced at all, are suggestions only and never silently auto-resolve identity.
- Continue the existing `toram_skills` module-boundary rule: it must not import Discord, item-search entrypoints, `toram_search`, or `ollama`.
- The embedding benchmark candidates are fixed for this milestone to `embeddinggemma:300m`, `qwen3-embedding:0.6b`, and `nomic-embed-text:v1.5`.
- Model selection is deterministic: maximize semantic top-5 hit rate; tie-break by top-3, then top-1, then lower warmed median query latency.

## File Structure

```text
toram_skills/
    models.py                    # existing canonical models
    schema.py                    # schema v2: search docs, FTS, vector storage
    repository.py                # canonical + exact/tree retrieval
    search_models.py             # typed filters/results/channel scores
    search_documents.py          # deterministic semantic/lexical documents
    structured_search.py         # typed SQL/filter retrieval
    lexical_search.py            # safe FTS5 querying
    semantic_search.py           # provider protocol, vector codec/index/search
    hybrid_search.py             # exact fast path + deterministic fusion
    retrieval_benchmark.py       # metrics and deterministic model/fusion selection

toram_skill_embeddings/
    __init__.py
    ollama_provider.py           # the only skill-retrieval module importing ollama

build_skills.py                  # existing canonical rebuild, now creates schema v2 docs/FTS
build_skill_embeddings.py        # persist vectors for one chosen model
benchmark_skill_embeddings.py    # run candidate-model benchmark and emit JSON

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
- Produces `SkillFilters`.
- Produces `ChannelScore` and `SkillSearchHit`.
- Produces `SkillRepository.get_tree(tree_id) -> SkillTreeDraft`.
- Produces `SkillRepository.resolve_tree_name(name) -> tuple[SkillTreeDraft, ...]`.
- Produces `SkillRepository.list_skills_in_tree(tree_id) -> tuple[SkillDraft, ...]`.
- Produces `SkillRepository.resolve_skill_name(name, *, tree_id=None) -> tuple[SkillDraft, ...]`.

- [ ] **Step 1: Write RED tests for cross-tree exact resolution**

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

Add a fixture-database test proving an alias row resolves to the canonical skill and that canonical-name matches sort before alias-only matches.

- [ ] **Step 2: Verify RED**

Run:
```bash
python -m unittest tests.test_skill_exact_lookup -v
```
Expected: failure because the new resolver methods/types do not exist.

- [ ] **Step 3: Add immutable typed search models**

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

- [ ] **Step 4: Implement exact repository resolvers with normalized equality only**

Use `normalize_skill_name()` for input. Resolve canonical names and aliases with SQL, deduplicate by skill ID, and order deterministically by `(canonical_match first, tree_id, source_order, id)`. Do not add fuzzy matching in this task.

- [ ] **Step 5: Run focused + full regression tests**

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
- Consumes `SkillFilters`.
- Produces `structured_skill_ids(repository, filters) -> tuple[str, ...]`.
- Adds normalized tree-level weapon restriction rows for querying.

- [ ] **Step 1: Write RED structured-filter tests**

Cover these exact semantics:

```python
filters = SkillFilters(tree_ids=("weapon_class_skills/magic_skills",), tiers=(4,))
ids = structured_skill_ids(repo, filters)
self.assertIn("weapon_class_skills/magic_skills/magic-finale", ids)

filters = SkillFilters(mp_cost_max=100)
ids = structured_skill_ids(repo, filters)
self.assertTrue(all(repo.get_skill(skill_id).mp_cost_value <= 100 for skill_id in ids))

filters = SkillFilters(ailments=("Tumble",))
ids = structured_skill_ids(repo, filters)
self.assertGreater(len(ids), 0)
```

Add fixture tests for `skill_types`, `damage_types`, and `weapons`. Across different fields use AND semantics; multiple values inside one field use OR semantics.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_structured_search -v
```

- [ ] **Step 3: Extend schema with normalized tree restrictions**

Add:

```sql
CREATE TABLE skill_tree_weapon_restrictions (
    tree_id TEXT NOT NULL REFERENCES skill_trees(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    weapon TEXT NOT NULL,
    normalized_weapon TEXT NOT NULL,
    PRIMARY KEY(tree_id, position)
);
```

Also add `normalized_name` columns to `skill_ailments`, `skill_weapon_requirements`, and `skill_weapon_restrictions`. Bump generated `schema_version` from `1` to `2`. Since this database is generated from raw source, rebuild instead of writing an in-place v1 migration.

- [ ] **Step 4: Populate normalized query rows during import**

Use the same casefold/whitespace normalization principle as skill names. Preserve display values unchanged in the canonical fields/rows.

- [ ] **Step 5: Implement parameterized SQL filtering**

Build SQL clauses only from `SkillFilters`; never interpolate user strings into SQL. Use `EXISTS` subqueries for ailments/weapons so multi-valued joins do not duplicate skill IDs. `weapons` matches explicit skill requirements/restrictions OR parent-tree restrictions.

- [ ] **Step 6: Rebuild a temporary schema-v2 DB and run tests**

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
- Produces `SkillSearchDocument(id, skill_id, position, kind, label, text, text_hash)`.
- Produces `build_search_documents(tree, skill) -> tuple[SkillSearchDocument, ...]`.
- Produces `search_document_manifest_hash(documents) -> str`.
- Produces `lexical_search(repository, query, *, eligible_skill_ids=None, limit=20) -> tuple[SkillSearchHit, ...]`.

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
    hits = lexical_search(repo, '" OR * NEAR(', limit=5)
    self.assertIsInstance(hits, tuple)
```

Add lexical cases for `Tumble`, `Venom`, `AMPR`, and a formula/mechanics phrase found in the corpus.

- [ ] **Step 2: Verify RED**

```bash
python -m unittest tests.test_skill_lexical_search -v
```

- [ ] **Step 3: Define deterministic search documents**

Create one `summary` document for every skill containing canonical name, tree name, aliases, structured scalar facts, description, game description, and section labels. Create one additional document per non-empty canonical `SkillSection`, preserving that section body. For skills with no sections and little normalized prose, create one `source` document from `raw_text`. Do not split any document by token count.

Document IDs are stable:

```text
<skill_id>#summary
<skill_id>#section:<zero-based-position>
<skill_id>#source
```

`text_hash = sha256(text.encode("utf-8")).hexdigest()`.

- [ ] **Step 4: Add schema-v2 search tables and FTS5**

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

Populate both tables deterministically during `build_skills.py`. Store metadata key `search_document_manifest_hash`.

- [ ] **Step 5: Implement safe FTS query construction**

Tokenize with a plain Unicode word regex; never pass the raw user string as FTS syntax. Query all normalized tokens with `AND`; if there are no results and more than one token, retry with `OR`. Rank documents by SQLite `bm25(skill_fts)`, collapse to each skill's best-ranked document, and expose the best document ID as evidence.

- [ ] **Step 6: Verify FTS survives canonical rebuild**

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

### Task 4: Semantic Vector Storage and Pure-Python Semantic Search

**Files:**
- Create: `toram_skills/semantic_search.py`
- Modify: `toram_skills/schema.py`
- Modify: `toram_skills/repository.py`
- Create: `tests/test_skill_semantic_search.py`

**Interfaces:**
- Produces `EmbeddingProvider` protocol with `model_name` and `embed(texts)`.
- Produces `EmbeddingUnavailable` and `EmbeddingIndexError`.
- Produces `build_embedding_index(repository, provider, *, batch_size=32) -> int`.
- Produces `SemanticSkillIndex.from_repository(repository, provider) -> SemanticSkillIndex`.
- Produces `SemanticSkillIndex.search(query, *, eligible_skill_ids=None, limit=20) -> tuple[SkillSearchHit, ...]`.

- [ ] **Step 1: Write RED tests using a fake embedding provider**

Use a deterministic fake that maps selected words to small vectors. Prove:

```python
index = SemanticSkillIndex.from_repository(repo, fake_provider)
hits = index.search("avoid attacks", limit=3)
self.assertEqual(hits[0].skill_id, expected_skill_id)
```

Also prove dimension mismatch, zero vectors, NaN/Inf vectors, stale document manifest, and model mismatch fail closed without returning fabricated results.

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

Metadata keys after a successful vector build:

```text
embedding_model
embedding_dimensions
embedding_document_manifest_hash
```

- [ ] **Step 4: Implement deterministic float codec and validation**

Store vectors as little-endian IEEE-754 float32 using `struct.pack(f"<{n}f", *values)`. Decode with the corresponding format. Reject empty, non-finite, mixed-dimension, and zero-norm vectors. Normalize vectors to unit length before persistence so cosine search is a dot product.

- [ ] **Step 5: Implement atomic embedding-index replacement**

Embed all current search documents in deterministic ID order, validate every returned vector, then replace `skill_embedding_vectors` and embedding metadata inside one SQLite transaction. A failed provider call must leave the previous valid vector index untouched.

- [ ] **Step 6: Implement in-memory semantic search**

Load persisted document vectors only when model name and `search_document_manifest_hash` match metadata. Embed one query through the supplied provider, normalize it, score every eligible document by dot product, sort by `(-similarity, document_id)`, then collapse each skill to its best document.

- [ ] **Step 7: Run focused + regression tests**

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

### Task 5: Ollama Embedding Adapter and Reproducible Embedding Benchmark

**Files:**
- Create: `toram_skill_embeddings/__init__.py`
- Create: `toram_skill_embeddings/ollama_provider.py`
- Create: `toram_skills/retrieval_benchmark.py`
- Create: `build_skill_embeddings.py`
- Create: `benchmark_skill_embeddings.py`
- Create: `tests/test_skill_embedding_benchmark.py`
- Modify: `tests/test_core_module_boundaries.py`

**Interfaces:**
- Produces `OllamaEmbeddingProvider(model_name, host=None)`.
- Produces benchmark `RetrievalCase`, `RetrievalMetrics`, `ModelBenchmarkResult` dataclasses.
- Produces `evaluate_semantic_cases(...)` and `select_embedding_model(...)`.
- CLI can build one model's vectors or benchmark the fixed three-model shortlist.

- [ ] **Step 1: Write RED adapter tests with a fake Ollama client**

Assert that the adapter calls the official `embed(model=..., input=[...])` API and converts connection/read/server errors to `EmbeddingUnavailable`. No Ollama import is added under `toram_skills/`.

- [ ] **Step 2: Write RED benchmark-selection tests**

```python
results = (
    ModelBenchmarkResult("a", top1=0.70, top3=0.85, top5=0.95, median_ms=20.0),
    ModelBenchmarkResult("b", top1=0.75, top3=0.90, top5=0.95, median_ms=30.0),
    ModelBenchmarkResult("c", top1=0.80, top3=0.88, top5=0.94, median_ms=10.0),
)
self.assertEqual(select_embedding_model(results).model, "b")
```

This proves the required priority: top-5, then top-3, then top-1, then lower median latency.

- [ ] **Step 3: Implement the Ollama adapter outside the core package**

Default host behavior should match the existing project: omit explicit host when none is configured so the Ollama library/environment selects it. Batch inputs are passed as a list. Validate response count exactly equals input count.

- [ ] **Step 4: Implement benchmark metrics**

For every semantic golden case, compute whether any expected skill ID appears in top 1/3/5. Warm each model once before timing. Record warmed query-embedding + retrieval latency per query; report median and p95. Benchmark output is deterministic JSON except latency fields.

- [ ] **Step 5: Implement the two thin CLIs**

`build_skill_embeddings.py`:

```text
--database coryn_data/database/skills.sqlite
--model <required>
--host <optional>
--batch-size 32
```

`benchmark_skill_embeddings.py`:

```text
--database coryn_data/database/skills.sqlite
--golden tests/fixtures/skill_retrieval_golden.json
--models embeddinggemma:300m qwen3-embedding:0.6b nomic-embed-text:v1.5
--output docs/benchmarks/2026-08-12-skill-embedding-benchmark.json
--host <optional>
```

The benchmark exits nonzero if any candidate cannot be evaluated; it must not silently select from a partial shortlist.

- [ ] **Step 6: Preserve module boundaries**

Extend the AST boundary test so `toram_skills/*.py` still cannot import `ollama`; permit it only in `toram_skill_embeddings/ollama_provider.py` and top-level benchmark/build scripts.

- [ ] **Step 7: Run focused tests**

```bash
python -m unittest tests.test_skill_embedding_benchmark tests.test_core_module_boundaries -v
```

- [ ] **Step 8: Commit**

```bash
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
- Produces `HybridSkillSearcher(repository, semantic_index=None)`.
- Produces `search(query, *, filters=SkillFilters(), limit=10) -> tuple[SkillSearchHit, ...]`.
- Produces `tune_fusion(golden_cases, lexical_results, semantic_results) -> FusionConfig`.

- [ ] **Step 1: Build a source-backed golden fixture with at least 60 cases**

Required distribution:

```text
15 exact-name / tree-qualified cases
15 lexical terminology/mechanics cases
20 semantic concept cases
10 combined filter + text cases
```

Every case stores `id`, `query`, `expected_skill_ids`, `top_k`, and optional typed `filters`. Every expected ID must exist in the canonical DB. Representative mandatory cases include:

```json
{"id":"exact-finale","query":"magic: finale","expected_skill_ids":["weapon_class_skills/magic_skills/magic-finale"],"top_k":1},
{"id":"exact-assassin-stab","query":"assassin stab","expected_skill_ids":["sub_weapon_skills/assassin_skills/assassin-stab"],"top_k":1},
{"id":"semantic-evasion","query":"skills that work with evasion","expected_skill_ids":[],"top_k":5},
{"id":"semantic-poison","query":"skills related to poison stacking","expected_skill_ids":[],"top_k":5}
```

For the semantic cases, fill `expected_skill_ids` only after verifying the corresponding canonical/raw skill text; empty expected lists are invalid and the fixture validation test must reject them.

- [ ] **Step 2: Write RED hybrid tests**

Prove:

1. exact canonical name bypasses FTS and semantic calls;
2. typed filters restrict both lexical and semantic candidates;
3. semantic unavailability falls back to lexical rather than failing the search;
4. duplicate document hits collapse to one skill;
5. fusion order is deterministic under equal scores.

- [ ] **Step 3: Verify RED**

```bash
python -m unittest tests.test_skill_hybrid_search -v
```

- [ ] **Step 4: Implement exact fast path and eligible-set filtering**

If `resolve_skill_name(query)` returns exact candidates that satisfy the typed filters, return those candidates directly with score `1.0` and no lexical/semantic channel calls. Otherwise compute the eligible set from `structured_skill_ids()` and pass it to both channels.

- [ ] **Step 5: Implement weighted reciprocal-rank fusion**

Use:

```python
score = sum(weight[channel] / (rrf_k + rank) for each participating channel)
```

Do not use raw BM25/cosine scores across channels. Initial config for tests is equal weights with `rrf_k=60`; the committed production config is replaced by the benchmark-selected setting in Task 7.

- [ ] **Step 6: Implement deterministic fusion tuning grid**

Evaluate only these configurations:

```text
rrf_k: 20 or 60
(lexical_weight, semantic_weight):
(1.0, 1.0)
(1.5, 1.0)
(2.0, 1.0)
(1.5, 0.5)
(2.0, 0.5)
```

Select by hybrid top-1, then top-3, then top-5; tie-break by lower semantic weight, then lower `rrf_k`. This honors the design requirement that exact/lexical terminology is not subordinated to semantic similarity.

- [ ] **Step 7: Run golden-set validation and focused tests**

```bash
python -m unittest tests.test_skill_hybrid_search -v
```

- [ ] **Step 8: Commit**

```bash
git add toram_skills/hybrid_search.py tests/test_skill_hybrid_search.py tests/fixtures/skill_retrieval_golden.json
git commit -m "feat: add hybrid skill retrieval"
```

---

### Task 7: Real Embedding Benchmark, Selected Default, Generated DB, and Milestone Acceptance

**Files:**
- Create: `toram_skills/retrieval_config.py`
- Create: `docs/benchmarks/2026-08-12-skill-embedding-benchmark.json`
- Modify: `tests/test_skill_hybrid_search.py`
- Generate: `coryn_data/database/skills.sqlite`

**Interfaces:**
- `DEFAULT_EMBEDDING_MODEL: str` is the benchmark winner.
- `DEFAULT_FUSION_CONFIG: FusionConfig` is the golden-set winner.

- [ ] **Step 1: Rebuild schema-v2 canonical DB before benchmarking**

```bash
python build_skills.py \
  --source raw_skills \
  --database coryn_data/database/skills.sqlite \
  --json-report /tmp/skills-v2-import.json
```

Acceptance before embeddings: 34 trees, 481 skills, zero import errors, current source manifest, deterministic search-document manifest, and non-empty FTS index.

- [ ] **Step 2: Run the real fixed-shortlist embedding benchmark**

The execution environment must have current Ollama and these three models available:

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

Do not pick a model until all three completed successfully. The JSON must include per-model top-1/top-3/top-5, median/p95 latency, selected model, corpus manifest, and search-document manifest.

- [ ] **Step 3: Persist the deterministic selected model/config**

Write `toram_skills/retrieval_config.py` from the benchmark result, for example structurally:

```python
DEFAULT_EMBEDDING_MODEL = "<actual benchmark winner>"
DEFAULT_FUSION_CONFIG = FusionConfig(
    rrf_k=<actual tuned value>,
    lexical_weight=<actual tuned value>,
    semantic_weight=<actual tuned value>,
)
```

The values are not chosen manually; they must exactly match the committed benchmark artifact. Add a regression test that reads the JSON and asserts parity.

- [ ] **Step 4: Build final document vectors with only the selected model**

```bash
python build_skill_embeddings.py \
  --database coryn_data/database/skills.sqlite \
  --model "$(python -c 'from toram_skills.retrieval_config import DEFAULT_EMBEDDING_MODEL; print(DEFAULT_EMBEDDING_MODEL)')"
```

Verify metadata model/document manifest and vector row count equal the current search-document count.

- [ ] **Step 5: Run fresh milestone verification**

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

- [ ] **Step 6: Run retrieval acceptance metrics from the committed DB/config**

Required gates:

```text
all exact-name golden cases: expected skill in top 1
all structured cases: no result violates typed filters
all lexical cases: expected skill in configured top_k
semantic top-5 hit rate: equals the selected-model benchmark artifact
hybrid top-1/top-3/top-5: equals the tuned-fusion benchmark artifact
deterministic channels still pass with semantic provider unavailable
```

If a golden case fails, classify it as data-label, retrieval, or benchmark issue and fix the root cause; never weaken an expected result merely to make the metric pass.

- [ ] **Step 7: Verify final database provenance**

Assert:

```text
schema_version = 2
source_manifest_hash = current raw source manifest
search_document_manifest_hash = current deterministic document manifest
embedding_model = DEFAULT_EMBEDDING_MODEL
embedding_document_manifest_hash = search_document_manifest_hash
embedding vector rows = search document rows
```

- [ ] **Step 8: Commit final Milestone 2 output**

```bash
git add toram_skills/retrieval_config.py docs/benchmarks/2026-08-12-skill-embedding-benchmark.json tests/test_skill_hybrid_search.py coryn_data/database/skills.sqlite
git commit -m "feat: finalize hybrid skill retrieval"
```

---

## Milestone 2 Completion Criteria

Milestone 2 is complete only when:

- exact canonical/alias skill lookup works across trees without an LLM;
- tree browsing and typed structured filters operate on canonical fields;
- schema v2 is generated reproducibly from the current raw corpus;
- FTS5 lexical search indexes canonical names, tree names, source-backed text, and sections;
- raw user strings cannot inject FTS syntax;
- semantic documents are deterministic and bounded by skill/section identity;
- vectors are validated, persisted, manifest-bound, and loaded in memory for cosine search;
- the core `toram_skills` package remains free of direct Ollama imports;
- all three fixed embedding candidates are benchmarked on the same golden semantic cases;
- the selected embedding model is chosen by the documented metric order, not intuition;
- hybrid fusion uses rank-based scores, not incomparable BM25/cosine magnitudes;
- the committed fusion configuration comes from the golden-set tuning grid;
- semantic unavailability degrades to deterministic lexical/structured retrieval;
- exact-name requests bypass semantic retrieval entirely;
- no `gemma4:e4b`, natural-language skill intent routing, Discord skill UI, or formula-evaluation code is added;
- item/Qwen behavior remains unchanged;
- the generated `skills.sqlite` is current and provenance-verified;
- the full repository test suite remains green.

After this milestone is accepted, write a separate Milestone 3 plan for deterministic skill query routing plus `gemma4:e4b` typed interpretation and grounded skill Q&A.