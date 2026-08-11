# Core Search Modularization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the reverse dependency on `search_items.py`, make item-filter configuration single-source, and split reusable repository/ranking/parser/routing code into focused modules without changing supported search behavior.

**Architecture:** Keep the current deterministic-first search behavior and Qwen safety boundary. Refactor incrementally behind compatibility imports: `toram_data` owns database/domain/filter concerns, `toram_search` owns ranking/parsing/routing/orchestration, and top-level `search_items.py` remains a CLI/compatibility surface until a later CLI-only cleanup. Discord behavior is intentionally unchanged in this plan.

**Tech Stack:** Python 3, stdlib `dataclasses`/`unittest`/`ast`, SQLite, existing `rapidfuzz`, existing Discord.py frontend, existing Ollama/Qwen fallback.

## Global Constraints

- No database schema changes.
- No new third-party dependencies.
- Preserve the deterministic-first routing order and existing Qwen confirmation/validation boundary.
- Preserve exact item search, upgrade search, stat search, stat-expression search, reconstruction, clarification, and fuzzy-confirmation behavior.
- Preserve current aliases, including `consume -> Usable` and `dte -> % stronger against earth`.
- Keep `crit` intentionally ambiguous between Critical Rate and Critical Damage.
- Do not add boss, skill, build-role, plugin-domain, or generic abstraction layers in this refactor.
- Do not redesign Discord UI or interaction behavior in this refactor.
- Compatibility imports from `search_items.py` are allowed during the migration, but no module under `toram_data/` or `toram_search/` may import `search_items` when the plan is complete.
- Every task must end with a green focused test gate before the next task begins.
- The final repository suite must be fully green; the current structured-fallback logging mismatch is fixed first so later refactor failures cannot hide behind a known baseline failure.

---

## Target Module Map

### Create

- `toram_data/item_filters.py` — item-filter dataclasses, alias-backed filter catalog, exact filter extraction, trailing-filter extraction.
- `toram_data/models.py` — database/domain result dataclasses currently owned by `search_items.py`.
- `toram_data/repository.py` — SQLite `ItemRepository` and database query methods.
- `toram_search/ranking.py` — item-name fuzzy/exact ranking and result paging.
- `toram_search/parser.py` — parsed-search models plus deterministic query parsing and structured-request validation.
- `toram_search/routing.py` — deterministic route classification and direct help/database/refusal routing.
- `toram_search/fallback_adapter.py` — Toram-specific construction of `QwenFallbackService` from repository catalogs and validators.
- `tests/test_item_filters.py` — direct tests for the single item-filter source of truth.
- `tests/test_core_module_boundaries.py` — compatibility/export and dependency-direction regression tests.

### Modify

- `toram_search/fallback.py`
- `toram_data/stat_query.py`
- `toram_search/item_query_entities.py`
- `toram_search/reconstruction.py`
- `toram_search/service.py`
- `search_items.py`
- existing focused tests such as `tests/test_structured_fallback.py`, `tests/test_item_search_relevance.py`, `tests/test_query_reconstruction.py`, `tests/test_search_service.py`, and parser/stat-query tests.

### Explicitly defer

- Splitting `discord_bot.py` into config/session/render/view modules.
- Adding Discord session expiration/cleanup.
- Replacing `dict[str, Any]` nested item stats/sources/images with dedicated typed dataclasses.
- Moving the terminal UI out of `search_items.py` into a `toram_cli` package.

Those are separate follow-up refactors after this core dependency cleanup is complete and green.

---

### Task 1: Restore a Fully Green Baseline

**Files:**
- Modify: `toram_search/fallback.py`
- Modify: `tests/test_structured_fallback.py`

**Interfaces:**
- Consumes: existing `QwenFallbackService.interpret(current_input, history)`.
- Produces: unchanged `FallbackOutcome`; only the rejection reason for a search payload missing `candidates` is made consistent with the existing test contract.

- [ ] **Step 1: Add a regression that distinguishes missing candidates from extra fields**

Keep the existing missing-candidates assertion and add this test to `tests/test_structured_fallback.py`:

```python
def test_search_payload_with_candidates_and_extra_field_is_rejected_as_unexpected_fields(self):
    fallback = service(
        {
            "intent": "search",
            "candidates": [
                {
                    "stats": [{"name": "Critical Rate"}],
                    "item_filter": "bow",
                }
            ],
            "extra": True,
        }
    )

    with self.assertLogs("toram_search.fallback", level="DEBUG") as captured:
        outcome = fallback.interpret("cr bow", ())

    self.assertEqual(outcome.kind, "failed")
    self.assertIn("search payload has unexpected fields", "\n".join(captured.output))
```

- [ ] **Step 2: Run the two focused tests and verify the existing missing-candidates case is RED**

Run:

```bash
python -m unittest \
  tests.test_structured_fallback.StructuredFallbackTests.test_rejected_payload_is_logged_with_reason \
  tests.test_structured_fallback.StructuredFallbackTests.test_search_payload_with_candidates_and_extra_field_is_rejected_as_unexpected_fields \
  -v
```

