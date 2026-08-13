# Skill Bot Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `skill ...` Discord search that returns canonical skill details or ranked free-text skill matches without changing the existing item/stat/help/database/Qwen flow.

**Architecture:** Add a frontend-neutral `toram_skill_search` application layer above `toram_skills`, with deterministic command parsing, canonical payloads, and a lazy cached MiniLM semantic runtime. The Discord layer intercepts only token-bounded `skill` commands, runs the skill application in `asyncio.to_thread(...)`, and renders skill-specific embeds/views; all non-skill queries continue through the existing `SearchService` unchanged.

**Tech Stack:** Python >=3.12; standard-library `dataclasses`, `pathlib`, `sqlite3`, `threading`; SQLite skill database; existing `toram_skills` hybrid retrieval; optional `sentence-transformers>=5,<6` and `transformers>=4.41,<5`; `discord.py>=2.7.1,<3`; existing `unittest` style.

## Global Constraints

- Users must explicitly start skill queries with the token `skill`; `skills`, `skillful`, and unrelated text must stay on the existing item path.
- Skill commands are free-text only in this milestone; do not parse `tier`, `mp<=`, weapon, ailment, or other structured filters.
- Skill commands never invoke Qwen or the item fallback service.
- Skill commands never mutate or clear `FailedQueryContext`.
- Exact canonical/alias lookup must complete before MiniLM/provider initialization.
- Free-text retrieval uses `sentence-transformers/all-MiniLM-L6-v2`, config `symmetric-encode-v1`, and `FusionConfig(rrf_k=20, lexical_weight=1.5, semantic_weight=1.0)` from `toram_skills.retrieval_config`.
- Provider/index/query failures must degrade to lexical skill retrieval instead of failing the skill command.
- A missing, unreadable, corrupt, or schema-invalid `skills.sqlite` must produce a skill-specific unavailable response without affecting item/stat search.
- The semantic runtime cache must never retain a request-scoped SQLite connection.
- Do not add a semantic similarity confidence threshold in this milestone. A no-match response is only produced when the retrieval channels actually return no hits.
- Do not change canonical skill content, item search semantics, item Qwen behavior, or existing non-skill Discord behavior.
- `toram_skills` must remain Discord-free and must not import `toram_skill_search`.
- `toram_skill_search` must remain Discord-free.
- Non-skill request execution must not initialize Sentence Transformers or open `skills.sqlite`.

## File Structure

Create:

```text
toram_skill_search/
    __init__.py          # public application-layer exports only
    models.py            # immutable frontend-neutral skill payloads
    runtime.py           # lazy provider/semantic-index cache
    service.py           # skill command parser + DB-backed application service

tests/
    test_skill_search_service.py
    test_discord_skill_search.py
```

Modify:

```text
toram_discord/config.py      # independent skill database path
toram_discord/app.py         # explicit-prefix routing only
toram_discord/render.py      # skill help/results/detail embeds
toram_discord/views.py       # sync bridge + skill result/detail interactions
toram_discord/sessions.py    # no new skill domain state; reuse page/generation only
discord_bot.py               # compatibility re-exports for new Discord helpers
.env.example                 # optional SKILL_DATABASE_PATH setting
tests/test_discord_bot.py
tests/test_discord_module_boundaries.py
```

Do not modify `toram_search/service.py`, `toram_search/fallback.py`, or `toram_skills/hybrid_search.py` unless a failing test demonstrates that the existing documented interface cannot satisfy the approved spec.

---

### Task 1: Frontend-Neutral Skill Command and Canonical Payloads

**Files:**
- Create: `toram_skill_search/__init__.py`
- Create: `toram_skill_search/models.py`
- Create: `toram_skill_search/service.py`
- Test: `tests/test_skill_search_service.py`

**Interfaces:**
- Consumes: `SkillRepository`, `HybridSkillSearcher`, `DEFAULT_FUSION_CONFIG`, `SkillDraft`, `SkillTreeDraft`.
- Produces:
  - `parse_skill_command(query: str) -> str | None`
  - `SkillDetailPayload(skill: SkillDraft, tree: SkillTreeDraft)`
  - `SkillResultItem(skill: SkillDraft, tree: SkillTreeDraft, snippet: str)`
  - `SkillResultsPayload(query: str, results: tuple[SkillResultItem, ...])`
  - `SkillHelpPayload(text: str)`
  - `SkillUnavailablePayload(text: str)`
  - `SkillPayload = SkillDetailPayload | SkillResultsPayload | SkillHelpPayload | SkillUnavailablePayload`
  - `SkillSearchService(repository: SkillRepository, *, semantic_runtime: object | None = None)`
  - `SkillSearchService.handle(query: str) -> SkillPayload`

- [ ] **Step 1: Write RED command-parser and exact-resolution tests**

Create `tests/test_skill_search_service.py` with the canonical database fixture:

```python
from __future__ import annotations

from pathlib import Path
import unittest

from toram_skill_search.service import SkillSearchService, parse_skill_command
from toram_skills.repository import SkillRepository

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "coryn_data" / "database" / "skills.sqlite"


class ExplodingSemanticRuntime:
    def get_index(self, repository):
        raise AssertionError("exact lookup must not initialize semantic runtime")


class SkillCommandParserTests(unittest.TestCase):
    def test_token_bounded_case_insensitive_prefix(self):
        self.assertEqual(parse_skill_command("skill magic finale"), "magic finale")
        self.assertEqual(parse_skill_command("  SKILL   attack while moving  "), "attack while moving")
        self.assertEqual(parse_skill_command("skill"), "")
        self.assertIsNone(parse_skill_command("skills magic finale"))
        self.assertIsNone(parse_skill_command("skillful magic finale"))
        self.assertIsNone(parse_skill_command("magic finale"))


class SkillSearchServiceTests(unittest.TestCase):
    def setUp(self):
        self.repo = SkillRepository(DATABASE)

    def tearDown(self):
        self.repo.close()

    def test_bare_skill_remainder_returns_help(self):
        payload = SkillSearchService(self.repo, semantic_runtime=ExplodingSemanticRuntime()).handle("")
        self.assertEqual(type(payload).__name__, "SkillHelpPayload")
        self.assertIn("skill magic finale", payload.text.casefold())

    def test_exact_canonical_name_returns_detail_without_semantic_runtime(self):
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("magic: finale")
        self.assertEqual(type(payload).__name__, "SkillDetailPayload")
        self.assertEqual(payload.skill.id, "weapon_class_skills/magic_skills/magic-finale")
        self.assertEqual(payload.tree.id, payload.skill.tree_id)
```

Add one exact-alias case using an alias already present in the canonical DB, and one synthetic/fake-repository test where `resolve_skill_name()` returns two skills to prove multiple exact matches produce `SkillResultsPayload` rather than arbitrary detail.

- [ ] **Step 2: Run the focused tests to verify RED**

Run:

```bash
python -m unittest tests.test_skill_search_service.SkillCommandParserTests tests.test_skill_search_service.SkillSearchServiceTests -v
```

Expected: FAIL because `toram_skill_search` and the payload/service interfaces do not exist yet.

- [ ] **Step 3: Add immutable payload models**

Create `toram_skill_search/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass

from toram_skills.models import SkillDraft, SkillTreeDraft


@dataclass(frozen=True)
class SkillDetailPayload:
    skill: SkillDraft
    tree: SkillTreeDraft


@dataclass(frozen=True)
class SkillResultItem:
    skill: SkillDraft
    tree: SkillTreeDraft
    snippet: str


@dataclass(frozen=True)
class SkillResultsPayload:
    query: str
    results: tuple[SkillResultItem, ...]


@dataclass(frozen=True)
class SkillHelpPayload:
    text: str


@dataclass(frozen=True)
class SkillUnavailablePayload:
    text: str


SkillPayload = SkillDetailPayload | SkillResultsPayload | SkillHelpPayload | SkillUnavailablePayload
```

Export these names from `toram_skill_search/__init__.py` together with the service/parser added below.

- [ ] **Step 4: Implement deterministic prefix parsing and exact-first service behavior**

Create `toram_skill_search/service.py` with this parser contract:

```python
def parse_skill_command(query: str) -> str | None:
    parts = str(query).strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "skill":
        return None
    if len(parts) == 1:
        return ""
    return " ".join(parts[1].split())
```

Add helpers that always resolve tree data canonically and create deterministic snippets:

```python
def _snippet(skill: SkillDraft) -> str:
    for text in (skill.description, skill.game_description):
        if text and text.strip():
            return " ".join(text.split())
    metadata = [
        value
        for value in (
            f"Tier {skill.tier}" if skill.tier is not None else None,
            skill.skill_type,
            f"MP {skill.mp_cost_text}" if skill.mp_cost_text else None,
            skill.damage_type,
        )
        if value
    ]
    return " · ".join(metadata) or "Canonical skill record"


def _result_item(repository: SkillRepository, skill: SkillDraft) -> SkillResultItem:
    return SkillResultItem(
        skill=skill,
        tree=repository.get_tree(skill.tree_id),
        snippet=_snippet(skill),
    )
```

Implement `SkillSearchService.handle()` in this exact order:

```python
class SkillSearchService:
    def __init__(self, repository: SkillRepository, *, semantic_runtime=None) -> None:
        self.repository = repository
        self.semantic_runtime = semantic_runtime

    def handle(self, query: str) -> SkillPayload:
        cleaned = " ".join(str(query).split())
        if not cleaned:
            return SkillHelpPayload(
                "Search Toram skills with `skill <words>`. Examples: "
                "`skill magic finale`, `skill attack while moving`, `skill inflict tumble`."
            )

        exact = self.repository.resolve_skill_name(cleaned)
        if len(exact) == 1:
            skill = exact[0]
            return SkillDetailPayload(skill, self.repository.get_tree(skill.tree_id))
        if len(exact) > 1:
            return SkillResultsPayload(
                cleaned,
                tuple(_result_item(self.repository, skill) for skill in exact),
            )

        return self._search_free_text(cleaned)
```

For now `_search_free_text()` should use `HybridSkillSearcher(repository, semantic_index=None, fusion_config=DEFAULT_FUSION_CONFIG)` so Task 1 has a complete lexical-only implementation before the lazy runtime is introduced in Task 2. Resolve each hit through `repository.get_skill(hit.skill_id)` and `_result_item(...)`; preserve hit order; return an empty `SkillResultsPayload` when there are no hits. Do not add any score threshold.

