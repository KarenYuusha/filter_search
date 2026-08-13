# Skill Bot Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add explicit `skill ...` Discord search that returns canonical skill details or ranked free-text skill matches without changing the existing item/stat/help/database/Qwen flow.

**Architecture:** Add a frontend-neutral `toram_skill_search` application layer above `toram_skills`, with deterministic command parsing, canonical payloads, and a lazy cached MiniLM semantic runtime. The Discord layer intercepts only token-bounded `skill` commands, runs the skill application in `asyncio.to_thread(...)`, and renders skill-specific embeds/views; every non-skill query continues through the existing `SearchService` unchanged.

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
    service.py           # command parser + DB-backed skill application service

tests/
    test_skill_search_service.py
    test_discord_skill_search.py
```

Modify:

```text
toram_discord/config.py
toram_discord/app.py
toram_discord/render.py
toram_discord/views.py
discord_bot.py
.env.example
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

Create `tests/test_skill_search_service.py`:

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
        self.assertEqual(
            parse_skill_command("  SKILL   attack while moving  "),
            "attack while moving",
        )
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
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("")
        self.assertEqual(type(payload).__name__, "SkillHelpPayload")
        self.assertIn("skill magic finale", payload.text.casefold())

    def test_exact_canonical_name_returns_detail_without_semantic_runtime(self):
        payload = SkillSearchService(
            self.repo,
            semantic_runtime=ExplodingSemanticRuntime(),
        ).handle("magic: finale")
        self.assertEqual(type(payload).__name__, "SkillDetailPayload")
        self.assertEqual(
            payload.skill.id,
            "weapon_class_skills/magic_skills/magic-finale",
        )
        self.assertEqual(payload.tree.id, payload.skill.tree_id)
```

Use `SkillRepository.resolve_skill_name()` against the canonical DB to identify one existing alias and add a test proving that alias returns `SkillDetailPayload` without semantic initialization. For the multiple-exact case, define a `FakeRepository` whose `resolve_skill_name()` returns two `SkillDraft` values and whose `get_tree()` returns their trees; assert the service returns both in original repository order as `SkillResultsPayload`.

- [ ] **Step 2: Run focused tests to verify RED**

```bash
python -m unittest tests.test_skill_search_service.SkillCommandParserTests tests.test_skill_search_service.SkillSearchServiceTests -v
```

Expected: FAIL because `toram_skill_search` does not exist yet.

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


SkillPayload = (
    SkillDetailPayload
    | SkillResultsPayload
    | SkillHelpPayload
    | SkillUnavailablePayload
)
```

Create `toram_skill_search/__init__.py` that re-exports these models and the parser/service names introduced below.

- [ ] **Step 4: Implement deterministic parser, snippets, and exact-first service behavior**

Create `toram_skill_search/service.py`:

```python
from __future__ import annotations

from toram_skill_search.models import (
    SkillDetailPayload,
    SkillHelpPayload,
    SkillPayload,
    SkillResultItem,
    SkillResultsPayload,
)
from toram_skills.hybrid_search import HybridSkillSearcher
from toram_skills.models import SkillDraft
from toram_skills.repository import SkillRepository
from toram_skills.retrieval_config import DEFAULT_FUSION_CONFIG


def parse_skill_command(query: str) -> str | None:
    parts = str(query).strip().split(maxsplit=1)
    if not parts or parts[0].casefold() != "skill":
        return None
    if len(parts) == 1:
        return ""
    return " ".join(parts[1].split())


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


class SkillSearchService:
    def __init__(self, repository: SkillRepository, *, semantic_runtime=None) -> None:
        self.repository = repository
        self.semantic_runtime = semantic_runtime

    def handle(self, query: str) -> SkillPayload:
        cleaned = " ".join(str(query).split())
        if not cleaned:
            return SkillHelpPayload(
                "Search Toram skills with `skill <words>`. Examples: "
                "`skill magic finale`, `skill attack while moving`, "
                "`skill inflict tumble`."
            )

        exact = self.repository.resolve_skill_name(cleaned)
        if len(exact) == 1:
            skill = exact[0]
            return SkillDetailPayload(
                skill,
                self.repository.get_tree(skill.tree_id),
            )
        if len(exact) > 1:
            return SkillResultsPayload(
                cleaned,
                tuple(_result_item(self.repository, skill) for skill in exact),
            )
        return self._search_free_text(cleaned)

    def _search_free_text(self, query: str) -> SkillResultsPayload:
        hits = HybridSkillSearcher(
            self.repository,
            semantic_index=None,
            fusion_config=DEFAULT_FUSION_CONFIG,
        ).search(query, limit=20)
        return SkillResultsPayload(
            query,
            tuple(
                _result_item(
                    self.repository,
                    self.repository.get_skill(hit.skill_id),
                )
                for hit in hits
            ),
        )
```