Expected before the fix:
- `test_rejected_payload_is_logged_with_reason` fails because `{"intent": "search"}` currently logs `search payload has unexpected fields`.
- the new extra-field test passes or remains valid after the fix.

- [ ] **Step 3: Reorder search-payload validation without weakening strictness**

Change only the `intent == "search"` branch in `QwenFallbackService.interpret` so missing/invalid candidates are checked before the exact-key-set check:

```python
if intent == "search":
    candidates_obj = payload.get("candidates")
    if not isinstance(candidates_obj, list) or not 1 <= len(candidates_obj) <= 3:
        return self._failed("missing or invalid search candidates", payload)
    if set(payload) != {"intent", "candidates"}:
        return self._failed("search payload has unexpected fields", payload)

    valid: list[SearchIntentRequest] = []
    seen: set[SearchIntentRequest] = set()
    for candidate_obj in candidates_obj:
        request = self._search_candidate_from_payload(candidate_obj)
        if request is None or request in seen:
            continue
        seen.add(request)
        if self.validate_search_request(request):
            valid.append(request)
        else:
            logger.debug(
                "Qwen search candidate failed repository validation: %r",
                request,
            )
    if valid:
        return FallbackOutcome("search_requests", search_requests=tuple(valid))
    return self._failed("no search candidate passed validation", payload)
```

- [ ] **Step 4: Run the structured fallback suite**

```bash
python -m unittest tests.test_structured_fallback -v
```

Expected: `OK`.

- [ ] **Step 5: Establish the clean repository baseline**

```bash
python -m unittest discover -s tests -v
```

Expected: full suite `OK`. Do not start the refactor if another failure remains; diagnose it first.

- [ ] **Step 6: Commit**

```bash
git add toram_search/fallback.py tests/test_structured_fallback.py
git commit -m "fix: restore structured fallback rejection contract"
```

---

### Task 2: Create One Item-Filter Source of Truth

**Files:**
- Create: `toram_data/item_filters.py`
- Create: `tests/test_item_filters.py`
- Modify: `toram_data/stat_query.py`
- Modify: `search_items.py`
- Modify: `toram_search/item_query_entities.py`
- Modify: `toram_search/reconstruction.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class ItemTypeFilter:
    label: str
    item_types: tuple[str, ...]
    consumed_text: str

@dataclass(frozen=True)
class ItemFilterPhrase:
    phrase: str
    label: str
    item_types: tuple[str, ...]


def normalize_filter_text(value: str) -> str: ...

def list_item_filter_phrases(
    available_item_types: set[str],
) -> tuple[ItemFilterPhrase, ...]: ...

def extract_item_filter(
    text: str,
    available_item_types: set[str],
) -> tuple[ItemTypeFilter | None, str]: ...

def resolve_item_filter(
    text: str,
    available_item_types: set[str],
) -> ItemTypeFilter | None: ...

def extract_trailing_item_filter(
    text: str,
    available_item_types: set[str],
) -> tuple[ItemTypeFilter | None, str]: ...
```

- Compatibility: `toram_data.stat_query.ItemTypeFilter`, `toram_data.stat_query.ItemFilterPhrase`, and `toram_data.stat_query.list_item_filter_phrases` remain importable by re-exporting the imported names.

- [ ] **Step 1: Write direct filter-catalog tests**

Create `tests/test_item_filters.py`:

```python
import unittest

from toram_data.item_filters import (
    extract_item_filter,
    extract_trailing_item_filter,
    list_item_filter_phrases,
)


ITEM_TYPES = {
    "Bow",
    "Usable",
    "Weapon Crysta",
    "Enhancer Crysta (Red)",
    "Normal Crysta",
}


class ItemFilterTests(unittest.TestCase):
    def test_consume_alias_resolves_to_usable_without_consuming_stat(self):
        item_filter, remaining = extract_item_filter("consume dte", ITEM_TYPES)

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Usable")
        self.assertEqual(item_filter.item_types, ("Usable",))
        self.assertEqual(remaining, "dte")

    def test_weapon_xtal_keeps_combined_crysta_semantics(self):
        item_filter, remaining = extract_item_filter("cr weapon xtal", ITEM_TYPES)

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(
            item_filter.item_types,
            ("Weapon Crysta", "Enhancer Crysta (Red)"),
        )
        self.assertEqual(remaining, "cr")

    def test_trailing_filter_extracts_same_semantics(self):
        item_filter, remaining = extract_trailing_item_filter(
            "critical rate weapon xtal",
            ITEM_TYPES,
        )

        self.assertIsNotNone(item_filter)
        self.assertEqual(item_filter.label, "Weapon Crysta + Red Enhancer")
        self.assertEqual(remaining, "critical rate")

    def test_catalog_contains_alias_backed_usable_filter(self):
        rows = list_item_filter_phrases(ITEM_TYPES)
        pairs = {(row.phrase, row.label) for row in rows}

        self.assertIn(("consume", "Usable"), pairs)
```