- [ ] **Step 5: Add lexical free-text and canonical-resolution tests**

Append tests using the real canonical DB:

```python
def test_free_text_returns_canonical_ranked_results(self):
    payload = SkillSearchService(self.repo).handle("inflict tumble")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
    self.assertTrue(payload.results)
    self.assertTrue(all(item.skill.tree_id == item.tree.id for item in payload.results))
    self.assertTrue(all(item.snippet for item in payload.results))


def test_free_text_does_not_apply_structured_filter_syntax(self):
    payload = SkillSearchService(self.repo).handle("sword tier 4")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
```

The second test only proves the string is accepted as retrieval text; do not assert filter semantics.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_skill_search_service -v
```

Expected: PASS.

Commit:

```bash
git add toram_skill_search tests/test_skill_search_service.py
git commit -m "feat: add skill search application core"
```

---

### Task 2: Lazy MiniLM Semantic Runtime With Lexical Degradation

**Files:**
- Create: `toram_skill_search/runtime.py`
- Modify: `toram_skill_search/service.py`
- Modify: `toram_skill_search/__init__.py`
- Test: `tests/test_skill_search_service.py`

**Interfaces:**
- Consumes: `DEFAULT_EMBEDDING_PROVIDER`, `DEFAULT_EMBEDDING_MODEL`, `DEFAULT_EMBEDDING_CONFIG_ID`, `DEFAULT_FUSION_CONFIG`, `SemanticSkillIndex.from_repository()`, `EmbeddingUnavailable`, `EmbeddingIndexError`.
- Produces:
  - `SemanticRuntimeCache.get_index(repository: SkillRepository) -> SemanticSkillIndex | None`
  - `DEFAULT_SEMANTIC_RUNTIME: SemanticRuntimeCache`
  - `SkillSearchService(..., semantic_runtime=DEFAULT_SEMANTIC_RUNTIME)`

- [ ] **Step 1: Write RED runtime tests**

Add fakes to `tests/test_skill_search_service.py`:

```python
class FakeSemanticIndex:
    def __init__(self, hits=(), error=None):
        self.hits = tuple(hits)
        self.error = error
        self.calls = []

    def search(self, query, *, eligible_skill_ids=None, limit=20):
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        return self.hits[:limit]


class FixedRuntime:
    def __init__(self, index):
        self.index = index
        self.calls = 0

    def get_index(self, repository):
        self.calls += 1
        return self.index
```

Add tests:

```python
def test_non_exact_query_requests_semantic_runtime(self):
    runtime = FixedRuntime(None)
    SkillSearchService(self.repo, semantic_runtime=runtime).handle("attack while moving")
    self.assertEqual(runtime.calls, 1)


def test_semantic_query_failure_retries_lexical_only(self):
    from toram_skills.semantic_search import EmbeddingUnavailable

    semantic = FakeSemanticIndex(error=EmbeddingUnavailable("offline"))
    payload = SkillSearchService(
        self.repo,
        semantic_runtime=FixedRuntime(semantic),
    ).handle("inflict tumble")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
    self.assertTrue(payload.results)
```

Add a cache-unit test with a fake provider/index factory proving two `get_index()` calls for the same resolved database path + search-document manifest return the same in-memory index and do not rebuild it.

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```bash
python -m unittest tests.test_skill_search_service -v
```

Expected: FAIL because `SemanticRuntimeCache` and semantic-aware `_search_free_text()` do not exist.

- [ ] **Step 3: Implement `SemanticRuntimeCache` with lazy imports and connection-free cached state**

Create `toram_skill_search/runtime.py`:

```python
from __future__ import annotations

from pathlib import Path
import threading

from toram_skills.repository import SkillRepository
from toram_skills.retrieval_config import (
    DEFAULT_EMBEDDING_CONFIG_ID,
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_EMBEDDING_PROVIDER,
)
from toram_skills.semantic_search import (
    EmbeddingIndexError,
    EmbeddingUnavailable,
    SemanticSkillIndex,
)


class SemanticRuntimeCache:
    def __init__(self, *, provider_factory=None, index_factory=None) -> None:
        self._lock = threading.Lock()
        self._provider_factory = provider_factory
        self._index_factory = index_factory or SemanticSkillIndex.from_repository
        self._provider = None
        self._indexes: dict[tuple[str, str], SemanticSkillIndex] = {}

    def _make_provider(self):
        if self._provider_factory is not None:
            return self._provider_factory()
        from toram_skill_embeddings.providers import EmbeddingProviderSpec, build_provider
        return build_provider(
            EmbeddingProviderSpec(
                provider=DEFAULT_EMBEDDING_PROVIDER,
                model=DEFAULT_EMBEDDING_MODEL,
            )
        )

    def get_index(self, repository: SkillRepository) -> SemanticSkillIndex | None:
        manifest = repository.get_metadata("search_document_manifest_hash") or ""
        key = (str(Path(repository.database_path).resolve()), manifest)
        cached = self._indexes.get(key)
        if cached is not None:
            return cached
        try:
            with self._lock:
                cached = self._indexes.get(key)
                if cached is not None:
                    return cached
                provider = self._provider
                if provider is None:
                    provider = self._make_provider()
                    if str(provider.config_id) != DEFAULT_EMBEDDING_CONFIG_ID:
                        raise EmbeddingIndexError("Configured embedding runtime ID does not match retrieval config")
                    self._provider = provider
                index = self._index_factory(repository, provider)
                self._indexes[key] = index
                return index
        except (EmbeddingUnavailable, EmbeddingIndexError, ImportError):
            return None