Task 1 intentionally uses lexical-only `HybridSkillSearcher(..., semantic_index=None)` for non-exact queries. Task 2 replaces only `_search_free_text()` and the constructor default; exact-first behavior remains unchanged.

- [ ] **Step 5: Add lexical free-text tests**

Append:

```python
def test_free_text_returns_canonical_ranked_results(self):
    payload = SkillSearchService(self.repo).handle("inflict tumble")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
    self.assertTrue(payload.results)
    self.assertTrue(
        all(item.skill.tree_id == item.tree.id for item in payload.results)
    )
    self.assertTrue(all(item.snippet for item in payload.results))


def test_free_text_accepts_filter_like_words_as_plain_text(self):
    payload = SkillSearchService(self.repo).handle("sword tier 4")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
```

Do not assert structured-filter behavior in the second test.

- [ ] **Step 6: Run focused tests and commit**

```bash
python -m unittest tests.test_skill_search_service -v
```

Expected: PASS.

```bash
git add toram_skill_search tests/test_skill_search_service.py
git commit -m "feat: add skill search application core"
```

---

### Task 2: Lazy MiniLM Runtime With Lexical Degradation

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

- [ ] **Step 1: Write RED runtime/fallback tests**

Add to `tests/test_skill_search_service.py`:

```python
from concurrent.futures import ThreadPoolExecutor

from toram_skill_search.runtime import SemanticRuntimeCache
from toram_skills.search_models import ChannelScore, SkillSearchHit
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


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


def test_non_exact_query_requests_semantic_runtime(self):
    runtime = FixedRuntime(None)
    SkillSearchService(
        self.repo,
        semantic_runtime=runtime,
    ).handle("attack while moving")
    self.assertEqual(runtime.calls, 1)


def test_semantic_query_failure_retries_lexical_only(self):
    semantic = FakeSemanticIndex(error=EmbeddingUnavailable("offline"))
    payload = SkillSearchService(
        self.repo,
        semantic_runtime=FixedRuntime(semantic),
    ).handle("inflict tumble")
    self.assertEqual(type(payload).__name__, "SkillResultsPayload")
    self.assertTrue(payload.results)
```

- [ ] **Step 2: Run focused tests to verify RED**

```bash
python -m unittest tests.test_skill_search_service -v
```

Expected: FAIL because the runtime module and semantic-aware free-text path do not exist.

- [ ] **Step 3: Implement a thread-safe connection-free semantic runtime cache**

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
        self._indexes = {}

    def _make_provider(self):
        if self._provider_factory is not None:
            return self._provider_factory()
        from toram_skill_embeddings.providers import (
            EmbeddingProviderSpec,
            build_provider,
        )
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
                        raise EmbeddingIndexError(
                            "Configured embedding runtime ID does not match retrieval config"
                        )
                    self._provider = provider
                index = self._index_factory(repository, provider)
                self._indexes[key] = index
                return index
        except (EmbeddingUnavailable, EmbeddingIndexError, ImportError):
            return None


DEFAULT_SEMANTIC_RUNTIME = SemanticRuntimeCache()
```

The cache key includes the resolved database path and search-document manifest. The cache stores only the provider and `SemanticSkillIndex`, whose vectors/doc IDs are loaded into Python tuples; it must never store `SkillRepository` or its SQLite connection.

- [ ] **Step 4: Upgrade free-text search to hybrid and retry lexical on semantic query failure**

Modify imports and constructor in `toram_skill_search/service.py`:

```python
from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
from toram_skills.semantic_search import EmbeddingIndexError, EmbeddingUnavailable