- [ ] **Step 2: Run the new tests and verify RED**

```bash
python -m unittest tests.test_item_filters -v
```

Expected: import failure because `toram_data.item_filters` does not exist yet.

- [ ] **Step 3: Move the filter dataclasses and catalog into `toram_data/item_filters.py`**

Build the module from the existing implementations in `toram_data/stat_query.py` and `search_items.py` using this dependency direction:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from toram_data.aliases import (
    ALL_CRYSTA_TYPES,
    ITEM_TYPE_ALIASES,
    ITEM_WORD_ALIASES,
    MAIN_WEAPON_TYPES,
    normalize_name,
    normalize_stat_text,
)
```

Move the following logic into this file with no semantic changes:
- `ItemTypeFilter`
- `ItemFilterPhrase`
- filter text normalization
- available-type normalization
- configured filter phrase construction
- `list_item_filter_phrases`
- non-trailing filter extraction
- trailing filter extraction
- `resolve_item_filter`

The configured combined filters must include the current Weapon/Special/Armor/Additional crysta combinations, colored enhancers, `xtal`, `wp`/`weapon`, plus every `ITEM_TYPE_ALIASES` entry.

- [ ] **Step 4: Make `stat_query.py` consume the shared module**

Replace its local filter dataclasses/catalog helpers with imports:

```python
from toram_data.item_filters import (
    ItemFilterPhrase,
    ItemTypeFilter,
    extract_trailing_item_filter,
    list_item_filter_phrases,
)
```

Where the parser previously called its local `_extract_trailing_filter`, call `extract_trailing_item_filter` instead. Do not change expression grammar or ranking semantics.

- [ ] **Step 5: Make `search_items.py` consume the same filter module**

Import:

```python
from toram_data.item_filters import (
    ItemTypeFilter,
    extract_item_filter,
    list_item_filter_phrases,
    resolve_item_filter,
)
```

Delete the duplicate `_normalize_filter_text`, `_available_types_by_normalized`, `_existing_types`, `_find_phrase_tokens`, `_filter_candidates`, `extract_item_filter`, and `resolve_item_filter` implementations from `search_items.py` after all call sites use the shared functions.

For the temporary `_build_fallback_service` still living in `search_items.py`, build prompt filter labels from the shared catalog:

```python
filter_labels = tuple(
    f"{row.phrase} -> {row.label}"
    for row in list_item_filter_phrases(repository.list_item_types())
)
```

Deduplicate while preserving catalog order before passing to `QwenFallbackService`.

- [ ] **Step 6: Update query-understanding imports**

Change `toram_search/item_query_entities.py` and `toram_search/reconstruction.py` to import `ItemFilterPhrase` / `list_item_filter_phrases` from `toram_data.item_filters`, not `toram_data.stat_query`.

- [ ] **Step 7: Run focused filter/parser/reconstruction tests**

```bash
python -m unittest \
  tests.test_item_filters \
  tests.test_item_query_entities \
  tests.test_query_reconstruction \
  tests.test_item_query_understanding \
  -v
```

Expected: `OK`.

- [ ] **Step 8: Run the full suite**

```bash
python -m unittest discover -s tests -v
```

Expected: `OK`.

- [ ] **Step 9: Commit**

```bash
git add \
  toram_data/item_filters.py \
  toram_data/stat_query.py \
  toram_search/item_query_entities.py \
  toram_search/reconstruction.py \
  search_items.py \
  tests/test_item_filters.py
git commit -m "refactor: centralize item filter definitions"
```

---

### Task 3: Extract Domain Models and SQLite Repository

**Files:**
- Create: `toram_data/models.py`
- Create: `toram_data/repository.py`
- Modify: `search_items.py`
- Create/Modify: `tests/test_core_module_boundaries.py`

**Interfaces:**
- `toram_data.models` produces the existing dataclasses with the same field names/types:
  - `ItemSummary`
  - `ItemDetail`
  - `StatRow`
  - `RankedStatItem`
  - `ClauseMatch`
  - `RankedExpressionItem`
  - `UpgradeGraph`
- `toram_data.repository` produces `ItemRepository` with the same constructor and public methods currently defined in `search_items.py`.
- `search_items.py` re-exports these names for compatibility during the migration.

- [ ] **Step 1: Write compatibility tests before moving code**

Create `tests/test_core_module_boundaries.py` with the initial model/repository expectations:

```python
import unittest

import search_items
from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository


class CoreModuleBoundaryTests(unittest.TestCase):
    def test_search_items_reexports_domain_models(self):
        self.assertIs(search_items.ItemSummary, ItemSummary)
        self.assertIs(search_items.ItemDetail, ItemDetail)
        self.assertIs(search_items.StatRow, StatRow)
        self.assertIs(search_items.RankedStatItem, RankedStatItem)
        self.assertIs(search_items.ClauseMatch, ClauseMatch)
        self.assertIs(search_items.RankedExpressionItem, RankedExpressionItem)
        self.assertIs(search_items.UpgradeGraph, UpgradeGraph)

    def test_search_items_reexports_repository(self):
        self.assertIs(search_items.ItemRepository, ItemRepository)