```

Do not store `repository` in the cache. `SemanticSkillIndex.from_repository()` already copies the vectors into immutable Python tuples, so cached index state is connection-free.

Instantiate:

```python
DEFAULT_SEMANTIC_RUNTIME = SemanticRuntimeCache()
```

Export both names from `toram_skill_search/__init__.py`.

- [ ] **Step 4: Upgrade free-text search to hybrid + retry lexical on semantic query failure**

Modify `_search_free_text()` in `toram_skill_search/service.py`:

```python
def _search_free_text(self, query: str) -> SkillResultsPayload:
    semantic_index = (
        None
        if self.semantic_runtime is None
        else self.semantic_runtime.get_index(self.repository)
    )
    searcher = HybridSkillSearcher(
        self.repository,
        semantic_index=semantic_index,
        fusion_config=DEFAULT_FUSION_CONFIG,
    )
    try:
        hits = searcher.search(query, limit=20)
    except (EmbeddingUnavailable, EmbeddingIndexError):
        hits = HybridSkillSearcher(
            self.repository,
            semantic_index=None,
            fusion_config=DEFAULT_FUSION_CONFIG,
        ).search(query, limit=20)
    return SkillResultsPayload(
        query,
        tuple(
            _result_item(self.repository, self.repository.get_skill(hit.skill_id))
            for hit in hits
        ),
    )
```

Set the constructor default to `DEFAULT_SEMANTIC_RUNTIME`, but preserve explicit `semantic_runtime=None` as a lexical-only test/integration seam.

- [ ] **Step 5: Add invalid-index retry and concurrency/cache tests**

Add tests proving:

```python
from concurrent.futures import ThreadPoolExecutor