class SkillSearchService:
    def __init__(
        self,
        repository: SkillRepository,
        *,
        semantic_runtime=DEFAULT_SEMANTIC_RUNTIME,
    ) -> None:
        self.repository = repository
        self.semantic_runtime = semantic_runtime
```

Replace `_search_free_text()` with:

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
            _result_item(
                self.repository,
                self.repository.get_skill(hit.skill_id),
            )
            for hit in hits
        ),
    )
```

Explicit `semantic_runtime=None` remains the lexical-only test/smoke seam.

- [ ] **Step 5: Add cache-concurrency and stale-index retry tests**

Append:

```python
def test_runtime_cache_builds_one_index_for_same_database_manifest(self):
    build_calls = []
    sentinel = FakeSemanticIndex()

    class Provider:
        provider_name = "sentence-transformers"
        model_name = "sentence-transformers/all-MiniLM-L6-v2"
        config_id = "symmetric-encode-v1"

    runtime = SemanticRuntimeCache(
        provider_factory=lambda: Provider(),
        index_factory=lambda repository, provider: (
            build_calls.append(repository.database_path) or sentinel
        ),
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        values = list(
            executor.map(lambda unused: runtime.get_index(self.repo), range(4))
        )
    self.assertTrue(all(value is sentinel for value in values))
    self.assertEqual(len(build_calls), 1)


def test_stale_index_failure_does_not_break_later_exact_lookup(self):
    runtime = SemanticRuntimeCache(
        provider_factory=lambda: type(
            "Provider",
            (),
            {"config_id": "symmetric-encode-v1"},
        )(),
        index_factory=lambda repository, provider: (_ for _ in ()).throw(
            EmbeddingIndexError("stale")
        ),
    )
    free_text = SkillSearchService(
        self.repo,
        semantic_runtime=runtime,
    ).handle("inflict tumble")
    exact = SkillSearchService(
        self.repo,
        semantic_runtime=runtime,
    ).handle("magic: finale")
    self.assertEqual(type(free_text).__name__, "SkillResultsPayload")
    self.assertEqual(type(exact).__name__, "SkillDetailPayload")
```

- [ ] **Step 6: Run focused retrieval tests and commit**

```bash
python -m unittest tests.test_skill_search_service tests.test_skill_hybrid_search -v
```

Expected: PASS.

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
- Produces:
  - `run_skill_search(database_path: Path, query: str, *, repository_factory=SkillRepository, semantic_runtime=DEFAULT_SEMANTIC_RUNTIME) -> SkillPayload`
  - `DiscordBotConfig.skill_database_path: Path`
  - optional environment variable `SKILL_DATABASE_PATH`

- [ ] **Step 1: Write RED DB/config tests**

Add to `tests/test_skill_search_service.py`:

```python
import tempfile

from toram_skill_search import SkillUnavailablePayload, run_skill_search


def test_missing_skill_database_returns_unavailable(self):
    payload = run_skill_search(
        Path("/definitely/missing/skills.sqlite"),
        "magic finale",
    )
    self.assertIsInstance(payload, SkillUnavailablePayload)
    self.assertIn("unavailable", payload.text.casefold())


def test_corrupt_skill_database_returns_unavailable(self):
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "skills.sqlite"
        path.write_bytes(b"not a sqlite database")
        payload = run_skill_search(path, "magic finale")
    self.assertIsInstance(payload, SkillUnavailablePayload)
```

Add to `DiscordConfigTests` in `tests/test_discord_bot.py`:

```python
def test_skill_database_path_defaults_to_canonical_database(self):
    config = load_config({
        "DISCORD_BOT_TOKEN": "token",
        "DISCORD_GUILD_ID": "333",
    })
    self.assertEqual(
        config.skill_database_path,
        (
            discord_bot.PROJECT_ROOT
            / "coryn_data/database/skills.sqlite"
        ).resolve(),
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

Extend `test_env_example_has_only_safe_template_values()` to assert the example contains `SKILL_DATABASE_PATH` only as a safe commented/default path and contains no local absolute path.

- [ ] **Step 2: Run focused tests to verify RED**

```bash
python -m unittest tests.test_skill_search_service tests.test_discord_bot.DiscordConfigTests -v
```

Expected: FAIL because `run_skill_search()` and `skill_database_path` do not exist.

- [ ] **Step 3: Implement the DB-owning application entrypoint**

In `toram_skill_search/service.py` add:

```python
import sqlite3
from pathlib import Path