```

- [ ] **Step 2: Run the compatibility tests and verify RED**

```bash
python -m unittest tests.test_core_module_boundaries -v
```

Expected: import failures because the new modules do not exist yet.

- [ ] **Step 3: Move database/domain dataclasses into `toram_data/models.py`**

Move the existing class definitions unchanged. Keep nested `stats`, `sources`, and `images` as their existing list/dict structures in this refactor; typed nested records are explicitly deferred.

The module imports should be limited to:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from toram_data.stat_query import ResolvedClause
```

Do not move parser-only models such as `ParsedSearch`, `StatResolution`, or `DeterministicRoute` here.

- [ ] **Step 4: Move `ItemRepository` into `toram_data/repository.py`**

Move the current `ItemRepository` implementation without SQL/query semantic changes. The repository module should import database/result models from `toram_data.models`, stat-expression types/helpers from `toram_data.stat_query`, and normalization helpers from `toram_data.aliases`.

Preserve these public methods exactly:

```python
class ItemRepository:
    def __init__(self, database_path: Path) -> None: ...
    def close(self) -> None: ...
    def list_items(self) -> list[ItemSummary]: ...
    def list_upgrade_items(self) -> list[ItemSummary]: ...
    def exact_upgrade_name_matches(self, query: str) -> list[ItemSummary]: ...
    def list_item_types(self) -> set[str]: ...
    def list_stat_names(self) -> list[str]: ...
    def count_items_total(self) -> int: ...
    def count_items_by_types(self, item_types: tuple[str, ...]) -> int: ...
    def count_items_with_stat(self, stat_name: str) -> int: ...
    def exact_name_matches(self, query: str) -> list[ItemSummary]: ...
    def get_item(self, item_id: int) -> ItemDetail: ...
    def get_upgrade_predecessors(self, item_id: int) -> list[ItemSummary]: ...
    def get_upgrade_successors(self, item_id: int) -> list[ItemSummary]: ...
    def get_upgrade_component(self, item_id: int) -> UpgradeGraph: ...
    def search_by_stat(
        self,
        stat_name: str,
        item_types: tuple[str, ...] | None,
    ) -> list[RankedStatItem]: ...
    def search_by_expression(
        self,
        expression: ResolvedStatExpression,
        item_types: tuple[str, ...] | None,
        *,
        primary_sort_ascending: bool = False,
    ) -> list[RankedExpressionItem]: ...
```

Private upgrade helpers move with the class.

- [ ] **Step 5: Replace definitions in `search_items.py` with compatibility imports**

At module import level, use:

```python
from toram_data.models import (
    ClauseMatch,
    ItemDetail,
    ItemSummary,
    RankedExpressionItem,
    RankedStatItem,
    StatRow,
    UpgradeGraph,
)
from toram_data.repository import ItemRepository
```

Delete the moved class definitions from `search_items.py`.

- [ ] **Step 6: Run compatibility plus service/data tests**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_search_service \
  tests.test_item_search_relevance \
  -v
```

Expected: `OK`.

- [ ] **Step 7: Run a real-database repository smoke test**

```bash
python - <<'PY'
from pathlib import Path
from toram_data.repository import ItemRepository

repo = ItemRepository(Path("coryn_data/database/items.sqlite"))
try:
    assert repo.list_items()
    assert "Usable" in repo.list_item_types()
    assert repo.list_stat_names()
finally:
    repo.close()
print("repository smoke: OK")
PY
```

Expected: `repository smoke: OK`.

- [ ] **Step 8: Run the full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_data/models.py toram_data/repository.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract item repository and domain models"
```

---

### Task 4: Extract Item-Name Ranking

**Files:**
- Create: `toram_search/ranking.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: `tests/test_item_search_relevance.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class RankedItem:
    item: ItemSummary
    score: float
    match_kind: str


def rank_items(query: str, items: Iterable[ItemSummary]) -> list[RankedItem]: ...

def page_results(
    results: list[RankedItem],
    *,
    page: int,
    page_size: int = PAGE_SIZE,
) -> list[RankedItem]: ...
```

- Keep constants `MIN_QUERY_LENGTH` and `ITEM_FUZZY_RELEVANCE_THRESHOLD` in the ranking module.
- Preserve the exact current scoring algorithm and threshold; this is a move, not a relevance redesign.

- [ ] **Step 1: Add direct-module compatibility assertions**

Extend `tests/test_core_module_boundaries.py`:

```python
from toram_search.ranking import RankedItem, rank_items


def test_search_items_reexports_ranking_symbols(self):
    self.assertIs(search_items.RankedItem, RankedItem)
    self.assertIs(search_items.rank_items, rank_items)
```

- [ ] **Step 2: Run the boundary test and verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_search_items_reexports_ranking_symbols -v
```

Expected: import failure for `toram_search.ranking`.

- [ ] **Step 3: Move ranking code into `toram_search/ranking.py`**