def test_runtime_cache_installs_one_index_for_same_database_manifest(self):
    build_calls = []
    sentinel = object()

    class Provider:
        provider_name = "sentence-transformers"
        model_name = "sentence-transformers/all-MiniLM-L6-v2"
        config_id = "symmetric-encode-v1"

    runtime = SemanticRuntimeCache(
        provider_factory=lambda: Provider(),
        index_factory=lambda repository, provider: build_calls.append(repository.database_path) or sentinel,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(executor.map(lambda _: runtime.get_index(self.repo), range(4)))
    self.assertTrue(all(value is sentinel for value in values))
    self.assertEqual(len(build_calls), 1)
```

Also add a test where `index_factory` raises `EmbeddingIndexError("stale")`; `get_index()` must return `None`, and a later exact lookup must still return detail.

- [ ] **Step 6: Run focused tests and commit**

Run:

```bash
python -m unittest tests.test_skill_search_service tests.test_skill_hybrid_search -v
```

Expected: PASS.

Commit:

```bash
git add toram_skill_search tests/test_skill_search_service.py
git commit -m "feat: add lazy skill semantic runtime"
```

---

### Task 3: Database Ownership, Unavailable Payload, and Discord Configuration

**Files:**
- Modify: `toram_skill_search/service.py`
- Modify: `toram_skill_search/__init__.py`
- Modify: `toram_discord/config.py`
- Modify: `.env.example`
- Modify: `tests/test_skill_search_service.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: `SkillRepository(Path)`, `verify_schema()` behavior, `DiscordBotConfig`.
- Produces:
  - `run_skill_search(database_path: Path, query: str, *, repository_factory=SkillRepository, semantic_runtime=DEFAULT_SEMANTIC_RUNTIME) -> SkillPayload`
  - `DiscordBotConfig.skill_database_path: Path`
  - optional environment variable `SKILL_DATABASE_PATH`

- [ ] **Step 1: Write RED missing/corrupt DB and config tests**

Add to `tests/test_skill_search_service.py`:

```python
import tempfile

from toram_skill_search import SkillUnavailablePayload, run_skill_search


def test_missing_skill_database_returns_unavailable(self):
    payload = run_skill_search(Path("/definitely/missing/skills.sqlite"), "magic finale")
    self.assertIsInstance(payload, SkillUnavailablePayload)
    self.assertIn("unavailable", payload.text.casefold())


def test_corrupt_skill_database_returns_unavailable(self):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "skills.sqlite"
        path.write_bytes(b"not a sqlite database")
        payload = run_skill_search(path, "magic finale")
    self.assertIsInstance(payload, SkillUnavailablePayload)
```

Add to `tests/test_discord_bot.py`:

```python
def test_skill_database_path_defaults_to_canonical_database(self):
    config = load_config({
        "DISCORD_BOT_TOKEN": "token",
        "DISCORD_GUILD_ID": "333",
    })
    self.assertEqual(
        config.skill_database_path,
        (discord_bot.PROJECT_ROOT / "coryn_data/database/skills.sqlite").resolve(),
    )


def test_skill_database_path_can_be_overridden(self):
    config = load_config({
        "DISCORD_BOT_TOKEN": "token",
        "DISCORD_GUILD_ID": "333",
        "SKILL_DATABASE_PATH": "tmp/custom-skills.sqlite",
    })
    self.assertEqual(
        config.skill_database_path,
        (discord_bot.PROJECT_ROOT / "tmp/custom-skills.sqlite").resolve(),
    )
```

- [ ] **Step 2: Run focused tests to verify RED**

Run:

```bash
python -m unittest tests.test_skill_search_service tests.test_discord_bot.DiscordConfigTests -v
```

Expected: FAIL because `run_skill_search()` and `skill_database_path` do not exist.

- [ ] **Step 3: Implement DB-owning application entrypoint**

In `toram_skill_search/service.py`, add:

```python
import sqlite3
from pathlib import Path

from toram_skills.schema import SchemaError


def run_skill_search(
    database_path: Path,
    query: str,
    *,
    repository_factory=SkillRepository,
    semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
) -> SkillPayload:
    repository = None
    try:
        repository = repository_factory(Path(database_path).expanduser().resolve())
        return SkillSearchService(
            repository,
            semantic_runtime=semantic_runtime,
        ).handle(query)
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, SchemaError):
        return SkillUnavailablePayload(
            "Skill search is currently unavailable. Item and stat search are still available."
        )
    finally:
        if repository is not None:
            repository.close()
```

Do not catch arbitrary `Exception` here; unexpected application bugs must still be visible to tests/logging. Export `run_skill_search` from `toram_skill_search/__init__.py`.

- [ ] **Step 4: Add independent skill DB config**

Modify `toram_discord/config.py`:

```python
DEFAULT_SKILL_DATABASE = (PROJECT_ROOT / "coryn_data/database/skills.sqlite").resolve()


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    guild_ids: frozenset[int]
    database_path: Path = core.DEFAULT_DATABASE
    skill_database_path: Path = DEFAULT_SKILL_DATABASE
```

In `load_config()` resolve an optional `SKILL_DATABASE_PATH` relative to `PROJECT_ROOT` when it is not absolute:

```python
skill_path_text = environ.get("SKILL_DATABASE_PATH", "").strip()
if skill_path_text:
    candidate = Path(skill_path_text).expanduser()
    skill_database_path = (
        candidate.resolve()
        if candidate.is_absolute()
        else (PROJECT_ROOT / candidate).resolve()
    )
else:
    skill_database_path = DEFAULT_SKILL_DATABASE
```

Return it in `DiscordBotConfig(...)`.

Update `.env.example` with:

```text
# Optional path to the generated skill database; relative paths are resolved from the project root.
# SKILL_DATABASE_PATH=coryn_data/database/skills.sqlite
```

- [ ] **Step 5: Verify config and DB-isolation tests pass**

Run:

```bash
python -m unittest tests.test_skill_search_service tests.test_discord_bot.DiscordConfigTests -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add toram_skill_search toram_discord/config.py .env.example tests/test_skill_search_service.py tests/test_discord_bot.py
git commit -m "feat: configure skill search database"
```

---

### Task 4: Discord Prefix Routing and Skill Rendering

**Files:**
- Modify: `toram_discord/app.py`
- Modify: `toram_discord/render.py`
- Modify: `toram_discord/views.py`
- Create: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: `parse_skill_command`, `run_skill_search`, skill payload models, `truncate_discord_text`, `_safe_field`, `PAGE_SIZE`.
- Produces:
  - `run_skill_query_sync(database_path: Path, query: str, *, skill_runner=run_skill_search) -> SkillPayload`
  - `build_skill_help_embed(payload: SkillHelpPayload, *, bot_example_prefix: str) -> discord.Embed`
  - `build_skill_results_embed(payload: SkillResultsPayload, page: int) -> discord.Embed`
  - `build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed`
  - `build_skill_payload_message(...) -> tuple[discord.Embed, discord.ui.View | None]`

- [ ] **Step 1: Write RED routing tests proving non-skill isolation**

Create `tests/test_discord_skill_search.py` with light fakes for message/reply and patching at the application boundary. Include these tests:

```python
class DiscordSkillRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_skill_prefix_uses_skill_path_not_item_path(self):
        # Build a fake tagged Discord message with content "<@99> skill magic finale".
        # Patch toram_discord.app.run_skill_query_sync to return SkillHelpPayload("skill help").
        # Patch toram_discord.app.run_query_sync to raise AssertionError if called.
        # Call process_tagged_query(...).
        # Assert run_skill_query_sync receives config.skill_database_path and "magic finale".
        ...

    async def test_non_skill_query_never_calls_skill_path(self):
        # Build content "<@99> hp armor".
        # Patch run_skill_query_sync to raise AssertionError if called.
        # Patch run_query_sync to return ServiceOutcome("help", text="item help").
        # Assert the item path is used.
        ...
```

Implement the fake message concretely with `SimpleNamespace` plus an async `reply(**kwargs)` recorder; use a real `DiscordSessionManager` and `DiscordBotConfig(token="x", guild_ids=frozenset({10}), ...)`. Do not call Discord network APIs.

Add parser-boundary cases for `skills ...` and `skillful ...` to the non-skill test matrix.

- [ ] **Step 2: Write RED rendering tests**

Construct payloads from canonical `SkillDraft`/`SkillTreeDraft` fixtures or fetch them from the real skill DB. Assert:

```python
def test_skill_detail_omits_empty_fields_and_internal_ids(self):
    embed = build_skill_detail_embed(payload)
    visible = "\n".join([
        embed.title or "",
        embed.description or "",
        *(field.name + "\n" + field.value for field in embed.fields),
    ])
    self.assertIn(payload.skill.name, visible)
    self.assertIn(payload.tree.name, visible)
    self.assertNotIn(payload.skill.id, visible)
    self.assertNotIn("None", visible)


def test_skill_results_hide_scores_and_show_ranked_names(self):
    embed = build_skill_results_embed(payload, page=0)
    visible = (embed.description or "")
    self.assertIn(payload.results[0].skill.name, visible)
    self.assertNotIn("semantic", visible.casefold())
    self.assertNotIn("rrf", visible.casefold())
```

Also test an empty `SkillResultsPayload` renders a deterministic no-results embed with skill examples and no Qwen language.

- [ ] **Step 3: Run RED tests**

Run:

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: FAIL because skill routing/render helpers do not exist.

- [ ] **Step 4: Add the synchronous skill bridge and renderers**

In `toram_discord/views.py`:

```python
def run_skill_query_sync(
    database_path: Path,
    query: str,
    *,
    skill_runner=run_skill_search,
) -> SkillPayload:
    return skill_runner(database_path.resolve(), query)
```

In `toram_discord/render.py`, implement:

```python
def build_skill_help_embed(payload: SkillHelpPayload, *, bot_example_prefix: str) -> discord.Embed:
    return discord.Embed(
        title="Toram Skill Search",
        description=(
            f"Search skills with `{bot_example_prefix} skill <words>`.\n\n"
            f"Examples:\n"
            f"• `{bot_example_prefix} skill magic finale`\n"
            f"• `{bot_example_prefix} skill attack while moving`\n"
            f"• `{bot_example_prefix} skill inflict tumble`"
        ),
    )
```

`build_skill_results_embed()` must use `PAGE_SIZE`, title `Skill search: <query>`, show `Showing X–Y of N`, and for each row render `**<name>** — <tree name>` plus the canonical snippet truncated so the total embed description remains <=4096 characters. When `results` is empty, return title `No skill results` and deterministic examples.

`build_skill_detail_embed()` must:

1. title with canonical skill name;
2. description with canonical tree name;
3. add an `Overview` field only for values that exist (`Tier`, `Required level`, `Skill type`, `MP cost`, `Damage type`, `Element`);
4. add `Range / Timing` only for present cast/hit range/time/count values;
5. add `Ailments`, `Weapon requirements`, and `Weapon restrictions` only when non-empty;
6. add `Description` and `Game description` only when non-empty;
7. add each canonical skill section as its own safe field, using the existing `_safe_field()` 1024-character truncation behavior;
8. never display internal IDs, raw embedding scores, or `raw_text`.

- [ ] **Step 5: Route only explicit skill commands in `process_tagged_query()`**

Modify `toram_discord/app.py` after mention extraction and after `sessions.start_query(...)`:

```python
skill_query = parse_skill_command(query)
if skill_query is not None:
    payload = await asyncio.to_thread(
        run_skill_query_sync,
        config.skill_database_path,
        skill_query,
    )
    if not sessions.is_current(key, session.generation):
        return
    session = sessions.get(key)
    if session is None:
        return
    embed, view = build_skill_payload_message(
        payload,
        bot_example_prefix=bot_example_prefix(message.guild, bot_user),
        sessions=sessions,
        key=key,
        generation=session.generation,
    )
    await message.reply(
        embed=embed,
        view=view,
        mention_author=False,
        allowed_mentions=discord.AllowedMentions.none(),
    )
    return
```

The existing item path below this branch must remain byte-for-byte behaviorally equivalent. In particular, do not call `session.failed_context.clear()` anywhere in the skill branch.

At this task, `build_skill_payload_message()` may return `None` for the view until Task 5 adds interactions. It must map `SkillHelpPayload`, `SkillUnavailablePayload`, `SkillDetailPayload`, and `SkillResultsPayload` to their skill-specific embeds.

- [ ] **Step 6: Run focused Discord tests and commit**

Run:

```bash
python -m unittest tests.test_discord_skill_search tests.test_discord_bot -v
```

Expected: PASS.

Commit:

```bash
git add toram_discord/app.py toram_discord/render.py toram_discord/views.py tests/test_discord_skill_search.py
git commit -m "feat: route and render skill searches"
```

---

### Task 5: Paginated Skill Results and Canonical Detail Interaction

**Files:**
- Modify: `toram_discord/views.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: `SessionBoundView`, `ActionButton`, `ActionSelect`, `PAGE_SIZE`, `SkillResultsPayload`, `SkillDetailPayload`, renderers from Task 4.
- Produces:
  - `SkillResultsView`
  - `SkillDetailView`
  - `build_skill_payload_message()` returns the appropriate skill view for non-empty results.

- [ ] **Step 1: Write RED view construction tests**

Add tests modeled after existing `SearchResultsView` tests:

```python
def test_skill_result_dropdown_uses_indexes_not_skill_ids(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "skill query")
    view = SkillResultsView(
        sessions=sessions,
        key=key,
        generation=session.generation,
        payload=payload_with_six_results,
    )
    self.assertEqual(
        [option.value for option in view.skill_select.options],
        ["0", "1", "2", "3", "4"],
    )
    self.assertNotIn(payload_with_six_results.results[0].skill.id, [
        option.value for option in view.skill_select.options
    ])
```

Add tests proving:

- six results create Next/Previous pagination controls;
- selecting an index yields a detail embed for the corresponding canonical `SkillResultItem.skill`;
- `SkillDetailView` contains a `Back to Results` button when opened from results;
- owner/generation protection is inherited from `SessionBoundView` rather than reimplemented.

- [ ] **Step 2: Run view tests to verify RED**

Run:

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: FAIL because skill views do not exist.

- [ ] **Step 3: Implement `SkillResultsView` with existing session page state**

In `toram_discord/views.py`, follow the existing `SearchResultsView` pagination pattern but keep the payload type specific:

```python
class SkillResultsView(SessionBoundView):
    def __init__(self, *, sessions, key, generation, payload: SkillResultsPayload):
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.payload = payload
        session = sessions.get(key)
        page = session.page if session is not None else 0
        total = len(payload.results)
        max_page = max((total - 1) // PAGE_SIZE, 0)
        page = min(max(page, 0), max_page)
        if session is not None:
            session.page = page
        start = page * PAGE_SIZE
        end = min(start + PAGE_SIZE, total)

        options = [
            discord.SelectOption(
                label=truncate_discord_text(payload.results[index].skill.name, 100),
                value=str(index),
                description=truncate_discord_text(payload.results[index].tree.name, 100),
            )
            for index in range(start, end)
        ]
        self.skill_select = None
        if options:
            self.skill_select = ActionSelect(
                placeholder="Select a skill",
                min_values=1,
                max_values=1,
                options=options,
                handler=self._select_skill,
                row=0,
            )
            self.add_item(self.skill_select)
```

Implement Previous/Next exactly like item pagination, rebuilding `SkillResultsView` and `build_skill_results_embed()` using `session.page`.

- [ ] **Step 4: Implement selection/detail/back without reopening the database**

The result payload already contains canonical `SkillDraft` and `SkillTreeDraft`, so selection does not need a second SQLite query:

```python
async def _select_skill(self, interaction, values: Sequence[str]) -> None:
    if not values or not values[0].isdigit():
        await interaction.response.send_message("Invalid skill selection.", ephemeral=True)
        return
    index = int(values[0])
    if not (0 <= index < len(self.payload.results)):
        await interaction.response.send_message("Invalid skill selection.", ephemeral=True)
        return
    result = self.payload.results[index]
    detail = SkillDetailPayload(result.skill, result.tree)
    session = self.sessions.get(self.key)
    if session is not None:
        session.selected_index = index
    await interaction.response.edit_message(
        embed=build_skill_detail_embed(detail),
        view=SkillDetailView(
            sessions=self.sessions,
            key=self.key,
            generation=self.generation,
            results_payload=self.payload,
        ),
    )
```

Implement `SkillDetailView` with one `Back to Results` button. `_back()` rebuilds `SkillResultsView` from `results_payload` and renders the current `session.page`. This view stores only immutable payload data and never a repository connection.

- [ ] **Step 5: Wire views into `build_skill_payload_message()`**

For `SkillResultsPayload`:

- empty results -> no view;
- non-empty results -> `SkillResultsView`;
- exact `SkillDetailPayload` -> no back button because there is no prior result list;
- help/unavailable -> no view.

- [ ] **Step 6: Run interaction and existing follow-up regression tests**

Run:

```bash
python -m unittest \
  tests.test_discord_skill_search \
  tests.test_discord_followup_regressions \
  tests.test_discord_bot -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add toram_discord/views.py tests/test_discord_skill_search.py
git commit -m "feat: add interactive skill results"
```

---

### Task 6: Compatibility Exports, Module Boundaries, and Full Acceptance

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_module_boundaries.py`
- Modify: `tests/test_discord_skill_search.py`
- Modify: `tests/test_discord_bot.py`

**Interfaces:**
- Consumes: all public Discord helpers introduced in Tasks 3-5.
- Produces: compatibility facade re-exports and final acceptance evidence.

- [ ] **Step 1: Write RED compatibility and isolation assertions**

Extend `tests/test_discord_module_boundaries.py` to assert canonical ownership for the new helpers:

```python
from toram_discord.render import (
    build_skill_detail_embed,
    build_skill_help_embed,
    build_skill_results_embed,
)
from toram_discord.views import (
    SkillDetailView,
    SkillResultsView,
    build_skill_payload_message,
    run_skill_query_sync,
)

self.assertIs(discord_bot.build_skill_detail_embed, build_skill_detail_embed)
self.assertIs(discord_bot.build_skill_help_embed, build_skill_help_embed)
self.assertIs(discord_bot.build_skill_results_embed, build_skill_results_embed)
self.assertIs(discord_bot.SkillDetailView, SkillDetailView)
self.assertIs(discord_bot.SkillResultsView, SkillResultsView)
self.assertIs(discord_bot.build_skill_payload_message, build_skill_payload_message)
self.assertIs(discord_bot.run_skill_query_sync, run_skill_query_sync)
```

Add an AST boundary test:

```python
def test_skill_application_layer_does_not_import_discord(self):
    for path in sorted((ROOT / "toram_skill_search").glob("*.py")):
        modules = imported_modules(path)
        forbidden = {m for m in modules if m == "discord" or m.startswith("toram_discord")}
        self.assertFalse(forbidden, f"{path} imports {sorted(forbidden)}")
```

Add a request-isolation regression test in `tests/test_discord_skill_search.py` that patches `toram_discord.app.run_skill_query_sync` to raise if called, sends a normal item query, and verifies the item outcome still renders. Add a `FailedQueryContext` regression that seeds one failed item attempt, executes a skill request through the Discord route, and asserts the same attempt remains afterward with no new skill attempt and no clearing.

- [ ] **Step 2: Run boundary tests to verify RED**

Run:

```bash
python -m unittest tests.test_discord_module_boundaries tests.test_discord_skill_search -v
```

Expected: FAIL until the compatibility facade exports the new symbols and any missing isolation assertions are satisfied.

- [ ] **Step 3: Re-export new Discord symbols from the compatibility facade**

Modify `discord_bot.py` only by extending its existing imports; do not define functions/classes there. Re-export:

```python
from toram_discord.render import (
    build_skill_detail_embed,
    build_skill_help_embed,
    build_skill_results_embed,
)
from toram_discord.views import (
    SkillDetailView,
    SkillResultsView,
    build_skill_payload_message,
    run_skill_query_sync,
)
```

Preserve the facade invariant: import-only plus `if __name__ == "__main__": main()`.

- [ ] **Step 4: Run all focused skill integration tests**

Run:

```bash
python -m unittest \
  tests.test_skill_search_service \
  tests.test_skill_hybrid_search \
  tests.test_discord_skill_search \
  tests.test_discord_bot \
  tests.test_discord_followup_regressions \
  tests.test_discord_module_boundaries -v
```

Expected: PASS.

- [ ] **Step 5: Verify the core module-boundary suite**

Run:

```bash
python -m unittest tests.test_core_module_boundaries tests.test_discord_module_boundaries -v
```

Expected: PASS, proving `toram_skills` remains frontend-free and the new `toram_skill_search` layer is Discord-free.

- [ ] **Step 6: Run the complete repository test suite**

Run:

```bash
python -m unittest discover -s tests -v
```

Expected: PASS with no regressions in item/stat/help/database/Qwen behavior.

- [ ] **Step 7: Perform a deterministic manual smoke check without Discord network access**

Run:

```bash
python - <<'PY'
from pathlib import Path
from toram_skill_search import run_skill_search

path = Path("coryn_data/database/skills.sqlite")
for query in ("", "magic: finale", "attack while moving", "inflict tumble"):
    payload = run_skill_search(path, query, semantic_runtime=None)
    print(query or "<help>", type(payload).__name__)
    if hasattr(payload, "results"):
        print([item.skill.name for item in payload.results[:3]])
    elif hasattr(payload, "skill"):
        print(payload.skill.name)
PY
```

Expected:

- empty query -> `SkillHelpPayload`;
- `magic: finale` -> `SkillDetailPayload` without semantic runtime;
- free-text queries -> `SkillResultsPayload` using lexical mode in this smoke check.

This smoke check intentionally passes `semantic_runtime=None`; real MiniLM behavior is already covered by the semantic runtime/unit tests and the existing benchmarked retrieval suite, avoiding an unnecessary model download during every acceptance run.

- [ ] **Step 8: Commit final compatibility/acceptance changes**

```bash
git add discord_bot.py tests/test_discord_module_boundaries.py tests/test_discord_skill_search.py tests/test_discord_bot.py
git commit -m "test: finalize skill bot integration"
```

---

## Acceptance Checklist

Before declaring the branch complete, verify all of these against fresh test output:

- `skill` returns deterministic help.
- `skill <exact canonical or alias>` returns canonical detail before semantic initialization.
- multiple exact matches produce a result list, never a guessed detail.
- free-text skill search uses the benchmark-selected hybrid configuration when semantic runtime is available.
- semantic provider/index/query failure falls back to lexical search.
- no similarity threshold was added.
- missing/corrupt skill DB returns a skill-specific unavailable payload.
- skill result pagination and selection open canonical detail and support Back to Results.
- skill searches do not invoke Qwen and do not mutate/clear `FailedQueryContext`.
- non-skill messages never execute the skill search path or open `skills.sqlite`.
- normal item/stat behavior remains unchanged.
- `toram_skill_search` imports no Discord frontend code.
- full `python -m unittest discover -s tests -v` passes.