from toram_skill_search.models import SkillUnavailablePayload
from toram_skill_search.runtime import DEFAULT_SEMANTIC_RUNTIME
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
        repository = repository_factory(
            Path(database_path).expanduser().resolve()
        )
        return SkillSearchService(
            repository,
            semantic_runtime=semantic_runtime,
        ).handle(query)
    except (FileNotFoundError, OSError, sqlite3.DatabaseError, SchemaError):
        return SkillUnavailablePayload(
            "Skill search is currently unavailable. "
            "Item and stat search are still available."
        )
    finally:
        if repository is not None:
            repository.close()
```

Do not catch arbitrary `Exception`; unexpected application bugs must still reach tests/logging. Export `run_skill_search` from `toram_skill_search/__init__.py`.

- [ ] **Step 4: Add independent skill DB configuration**

Modify `toram_discord/config.py`:

```python
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILL_DATABASE = (
    PROJECT_ROOT / "coryn_data/database/skills.sqlite"
).resolve()


@dataclass(frozen=True)
class DiscordBotConfig:
    token: str
    guild_ids: frozenset[int]
    database_path: Path = core.DEFAULT_DATABASE
    skill_database_path: Path = DEFAULT_SKILL_DATABASE
```

Inside `load_config()` after guild validation:

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

return DiscordBotConfig(
    token=token,
    guild_ids=guild_ids,
    skill_database_path=skill_database_path,
)
```

Update `.env.example`:

```text
# Optional generated skill database path; relative paths use the project root.
# SKILL_DATABASE_PATH=coryn_data/database/skills.sqlite
```

- [ ] **Step 5: Run DB/config tests and commit**

```bash
python -m unittest tests.test_skill_search_service tests.test_discord_bot.DiscordConfigTests -v
```

Expected: PASS.

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
- Consumes: `parse_skill_command`, `run_skill_search`, skill payload models, `PAGE_SIZE`, `truncate_discord_text`, `_safe_field`.
- Produces:
  - `run_skill_query_sync(database_path: Path, query: str, *, skill_runner=run_skill_search) -> SkillPayload`
  - `build_skill_help_embed(payload: SkillHelpPayload, *, bot_example_prefix: str) -> discord.Embed`
  - `build_skill_results_embed(payload: SkillResultsPayload, page: int) -> discord.Embed`
  - `build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed`
  - `build_skill_payload_message(...) -> tuple[discord.Embed, discord.ui.View | None]`

- [ ] **Step 1: Write RED explicit-routing tests with an executable fake Discord message**

Create `tests/test_discord_skill_search.py`:

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from toram_discord.app import process_tagged_query
from toram_discord.config import DiscordBotConfig
from toram_discord.sessions import DiscordSessionManager
from toram_search.service import ServiceOutcome
from toram_skill_search.models import SkillHelpPayload


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.guild = SimpleNamespace(id=10, get_member=lambda user_id: None)
        self.channel = SimpleNamespace(id=30)
        self.author = SimpleNamespace(id=20, bot=False)
        self.mentions = [SimpleNamespace(id=99)]
        self.webhook_id = None
        self.replies = []

    async def reply(self, **kwargs) -> None:
        self.replies.append(kwargs)


class DiscordSkillRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot_user = SimpleNamespace(
            id=99,
            display_name="Toram Search",
            name="Toram Search",
        )
        self.config = DiscordBotConfig(
            token="x",
            guild_ids=frozenset({10}),
            database_path=Path("items.sqlite"),
            skill_database_path=Path("skills.sqlite"),
        )
        self.sessions = DiscordSessionManager()

    async def test_explicit_skill_prefix_uses_skill_path_not_item_path(self):
        message = FakeMessage("<@99> skill magic finale")
        calls = []

        def fake_skill_runner(database_path, query):
            calls.append((database_path, query))
            return SkillHelpPayload("skill help")

        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=fake_skill_runner,
        ), patch(
            "toram_discord.app.run_query_sync",
            side_effect=AssertionError("item path must not run"),
        ):
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=self.sessions,
            )

        self.assertEqual(calls, [(Path("skills.sqlite"), "magic finale")])
        self.assertEqual(len(message.replies), 1)

    async def test_non_skill_query_never_calls_skill_path(self):
        message = FakeMessage("<@99> hp armor")
        with patch(
            "toram_discord.app.run_skill_query_sync",
            side_effect=AssertionError("skill path must not run"),
        ), patch(
            "toram_discord.app.run_query_sync",
            return_value=ServiceOutcome("help", text="item help"),
        ) as item_runner:
            await process_tagged_query(
                message,
                bot_user=self.bot_user,
                config=self.config,
                sessions=self.sessions,
            )

        item_runner.assert_called_once()
        self.assertEqual(len(message.replies), 1)

    async def test_non_token_prefixes_remain_item_queries(self):
        for query in ("skills magic finale", "skillful magic finale"):
            with self.subTest(query=query):
                message = FakeMessage(f"<@99> {query}")
                with patch(
                    "toram_discord.app.run_skill_query_sync",
                    side_effect=AssertionError("skill path must not run"),
                ), patch(
                    "toram_discord.app.run_query_sync",
                    return_value=ServiceOutcome("help", text="item help"),
                ) as item_runner:
                    await process_tagged_query(
                        message,
                        bot_user=self.bot_user,
                        config=self.config,
                        sessions=DiscordSessionManager(),
                    )
                item_runner.assert_called_once()
```

- [ ] **Step 2: Write RED rendering tests**

Add a helper that loads `magic: finale` from the canonical DB and constructs `SkillDetailPayload` and `SkillResultsPayload`. Then add:

```python
def test_skill_detail_omits_internal_ids_and_none_text(self):
    embed = build_skill_detail_embed(self.detail_payload)
    visible = "\n".join([
        embed.title or "",
        embed.description or "",
        *(field.name + "\n" + field.value for field in embed.fields),
    ])
    self.assertIn(self.detail_payload.skill.name, visible)
    self.assertIn(self.detail_payload.tree.name, visible)
    self.assertNotIn(self.detail_payload.skill.id, visible)
    self.assertNotIn("None", visible)


def test_skill_results_hide_retrieval_diagnostics(self):
    embed = build_skill_results_embed(self.results_payload, page=0)
    visible = embed.description or ""
    self.assertIn(self.results_payload.results[0].skill.name, visible)
    self.assertNotIn("semantic", visible.casefold())
    self.assertNotIn("rrf", visible.casefold())
```

Construct `SkillResultsPayload("nope", ())` and assert title `No skill results`, deterministic skill examples, and no Qwen/fallback wording.

- [ ] **Step 3: Run routing/render tests to verify RED**

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: FAIL because the skill Discord bridge/renderers do not exist.

- [ ] **Step 4: Add the synchronous skill bridge and skill renderers**

In `toram_discord/views.py` add imports for the skill models/application and:

```python
def run_skill_query_sync(
    database_path: Path,
    query: str,
    *,
    skill_runner=run_skill_search,
) -> SkillPayload:
    return skill_runner(database_path.resolve(), query)
```

In `toram_discord/render.py` add skill model imports and:

```python
def build_skill_help_embed(
    payload: SkillHelpPayload,
    *,
    bot_example_prefix: str,
) -> discord.Embed:
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

Implement `build_skill_results_embed(payload, page)` with the same page math as `build_search_results_embed`: `PAGE_SIZE=5`, clamped page, `Showing X–Y of N`, and each row formatted as `**<skill name>** — <tree name>` followed by a truncated canonical snippet. Clamp the final description with `truncate_discord_text(..., 4096)`. When there are no results, return title `No skill results` and deterministic examples.

Implement `build_skill_detail_embed(payload)` with this field policy:

```python
def build_skill_detail_embed(payload: SkillDetailPayload) -> discord.Embed:
    skill = payload.skill
    embed = discord.Embed(
        title=truncate_discord_text(skill.name, 256),
        description=truncate_discord_text(payload.tree.name, 4096),
    )
    overview = []
    for label, value in (
        ("Tier", skill.tier),
        ("Required level", skill.required_level),
        ("Skill type", skill.skill_type),
        ("MP cost", skill.mp_cost_text),
        ("Damage type", skill.damage_type),
        ("Element", skill.element),
    ):
        if value is not None and str(value).strip():
            overview.append(f"{label}: {value}")
    if overview:
        _safe_field(embed, "Overview", "\n".join(overview))
```

Continue the function with a `Range / Timing` field only for present cast/hit range/time/count values; optional `Ailments`, `Weapon requirements`, `Weapon restrictions`, `Description`, and `Game description` fields; then one `_safe_field(embed, section.label, section.body)` per non-empty canonical section. Never display `skill.id`, `tree.id`, `raw_text`, embedding scores, or channels.

- [ ] **Step 5: Add a skill payload message dispatcher without interactions yet**

In `toram_discord/views.py`:

```python
def build_skill_payload_message(
    payload: SkillPayload,
    *,
    bot_example_prefix: str,
    sessions: DiscordSessionManager,
    key: SessionKey,
    generation: int,
) -> tuple[discord.Embed, discord.ui.View | None]:
    if isinstance(payload, SkillHelpPayload):
        return build_skill_help_embed(
            payload,
            bot_example_prefix=bot_example_prefix,
        ), None
    if isinstance(payload, SkillUnavailablePayload):
        return _build_text_embed("Skill search unavailable", payload.text), None
    if isinstance(payload, SkillDetailPayload):
        return build_skill_detail_embed(payload), None
    return build_skill_results_embed(
        payload,
        sessions.get(key).page if sessions.get(key) is not None else 0,
    ), None
```

Task 5 upgrades the non-empty result branch to return `SkillResultsView`.

- [ ] **Step 6: Route only explicit skill commands in `process_tagged_query()`**

Add imports in `toram_discord/app.py` for `parse_skill_command`, `build_skill_payload_message`, and `run_skill_query_sync`. After mention extraction and `sessions.start_query(...)`, insert:

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

Leave the existing item path below this branch behaviorally unchanged. Do not call `session.failed_context.clear()` in the skill branch.

- [ ] **Step 7: Run focused Discord tests and commit**

```bash
python -m unittest tests.test_discord_skill_search tests.test_discord_bot -v
```

Expected: PASS.

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
- Produces: `SkillResultsView`, `SkillDetailView`, interactive `build_skill_payload_message()` for non-empty result payloads.

- [ ] **Step 1: Write RED result-view tests**

Build a six-result payload by taking six canonical skills from `SkillRepository` and wrapping them in `SkillResultItem`. Add:

```python
def test_skill_result_dropdown_uses_indexes_not_skill_ids(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "skill query")
    view = SkillResultsView(
        sessions=sessions,
        key=key,
        generation=session.generation,
        payload=self.six_result_payload,
    )
    self.assertEqual(
        [option.value for option in view.skill_select.options],
        ["0", "1", "2", "3", "4"],
    )
    skill_ids = {item.skill.id for item in self.six_result_payload.results}
    self.assertTrue(
        skill_ids.isdisjoint(
            {option.value for option in view.skill_select.options}
        )
    )


def test_six_skill_results_have_pagination_controls(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    session = sessions.start_query(key, "skill query")
    view = SkillResultsView(
        sessions=sessions,
        key=key,
        generation=session.generation,
        payload=self.six_result_payload,
    )
    labels = [child.label for child in view.children if hasattr(child, "label")]
    self.assertIn("Previous", labels)
    self.assertIn("Next", labels)
```

Also instantiate `SkillDetailView` with a result payload and assert it contains `Back to Results`. Reuse `SessionBoundView.interaction_check` tests already present for owner/generation protection; do not duplicate that logic in skill views.

- [ ] **Step 2: Run view tests to verify RED**

```bash
python -m unittest tests.test_discord_skill_search -v
```

Expected: FAIL because the skill views do not exist.

- [ ] **Step 3: Implement `SkillResultsView` with existing session page state**

Add to `toram_discord/views.py`:

```python
class SkillResultsView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        payload: SkillResultsPayload,
    ) -> None:
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
                label=truncate_discord_text(
                    payload.results[index].skill.name,
                    100,
                ),
                value=str(index),
                description=truncate_discord_text(
                    payload.results[index].tree.name,
                    100,
                ),
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

Add Previous/Next buttons when `total > PAGE_SIZE`, copying only the page-clamping mechanics from `SearchResultsView`. `_previous()` and `_next()` must rebuild `SkillResultsView` and call `build_skill_results_embed(self.payload, session.page)`.

- [ ] **Step 4: Implement selection and detail/back navigation without reopening SQLite**

Use the canonical records already present in `SkillResultItem`:

```python
async def _select_skill(
    self,
    interaction: discord.Interaction,
    values: Sequence[str],
) -> None:
    if not values or not values[0].isdigit():
        await interaction.response.send_message(
            "Invalid skill selection.",
            ephemeral=True,
        )
        return
    index = int(values[0])
    if not (0 <= index < len(self.payload.results)):
        await interaction.response.send_message(
            "Invalid skill selection.",
            ephemeral=True,
        )
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

Add:

```python
class SkillDetailView(SessionBoundView):
    def __init__(
        self,
        *,
        sessions: DiscordSessionManager,
        key: SessionKey,
        generation: int,
        results_payload: SkillResultsPayload,
    ) -> None:
        super().__init__(
            sessions=sessions,
            key=key,
            generation=generation,
            owner_id=key[2],
        )
        self.results_payload = results_payload
        self.add_item(
            ActionButton(
                label="Back to Results",
                style=discord.ButtonStyle.primary,
                handler=self._back,
            )
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        session = self.sessions.get(self.key)
        if session is None:
            return
        session.selected_index = None
        await interaction.response.edit_message(
            embed=build_skill_results_embed(
                self.results_payload,
                session.page,
            ),
            view=SkillResultsView(
                sessions=self.sessions,
                key=self.key,
                generation=self.generation,
                payload=self.results_payload,
            ),
        )
```

These views retain only immutable payload data and session identifiers, never a `SkillRepository` or SQLite connection.

- [ ] **Step 5: Wire non-empty results into `build_skill_payload_message()`**

Replace only the `SkillResultsPayload` branch:

```python
if isinstance(payload, SkillResultsPayload):
    page = sessions.get(key).page if sessions.get(key) is not None else 0
    view = None
    if payload.results:
        view = SkillResultsView(
            sessions=sessions,
            key=key,
            generation=generation,
            payload=payload,
        )
    return build_skill_results_embed(payload, page), view
```

Exact `SkillDetailPayload` remains a detail embed with no Back button because there is no prior results list.

- [ ] **Step 6: Run interaction regressions and commit**

```bash
python -m unittest tests.test_discord_skill_search tests.test_discord_followup_regressions tests.test_discord_bot -v
```

Expected: PASS.

```bash
git add toram_discord/views.py tests/test_discord_skill_search.py
git commit -m "feat: add interactive skill results"
```

---

### Task 6: Compatibility Exports, Isolation Assertions, and Full Acceptance

**Files:**
- Modify: `discord_bot.py`
- Modify: `tests/test_discord_module_boundaries.py`
- Modify: `tests/test_discord_skill_search.py`

**Interfaces:**
- Consumes: all public Discord helpers introduced in Tasks 3-5.
- Produces: compatibility facade re-exports and final acceptance evidence.

- [ ] **Step 1: Write RED compatibility/module-boundary assertions**

Extend `tests/test_discord_module_boundaries.py`:

```python
def test_skill_render_symbols_have_canonical_package_owner(self):
    from toram_discord.render import (
        build_skill_detail_embed,
        build_skill_help_embed,
        build_skill_results_embed,
    )

    self.assertIs(discord_bot.build_skill_detail_embed, build_skill_detail_embed)
    self.assertIs(discord_bot.build_skill_help_embed, build_skill_help_embed)
    self.assertIs(discord_bot.build_skill_results_embed, build_skill_results_embed)


def test_skill_view_symbols_have_canonical_package_owner(self):
    from toram_discord.views import (
        SkillDetailView,
        SkillResultsView,
        build_skill_payload_message,
        run_skill_query_sync,
    )

    self.assertIs(discord_bot.SkillDetailView, SkillDetailView)
    self.assertIs(discord_bot.SkillResultsView, SkillResultsView)
    self.assertIs(discord_bot.build_skill_payload_message, build_skill_payload_message)
    self.assertIs(discord_bot.run_skill_query_sync, run_skill_query_sync)


def test_skill_application_layer_does_not_import_discord(self):
    for path in sorted((ROOT / "toram_skill_search").glob("*.py")):
        modules = imported_modules(path)
        forbidden = {
            module
            for module in modules
            if module == "discord" or module.startswith("toram_discord")
        }
        self.assertFalse(forbidden, f"{path} imports {sorted(forbidden)}")
```