Move unchanged:
- `RankedItem`
- `_score_item`
- `_is_relevant_ranked_item`
- `rank_items`
- `page_results`

Use these imports:

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from rapidfuzz import fuzz

from toram_data.aliases import normalize_name
from toram_data.models import ItemSummary
```

- [ ] **Step 4: Re-export ranking from `search_items.py`**

```python
from toram_search.ranking import RankedItem, page_results, rank_items
```

Delete the moved definitions from `search_items.py`.

- [ ] **Step 5: Run all relevance regressions**

```bash
python -m unittest tests.test_item_search_relevance tests.test_core_module_boundaries -v
```

Expected: `OK`, including the existing threshold tests at 69.999 and 70.0.

- [ ] **Step 6: Run full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/ranking.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract item name ranking"
```

---

### Task 5: Extract Deterministic Query Parsing

**Files:**
- Create: `toram_search/parser.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: existing parser/stat-query/structured-intent tests.

**Interfaces:**
- Produces parser models:

```python
SearchIntent = Literal[
    "exact_item",
    "item_search",
    "stat_search",
    "stat_choices",
    "guided_stat",
    "stat_expression",
    "exact_upgrade",
    "upgrade_search",
]

@dataclass(frozen=True)
class StatResolution:
    stat_name: str
    matched_text: str
    confidence: float
    requires_confirmation: bool

@dataclass(frozen=True)
class ParsedSearch:
    intent: SearchIntent
    raw_query: str
    item_query: str | None = None
    item_id: int | None = None
    stat: StatResolution | None = None
    stat_choices: tuple[str, ...] = ()
    filter: ItemTypeFilter | None = None
    requires_confirmation: bool = False
    error: str | None = None
    parsed_expression: ParsedStatExpression | None = None
    resolved_expression: ResolvedStatExpression | None = None
    primary_sort_ascending: bool = False
```

- Produces public parsing/validation functions:

```python
def resolve_stat_choices(text: str, available_stats: list[str]) -> tuple[str, ...]: ...

def resolve_stat_name(
    text: str,
    available_stats: list[str],
    *,
    allow_fuzzy: bool = True,
) -> StatResolution | None: ...

def parse_expression_request(text: str, repository: ItemRepository) -> ParsedSearch: ...

def parse_structured_search_request(
    request: SearchIntentRequest,
    repository: ItemRepository,
    *,
    raw_query: str = "",
) -> ParsedSearch | None: ...

def format_structured_search_request(request: SearchIntentRequest) -> str: ...

def extract_natural_upgrade_target(text: str) -> str | None: ...

def parse_search_query(query: str, repository: ItemRepository) -> ParsedSearch: ...

def build_search_stat_terms(available_stats: list[str]) -> tuple[str, ...]: ...

def find_non_overlapping_stat_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]: ...
```

- The public constant `SEARCH_ONLY_STAT_ALIASES` moves with parser behavior.
- Keep terminal-only prompting functions in `search_items.py` for now.

- [ ] **Step 1: Add direct parser contract tests**

Extend `tests/test_core_module_boundaries.py` with compatibility assertions:

```python
from toram_search import parser as parser_module


def test_search_items_reexports_parser_symbols(self):
    self.assertIs(search_items.ParsedSearch, parser_module.ParsedSearch)
    self.assertIs(search_items.StatResolution, parser_module.StatResolution)
    self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
    self.assertIs(
        search_items.parse_structured_search_request,
        parser_module.parse_structured_search_request,
    )
```

Add a direct semantic test using a tiny repository fake already shaped like the service test repositories:

```python
class ParserRepository:
    def list_stat_names(self):
        return ["Critical Rate", "% stronger against earth"]

    def list_item_types(self):
        return {"Bow", "Usable"}

    def exact_name_matches(self, query):
        return []

    def exact_upgrade_name_matches(self, query):
        return []


def test_consume_dte_parses_as_stat_search_over_usable(self):
    parsed = parser_module.parse_search_query("consume dte", ParserRepository())

    self.assertIn(parsed.intent, {"stat_search", "stat_expression"})
    self.assertIsNotNone(parsed.filter)
    self.assertEqual(parsed.filter.label, "Usable")
```

- [ ] **Step 2: Run the new parser boundary tests and verify RED**

```bash
python -m unittest tests.test_core_module_boundaries -v
```

Expected: import failure for `toram_search.parser`.

- [ ] **Step 3: Move noninteractive parser models/helpers into `toram_search/parser.py`**

Move the current implementation from `search_items.py` without changing grammar. This includes:
- `SearchIntent`
- `StatResolution`
- `ParsedSearch`
- stat-choice/stat-name resolution
- negative bare-expression parsing
- expression parsing
- structured-request validation
- structured-request formatting (rename `_format_structured_search_request` to public `format_structured_search_request`)
- natural-upgrade target extraction
- bare `parse_search_query`
- stat-term discovery helpers used by routing
- database stat/filter validation helpers used by structured requests.

Use direct imports from `toram_data.aliases`, `toram_data.item_filters`, `toram_data.repository`, `toram_data.stat_query`, and `toram_search.fallback`. `parser.py` must not import `search_items`.

- [ ] **Step 4: Keep compatibility names in `search_items.py`**

Import/re-export the public parser symbols. For existing private callers/tests that still reference `_format_structured_search_request`, retain a temporary compatibility alias:

```python
from toram_search.parser import format_structured_search_request

_format_structured_search_request = format_structured_search_request
```

Do not duplicate implementation.

- [ ] **Step 5: Keep terminal interaction helpers local but make them call parser APIs**

Functions such as `resolve_expression_interactively`, `prompt_guided_stat`, and the terminal search loop remain in `search_items.py`; update their references so they consume the imported parser models/functions.

- [ ] **Step 6: Run focused parsing tests**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_structured_fallback \
  tests.test_query_reconstruction \
  tests.test_search_service \
  -v
```

Also run any existing stat-query/parser test modules found under `tests/` before committing.

- [ ] **Step 7: Run full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/parser.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract deterministic query parser"
```

---

### Task 6: Extract Deterministic Routing

**Files:**
- Create: `toram_search/routing.py`
- Modify: `search_items.py`
- Modify: `toram_search/service.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: `tests/test_search_service.py` and routing-focused regressions.

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class DeterministicRoute:
    kind: Literal["search", "help", "database", "fallback", "refuse"]
    parsed: ParsedSearch | None = None
    database_request: DatabaseActionRequest | None = None
    help_text: str | None = None
    record_failure: bool = False


def make_database_question_service(
    repository: ItemRepository,
) -> DatabaseQuestionService: ...


def route_deterministically(
    query: str,
    repository: ItemRepository,
    all_items: list[ItemSummary],
    help_service: HelpService,
    database_service: DatabaseQuestionService,
) -> DeterministicRoute: ...
```

- `route_deterministically` must retain its current precedence: parsed direct search -> simple ranking search -> direct help -> direct database question -> out-of-scope refusal -> assistant-question fallback -> failed-stat fallback -> ordinary item search.

- [ ] **Step 1: Add routing compatibility assertions**

Extend `tests/test_core_module_boundaries.py`:

```python
from toram_search.routing import DeterministicRoute, route_deterministically


def test_search_items_reexports_routing_symbols(self):
    self.assertIs(search_items.DeterministicRoute, DeterministicRoute)
    self.assertIs(search_items.route_deterministically, route_deterministically)
```

- [ ] **Step 2: Run the boundary test and verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_search_items_reexports_routing_symbols -v
```

Expected: import failure for `toram_search.routing`.

- [ ] **Step 3: Move routing-only logic into `toram_search/routing.py`**

Move unchanged:
- `DeterministicRoute`
- simple `highest`/`best` routing helper
- unknown-stat detection used by routing
- failed-stat query detection
- out-of-scope build concept detection
- assistant-question detection
- `make_database_question_service`
- `route_deterministically`

Use parser functions from `toram_search.parser` instead of local/top-level definitions.

- [ ] **Step 4: Re-export routing from `search_items.py`**

```python
from toram_search.routing import (
    DeterministicRoute,
    make_database_question_service,
    route_deterministically,
)
```

Delete the moved routing implementations from `search_items.py`.

- [ ] **Step 5: Update `SearchService` to import routing directly**

Replace only routing references in this task:

```python
from toram_search.routing import route_deterministically
```

`service.py` may still temporarily import `search_items as core` for other symbols until Task 7. Do not perform the full dependency cleanup early.

- [ ] **Step 6: Run service/routing regressions**

```bash
python -m unittest \
  tests.test_core_module_boundaries \
  tests.test_search_service \
  tests.test_item_query_understanding \
  tests.test_query_reconstruction \
  -v
```

Expected: `OK`.

- [ ] **Step 7: Run full suite and commit**

```bash
python -m unittest discover -s tests -v

git add toram_search/routing.py toram_search/service.py search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: extract deterministic search routing"
```

---

### Task 7: Extract Toram-Specific Fallback Construction and Decouple `SearchService`

**Files:**
- Create: `toram_search/fallback_adapter.py`
- Modify: `toram_search/service.py`
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Test: `tests/test_search_service.py`, `tests/test_structured_fallback.py`.

**Interfaces:**
- Produces:

```python
def build_fallback_service(
    repository: ItemRepository,
    database_service: DatabaseQuestionService,
    llm_client: object,
) -> QwenFallbackService: ...
```

- After this task, `toram_search/service.py` has zero imports from `search_items`.

- [ ] **Step 1: Add a dependency-direction test that is expected to fail before the refactor**

Extend `tests/test_core_module_boundaries.py`:

```python
import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_package_modules_do_not_import_top_level_search_items(self):
    offenders = []
    for package in ("toram_data", "toram_search"):
        for path in sorted((PROJECT_ROOT / package).glob("*.py")):
            if "search_items" in imported_module_names(path):
                offenders.append(str(path.relative_to(PROJECT_ROOT)))

    self.assertEqual(offenders, [])
```