- [ ] **Step 2: Add RED request/failure-context isolation tests**

Append to `tests/test_discord_skill_search.py`:

```python
async def test_skill_query_preserves_existing_failed_item_context(self):
    sessions = DiscordSessionManager()
    key = (10, 30, 20)
    previous = sessions.start_query(key, "bad item query")
    previous.failed_context.record_failure("bad item query")
    message = FakeMessage("<@99> skill magic finale")

    with patch(
        "toram_discord.app.run_skill_query_sync",
        return_value=SkillHelpPayload("skill help"),
    ), patch(
        "toram_discord.app.run_query_sync",
        side_effect=AssertionError("item path must not run"),
    ):
        await process_tagged_query(
            message,
            bot_user=self.bot_user,
            config=self.config,
            sessions=sessions,
        )

    current = sessions.get(key)
    attempts = current.failed_context.snapshot()
    self.assertEqual(
        [attempt.original_query for attempt in attempts],
        ["bad item query"],
    )


async def test_item_query_executes_without_opening_skill_path(self):
    message = FakeMessage("<@99> cr bow")
    with patch(
        "toram_discord.app.run_skill_query_sync",
        side_effect=AssertionError("skill path must not run"),
    ), patch(
        "toram_discord.app.run_query_sync",
        return_value=ServiceOutcome("help", text="item path"),
    ) as item_runner:
        await process_tagged_query(
            message,
            bot_user=self.bot_user,
            config=self.config,
            sessions=DiscordSessionManager(),
        )
    item_runner.assert_called_once()
```

These tests prove request execution never reaches the skill runner for normal item queries; combined with Task 2's lazy provider import, normal requests cannot initialize Sentence Transformers.

- [ ] **Step 3: Run boundary/isolation tests to verify RED**

```bash
python -m unittest tests.test_discord_module_boundaries tests.test_discord_skill_search -v
```

Expected: FAIL until the compatibility facade exports the new symbols.

- [ ] **Step 4: Re-export new Discord symbols from `discord_bot.py`**

Extend only the existing import lists:

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

Keep `discord_bot.py` import-only plus its existing `if __name__ == "__main__": main()` launcher. Do not define new functions/classes in the facade.

- [ ] **Step 5: Run all focused skill integration tests**

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

- [ ] **Step 6: Verify core/frontend module boundaries**

```bash
python -m unittest tests.test_core_module_boundaries tests.test_discord_module_boundaries -v
```

Expected: PASS.

- [ ] **Step 7: Run the complete repository test suite**

```bash
python -m unittest discover -s tests -v
```

Expected: PASS with no item/stat/help/database/Qwen regressions.

- [ ] **Step 8: Run a deterministic local smoke check without Discord networking or model downloads**

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
- `magic: finale` -> `SkillDetailPayload` without semantic initialization;
- free-text queries -> `SkillResultsPayload` in lexical-only smoke mode.

The existing benchmark/retrieval tests and Task 2 runtime tests cover semantic behavior without forcing a model download on every final acceptance run.

- [ ] **Step 9: Commit final compatibility/isolation tests**

```bash
git add discord_bot.py tests/test_discord_module_boundaries.py tests/test_discord_skill_search.py
git commit -m "test: finalize skill bot integration"
```

---

## Acceptance Checklist

Before declaring the branch complete, verify all items against fresh test output:

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
- normal item/stat/help/database/Qwen behavior remains unchanged.
- `toram_skill_search` imports no Discord frontend code.
- full `python -m unittest discover -s tests -v` passes.