- [ ] **Step 2: Run only the dependency test and verify RED**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_package_modules_do_not_import_top_level_search_items -v
```

Expected: FAIL listing at least `toram_search/service.py`.

- [ ] **Step 3: Create `toram_search/fallback_adapter.py`**

Move Toram-specific fallback construction out of `search_items.py`. Build catalogs from the actual repository:

```python
from toram_data.aliases import STAT_ALIASES, STAT_AMBIGUOUS_GROUPS
from toram_data.item_filters import list_item_filter_phrases
from toram_data.repository import ItemRepository
from toram_search.fallback import QwenFallbackService
from toram_search.parser import (
    SEARCH_ONLY_STAT_ALIASES,
    parse_structured_search_request,
)
```

Construct aliases with the same textual format currently sent to Qwen:

```python
aliases: list[str] = []
for alias, target in sorted(STAT_ALIASES.items()):
    aliases.append(f"{alias} -> {target}")
for alias, targets in sorted(STAT_AMBIGUOUS_GROUPS.items()):
    aliases.append(f"{alias} -> {' / '.join(targets)}")
for alias, target in sorted(SEARCH_ONLY_STAT_ALIASES.items()):
    aliases.append(f"{alias} -> {target}")
```

Build item filters from `list_item_filter_phrases(repository.list_item_types())`, deduplicating rendered `phrase -> label` strings while preserving order.

Pass validators exactly as today:

```python
return QwenFallbackService(
    llm_client,
    validate_search_request=lambda request: parse_structured_search_request(
        request,
        repository,
    ) is not None,
    validate_database_action=database_service.validate_request,
    stat_catalog=tuple(repository.list_stat_names()),
    alias_catalog=tuple(aliases),
    item_filter_catalog=tuple(filter_labels),
)
```

- [ ] **Step 4: Replace every `core.*` dependency in `toram_search/service.py` with direct module imports**

Import from the new owners:
- models/results from `toram_data.models`
- repository from `toram_data.repository`
- normalization/stat resolution from `toram_data.aliases`
- filter/crysta helpers from `toram_data.aliases` / `toram_data.item_filters`
- parsed search and structured parsing from `toram_search.parser`
- ranking from `toram_search.ranking`
- routing from `toram_search.routing`
- fallback construction from `toram_search.fallback_adapter`

Delete:

```python
import search_items as core
```

Keep `SearchService` orchestration behavior unchanged.

- [ ] **Step 5: Replace the CLI fallback builder with a compatibility alias**

In `search_items.py`:

```python
from toram_search.fallback_adapter import build_fallback_service

_build_fallback_service = build_fallback_service
```

Update terminal initialization calls to the new signature:

```python
fallback_service = build_fallback_service(
    repository,
    database_service,
    client,
)
```

Do not maintain a second implementation.

- [ ] **Step 6: Run the dependency-direction test again**

```bash
python -m unittest tests.test_core_module_boundaries.CoreModuleBoundaryTests.test_package_modules_do_not_import_top_level_search_items -v
```

Expected: `OK`.

- [ ] **Step 7: Run service/fallback/query-understanding tests**

```bash
python -m unittest \
  tests.test_search_service \
  tests.test_structured_fallback \
  tests.test_item_query_understanding \
  tests.test_query_reconstruction \
  tests.test_item_search_relevance \
  tests.test_core_module_boundaries \
  -v
```

Expected: `OK`.

- [ ] **Step 8: Compile the modularized core**

```bash
python -m py_compile \
  toram_data/aliases.py \
  toram_data/item_filters.py \
  toram_data/models.py \
  toram_data/repository.py \
  toram_data/stat_query.py \
  toram_search/fallback.py \
  toram_search/fallback_adapter.py \
  toram_search/item_query_entities.py \
  toram_search/parser.py \
  toram_search/ranking.py \
  toram_search/reconstruction.py \
  toram_search/routing.py \
  toram_search/service.py \
  toram_search/understanding.py \
  search_items.py \
  discord_bot.py
```

Expected: no output and exit code 0.

- [ ] **Step 9: Commit**

```bash
git add \
  toram_search/fallback_adapter.py \
  toram_search/service.py \
  search_items.py \
  tests/test_core_module_boundaries.py
git commit -m "refactor: decouple search service from cli module"
```

---

### Task 8: Final Compatibility Cleanup and End-to-End Verification

**Files:**
- Modify: `search_items.py`
- Modify: `tests/test_core_module_boundaries.py`
- Modify only other files required to remove now-dead duplicate imports/definitions.

**Interfaces:**
- `search_items.py` remains a supported CLI entry point and compatibility import surface.
- `toram_data/*` and `toram_search/*` contain the reusable implementation.
- Discord is still allowed to import `search_items as core` during this plan because it is a frontend entry point; removing that dependency belongs to the later Discord modularization plan.

- [ ] **Step 1: Add final single-source assertions**

Extend `tests/test_core_module_boundaries.py`:

```python
from toram_data import item_filters
from toram_search import parser as parser_module
from toram_search import ranking as ranking_module
from toram_search import routing as routing_module


def test_search_items_compatibility_exports_point_to_canonical_modules(self):
    self.assertIs(search_items.extract_item_filter, item_filters.extract_item_filter)
    self.assertIs(search_items.resolve_item_filter, item_filters.resolve_item_filter)
    self.assertIs(search_items.ItemRepository, ItemRepository)
    self.assertIs(search_items.rank_items, ranking_module.rank_items)
    self.assertIs(search_items.parse_search_query, parser_module.parse_search_query)
    self.assertIs(search_items.route_deterministically, routing_module.route_deterministically)
```

- [ ] **Step 2: Delete dead duplicate definitions/imports from `search_items.py`**

Keep only code still owned by the terminal frontend or temporary compatibility surface. In particular, there should be no second implementation of:
- item-filter catalog/extraction
- domain database models
- `ItemRepository`
- item-name ranking
- deterministic parser
- deterministic router
- fallback-service factory.

Do not move terminal rendering/screens in this task; avoiding a second frontend refactor is intentional.

- [ ] **Step 3: Run the exact user-regression smoke cases against the checked-in database**

```bash
python - <<'PY'
from pathlib import Path

from toram_data.repository import ItemRepository
from toram_search.service import SearchService
from toram_search.session import FailedQueryContext


class MustNotCallLLM:
    def complete(self, *args, **kwargs):
        raise AssertionError("deterministic query unexpectedly called Qwen")


repo = ItemRepository(Path("coryn_data/database/items.sqlite"))
try:
    service = SearchService(repo, llm_client=MustNotCallLLM())

    consume = service.handle_query("consume dte", FailedQueryContext(max_entries=3))
    assert consume.kind in {"search", "item_understanding"}, consume

    reconstructed = service.handle_query(
        "xtall cr weapon",
        FailedQueryContext(max_entries=3),
    )
    assert reconstructed.kind == "search", reconstructed

    ambiguous = service.handle_query(
        "crit xtal weapon",
        FailedQueryContext(max_entries=3),
    )
    assert ambiguous.kind in {"search", "item_understanding"}, ambiguous

    exact_name = repo.list_items()[0].name
    exact = service.handle_query(exact_name, FailedQueryContext(max_entries=3))
    assert exact.kind == "search", exact
finally:
    repo.close()

print("core modularization smoke: OK")
PY
```

Expected: `core modularization smoke: OK`, with no Qwen call on deterministic cases.

- [ ] **Step 4: Run the complete test suite fresh**

```bash
python -m unittest discover -s tests -v
```

Expected: full suite `OK` with zero known baseline failures.

- [ ] **Step 5: Run compilation again**

```bash
python -m compileall -q toram_data toram_search search_items.py discord_bot.py
```

Expected: exit code 0.

- [ ] **Step 6: Inspect the dependency direction mechanically**

```bash
python - <<'PY'
import ast
from pathlib import Path

root = Path(".")
offenders = []
for package in ("toram_data", "toram_search"):
    for path in sorted((root / package).glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name == "search_items" for alias in node.names):
                    offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom) and node.module == "search_items":
                offenders.append(str(path))

assert not offenders, offenders
print("dependency direction: OK")
PY
```

Expected: `dependency direction: OK`.

- [ ] **Step 7: Review final diff for refactor-only scope**

The final implementation diff should show:
- one canonical filter module instead of duplicate catalogs,
- reusable models/repository/ranking/parser/routing modules,
- `SearchService` importing those modules directly,
- `search_items.py` retaining frontend/compatibility code,
- no Discord UX changes,
- no DB/schema changes,
- no boss/skill abstractions.

If behavior-changing features appear in the diff, remove them or split them into a separate PR before integration.

- [ ] **Step 8: Commit final cleanup**

```bash
git add search_items.py tests/test_core_module_boundaries.py
git commit -m "refactor: finalize core search module boundaries"
```

---

## Completion Criteria

The refactor is complete only when all of the following are true:

1. `python -m unittest discover -s tests -v` is fully green.
2. `toram_data/` and `toram_search/` contain no import of top-level `search_items`.
3. There is only one item-filter catalog/extraction implementation.
4. `SearchService` depends directly on package modules, not CLI code.
5. `search_items.py` still runs as the existing terminal entry point.
6. Existing Discord behavior remains unchanged.
7. `consume dte`, `xtall cr weapon`, `crit xtal weapon`, exact item-name search, upgrade search, and structured Qwen confirmation remain covered by tests/smoke checks.
8. No new dependency, DB migration, or cross-domain abstraction has been introduced.

## Follow-Up Plans After This One

Do not bundle these into the same implementation PR:

1. **Discord lifecycle modularization:** split config/session/render/views/runtime and add bounded session expiration/cleanup.
2. **Typed item-detail records:** replace nested stat/source/image dictionaries with explicit dataclasses after the core imports are stable.
3. **CLI extraction:** move terminal rendering/screens into a `toram_cli` package if the CLI remains an actively maintained frontend.
